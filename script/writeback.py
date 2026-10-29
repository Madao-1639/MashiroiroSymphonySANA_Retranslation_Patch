"""Stage 4: write the CN fan translation back into the Steam scripts.

Sub-commands (run from the project root, GalRev env):

    mamba run -n GalRev python script/writeback.py fetch        # decrypt the 简中 domain scripts (data.xp3 + patch.xp3)
    mamba run -n GalRev python script/writeback.py roundtrip    # gate: zero-change rebuild + grow/shrink
    mamba run -n GalRev python script/writeback.py apply        # apply the CN text -> tmp/out_zhcn
    mamba run -n GalRev python script/pack_patch.py             # -> releases/CN.xp3 (Hxv4, self-verifying)

The `pack` / `verify` sub-commands below are FHD-era leftovers: they build a *plain* xp3,
which is not the delivery format here (delivery = Hxv4 `CN.xp3` from script/pack_patch.py).

Design
------
* Only the string pool and two inline fields of each page are touched: the text reference
  (`STRING_Nx` index) and the `length` integer. The object graph, name trie and resource
  chunks stay byte-identical, which is why no full object-graph serialiser is needed --
  both fields are patched **in place** and only when the new value fits the old byte width.
* Text mapping is purely positional: `chs (script, scene, row, page) -> steam (same)`.
  This game's two versions are structurally identical (script/align_check.py), so no
  reordering / offset handling is needed; the 16 CN-only scripts are simply skipped.
* Rows marked `source_untranslated` (the CN patch is still Japanese there) keep the official text.
* The official scripts deduplicate identical strings, so one slot can back several pages with
  different CN translations. Those pages get appended string slots and their text reference is
  re-pointed in place (same inline width); if a reference cannot hold the new index the page
  keeps the majority text and the case is reported.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tool import hxv4, psb, scn, textset, xp3  # noqa: E402

NL = chr(10)
PROFILE = os.path.join(ROOT, "resource", "hxv4", "profile_live.json")
GAME = "E:/SteamLibrary/steamapps/common/MashiroiroSymphonySANA"
# 本作没有 data_fhd.xp3；顺序不能反：patch.xp3 覆盖 data.xp3（完整版靠它）
ARCHIVES = [os.path.join(GAME, n) for n in ("data.xp3", "patch.xp3")]
DOMAIN_ZHCN = "9d31bc7aa367a8f1"
SALT = "xp3hnp"
FETCH_DIR = os.path.join(ROOT, "tmp", "zhcn_psb")
FETCH_MANIFEST = os.path.join(FETCH_DIR, "_manifest.json")
CHS_DIR = os.path.join(ROOT, "resource", "chs_text")
ZHCN_DIR = os.path.join(ROOT, "resource", "steam_text_zhcn")
MATCH_DIR = os.path.join(ROOT, "resource", "match")
REPORT = os.path.join(ROOT, "tmp", "_report_apply.json")


def log(msg):
    print(msg, flush=True)


def read_jsonl(path):
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                yield json.loads(line)


def name_hash(name: str) -> str:
    """Hxv4 `file_hash` of an archive entry name (BLAKE2s-256 over UTF-16LE name + salt)."""
    return hashlib.blake2s(name.encode("utf-16-le") + SALT.encode("utf-16-le")).hexdigest()


def load_manifest():
    if not os.path.exists(FETCH_MANIFEST):
        raise SystemExit("run `fetch` first")
    with open(FETCH_MANIFEST, encoding="utf-8") as fp:
        return json.load(fp)["scripts"]


DATA = {}


def load_text_maps():
    """Load the per-page facts once: CN text, Steam page record, pair status."""
    if DATA:
        return DATA
    chs = {}
    for rec in textset.iter_records(CHS_DIR, textset.MESSAGES):
        for p in rec["pages"]:
            chs[(rec["script"], rec["scene"], rec["row"], p["page"])] = p["text"]
    steam = {}
    for rec in textset.iter_records(ZHCN_DIR, textset.MESSAGES):
        for p in rec["pages"]:
            steam[(rec["script"], rec["scene"], rec["row"], p["page"])] = p
    status = {}
    for rec in textset.iter_records(MATCH_DIR, textset.PAIRS):
        status[(rec["script"], rec["scene"], rec["row"], rec["page"])] = rec["status"]
    chs_sel, steam_sel = {}, {}
    for rec in textset.iter_records(CHS_DIR, textset.SELECTS):
        chs_sel[(rec["script"], rec["scene"], rec["index"])] = rec["text"]
    for rec in textset.iter_records(ZHCN_DIR, textset.SELECTS):
        steam_sel[(rec["script"], rec["scene"], rec["index"])] = rec
    DATA.update(chs=chs, steam=steam, status=status, chs_sel=chs_sel, steam_sel=steam_sel)
    return DATA


# --------------------------------------------------------------------------- slots

def load_script_maps(script):
    """只加载单个脚本的三方数据（分片文件 → 每个 worker 的工作量与脚本大小成正比）。"""
    chs, steam, status = {}, {}, {}
    chs_sel, steam_sel = {}, {}
    for rec in textset.records_for(CHS_DIR, script, textset.MESSAGES):
        for p in rec["pages"]:
            chs[(rec["script"], rec["scene"], rec["row"], p["page"])] = p["text"]
    for rec in textset.records_for(ZHCN_DIR, script, textset.MESSAGES):
        for p in rec["pages"]:
            steam[(rec["script"], rec["scene"], rec["row"], p["page"])] = p
    for rec in textset.records_for(MATCH_DIR, script, textset.PAIRS):
        status[(rec["script"], rec["scene"], rec["row"], rec["page"])] = rec["status"]
    for rec in textset.records_for(CHS_DIR, script, textset.SELECTS):
        chs_sel[(rec["script"], rec["scene"], rec["index"])] = rec["text"]
    for rec in textset.records_for(ZHCN_DIR, script, textset.SELECTS):
        steam_sel[(rec["script"], rec["scene"], rec["index"])] = rec
    return {"chs": chs, "steam": steam, "status": status,
            "chs_sel": chs_sel, "steam_sel": steam_sel}


def _page_paths(pf, refs):
    """Group recorded slots by the trailing index of `$.scenes[i].texts[j][1][k][<tail>]`."""
    per_scene = collections.defaultdict(dict)
    for path, pos, width, value in refs:
        if not path.startswith("$.scenes["):
            continue
        tail = path[len("$.scenes["):]
        try:
            scene_i, rest = tail.split("]", 1)
            if not rest.startswith(".texts["):
                continue
            row_s, rest2 = rest[len(".texts["):].split("]", 1)
            if not rest2.startswith("[1]["):
                continue
            page_s, rest3 = rest2[len("[1]["):].split("]", 1)
            per_scene[int(scene_i)][(int(row_s), int(page_s), rest3)] = (pos, width, value)
        except ValueError:
            continue
    labels = {}
    for i, scene in enumerate(pf.root["scenes"]):
        lbl = scene.get("label")
        labels[i] = lbl.value if isinstance(lbl, psb.PsbString) else None
    out = collections.defaultdict(dict)
    for scene_i, entries in per_scene.items():
        for (row, page, tail), slot in entries.items():
            out[tail][(labels.get(scene_i), row, page)] = slot
    return out


def all_slots(pf):
    """(ints, strs) for one PsbFile, recorded in a single graph walk and cached on the object."""
    cached = getattr(pf, "_slot_cache", None)
    if cached is None:
        cached = pf.record_slots("$")
        pf._slot_cache = cached
    return cached


def select_text_slots(pf):
    """(scene_label, select_index) -> (pos, width, string_index) for `selects[j].text`."""
    labels = {}
    for i, scene in enumerate(pf.root["scenes"]):
        lbl = scene.get("label")
        labels[i] = lbl.value if isinstance(lbl, psb.PsbString) else None
    out = {}
    for path, pos, width, idx in all_slots(pf)[1]:
        if not path.startswith("$.scenes") or ".selects[" not in path                 or not path.endswith("].text"):
            continue
        try:
            scene_i = int(path[len("$.scenes["):].split("]", 1)[0])
            sel_i = int(path.split(".selects[", 1)[1].split("]", 1)[0])
        except ValueError:
            continue
        out[(labels.get(scene_i), sel_i)] = (pos, width, idx)
    return out


def page_slots(pf):
    """{'length': {(scene,row,page): (pos,width,value)}, 'text': same} for one script."""
    ints_raw, strs_raw = all_slots(pf)
    ints = _page_paths(pf, [s for s in ints_raw if s[0].startswith("$.scenes")])
    refs = _page_paths(pf, [s for s in strs_raw if s[0].startswith("$.scenes")])
    return {"length": dict(ints.get("[2]", {})), "text": dict(refs.get("[1]", {}))}


# --------------------------------------------------------------------------- fetch

def _fetch_chunk(task):
    """worker：处理 (归档路径, 条目下标列表)，返回 {脚本名: 元数据}。"""
    path, indices = task
    profile = hxv4.Hxv4Profile.load_json(PROFILE)
    arc = hxv4.Hxv4Archive(path, profile)
    got = {}
    for idx in indices:
        entry = arc.entries[idx]
        rec = entry.record
        if rec is None or rec.domain_hash.hex() != DOMAIN_ZHCN:
            continue
        data = arc.read_entry(entry)
        if data[:4] not in (psb.PSB_SIG, psb.MDF_SIG):   # this domain mixes both shells
            continue
        sf = scn.ScnFile(data)
        got[sf.script_name] = {"container": os.path.basename(path),
                               "file_hash": rec.file_hash.hex(),
                               "shell": "mdf" if data[:3] == b"mdf" else "psb",
                               "bytes": len(data)}
        with open(os.path.join(FETCH_DIR, sf.script_name + ".scn"), "wb") as fp:
            fp.write(data)
    return got


def cmd_fetch(args):
    profile = hxv4.Hxv4Profile.load_json(PROFILE)
    os.makedirs(FETCH_DIR, exist_ok=True)
    jobs = max(1, min(args.jobs, os.cpu_count() or 1))
    found = {}
    for path in ARCHIVES:  # ascending priority: the later archive wins
        arc = hxv4.Hxv4Archive(path, profile)
        todo = [e.index for e in arc.entries
                if e.record is not None and e.record.domain_hash.hex() == DOMAIN_ZHCN]
        arc.close()
        if jobs == 1 or len(todo) < 8:
            chunks = [(path, todo)]
        else:
            step = max(1, len(todo) // (jobs * 4))
            chunks = [(path, todo[i:i + step]) for i in range(0, len(todo), step)]
        kept = 0
        if len(chunks) == 1:
            results = [_fetch_chunk(chunks[0])]
        else:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                results = list(pool.map(_fetch_chunk, chunks))
        for got in results:            # 合并顺序 = 归档优先级顺序
            found.update(got)
            kept += len(got)
        log(f"{os.path.basename(path)}: {kept} script(s) in domain {DOMAIN_ZHCN}")
        del arc
    with open(FETCH_MANIFEST, "w", encoding="utf-8", newline=NL) as fp:
        json.dump({"domain": DOMAIN_ZHCN, "scripts": found}, fp,
                  ensure_ascii=False, indent=1, sort_keys=True)
    log(f"fetched {len(found)} script(s) -> {os.path.relpath(FETCH_DIR, ROOT)}")


# ----------------------------------------------------------------------- roundtrip

def cmd_roundtrip(args):
    """Gate: a rebuild without edits must reproduce the body byte for byte."""
    manifest = load_manifest()
    d = load_text_maps()
    steam = d["steam"]
    bad = []
    checked = 0
    for script in sorted(manifest):
        data = open(os.path.join(FETCH_DIR, script + ".scn"), "rb").read()
        pf = psb.PsbFile(data)
        checked += 1
        if psb.PsbFile(pf.rebuild({})).body != pf.body:
            bad.append((script, "psb.rebuild({}) != original body"))
            continue
        # the slot mapper must agree with the extractor about every page's `length`
        slots = page_slots(pf)["length"]
        page_len = {(sc, r, p): v["len"] for (s_, sc, r, p), v in steam.items() if s_ == script}
        missing = set(page_len) - set(slots)
        if missing:
            bad.append((script, f"{len(missing)} page(s) without a length slot"))
            continue
        wrong = [(k, page_len[k], slots[k][2]) for k in page_len if slots[k][2] != page_len[k]]
        if wrong:
            bad.append((script, f"{len(wrong)} length slot(s) disagree with the extractor, "
                                f"e.g. {wrong[0]}"))
            continue
        # a zero-change rebuild cannot see offset bugs, so grow and shrink one string and
        # require the file to parse back with exactly the expected pool
        same = {i: pf.string_value(i) for i in range(len(pf.string_offsets))}
        if same:
            for tag, delta in (("grow", "▩" * 40), ("shrink", "")):
                edited = dict(same)
                edited[0] = same[0] + delta
                try:
                    pf2 = psb.PsbFile(pf.rebuild(edited))
                except Exception as exc:  # noqa: BLE001
                    bad.append((script, f"{tag} test cannot re-parse: {type(exc).__name__}: {exc}"))
                    break
                if pf2.string_value(0) != edited[0] or any(
                        pf2.string_value(i) != same[i] for i in same if i != 0):
                    bad.append((script, f"{tag} test corrupted the string pool"))
                    break
    log(f"round-trip: {checked - len(bad)}/{checked} script(s) byte-identical "
        f"(body + length-slot alignment + grow/shrink re-parse)")
    for script, why in bad[:10]:
        log(f"  FAIL {script}: {why}")
    if bad:
        raise SystemExit(f"round-trip gate failed on {len(bad)} script(s)")

    chs_ok = chs_bad = 0
    for archive in ("mashiro_sana_chs.xp3", "mashiro_sana_cn2.xp3"):
        path = os.path.join(ROOT, "..", "retranslation_patch_original", archive)
        if not os.path.exists(path):
            continue
        fp_ = open(path, "rb")
        import mmap
        mm = mmap.mmap(fp_.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            for entry in xp3.parse_xp3(mm, show_log=False):
                if not entry.name or not entry.name.lower().endswith(".scn"):
                    continue
                pf = psb.PsbFile(bytes(xp3.extract_entry(mm, entry)))
                if psb.PsbFile(pf.rebuild({})).body == pf.body:
                    chs_ok += 1
                else:
                    chs_bad += 1
        finally:
            mm.close()
            fp_.close()
    log(f"round-trip (CN patch, mdf shell): {chs_ok} ok, {chs_bad} bad")
    if chs_bad:
        raise SystemExit("round-trip gate failed on the CN patch side")


# --------------------------------------------------------------------------- apply

APPLY_OUT = os.path.join(ROOT, "tmp", "out_zhcn")


def _apply_init(outdir):
    global APPLY_OUT
    APPLY_OUT = outdir          # 子进程要自己拿到 --out（此前只创建目录，子进程会写默认目录）
    os.makedirs(outdir, exist_ok=True)


def _apply_one(script):
    """Apply the CN text to one script; returns (script, per-script stats)."""
    d = load_script_maps(script)
    chs, steam, status = d["chs"], d["steam"], d["status"]
    chs_sel, steam_sel = d["chs_sel"], d["steam_sel"]
    outdir = APPLY_OUT
    stats = collections.Counter()
    len_skipped = []
    ref_skipped = []
    shared_conflicts = []
    data = open(os.path.join(FETCH_DIR, script + ".scn"), "rb").read()
    pf = psb.PsbFile(data)
    slots = page_slots(pf)
    len_slots, text_slots = slots["length"], slots["text"]

    expect = {}
    for key_page in len_slots:
        key = (script,) + key_page
        st = status.get(key)
        if steam.get(key) is None or st is None:
            stats["page_without_pair"] += 1
            continue
        if st == "source_untranslated":
            stats["page_kept_official"] += 1
            continue
        text = chs.get(key)
        if text is None:
            stats["page_missing_cn"] += 1
            continue
        expect[key_page] = text

    # Pages can share one string slot (the official scripts dedup identical strings) and
    # even one *page list object*, which makes two rows literally inseparable: one reference
    # position then serves several pages. Decide the target text per reference position,
    # then hand out slots -- reuse a slot only when every consumer of it agrees, otherwise
    # append a fresh slot and re-point the reference in place.
    by_pos = collections.defaultdict(list)          # pos -> [(page, width, current idx)]
    for key_page, (pos, width, idx) in text_slots.items():
        by_pos[pos].append((key_page, width, idx))
    official = {idx: steam[(script,) + page]["text"]
                for page, (_, _, idx) in text_slots.items()}
    # count *distinct byte positions* per slot: the object graph is a DAG, so a shared
    # page list shows up as several visits of the very same reference
    idx_positions = collections.defaultdict(set)
    for _path, pos, _width, idx in all_slots(pf)[1]:
        idx_positions[idx].add(pos)
    consumers = {idx: len(positions) for idx, positions in idx_positions.items()}
    target = {}
    for pos, refs in by_pos.items():
        votes = collections.Counter()
        for page, width, idx in refs:
            want = expect.get(page)
            votes[want if want is not None else official[idx]] += 1
        if len(votes) > 1:
            stats["page_ref_shared"] += 1
            winner_text = votes.most_common(1)[0][0]
            for page, width, idx in refs:
                want = expect.get(page)
                if want is not None and want != winner_text:
                    shared_conflicts.append({"script": script, "scene": page[0],
                                             "row": page[1], "page": page[2],
                                             "wanted": want, "written": winner_text})
                    stats["page_text_unreachable"] += 1
        else:
            winner_text = next(iter(votes))
        target[pos] = winner_text

    # slot assignment: keep an existing slot when all of its consumers want the same text
    new_strings = {}
    extra_strings = []
    text_to_slot = {}
    for idx in set(official):
        wanted = {target[pos] for pos, refs in by_pos.items() if refs[0][2] == idx}
        pos_count = sum(1 for pos, refs in by_pos.items() if refs[0][2] == idx)
        unknown = consumers.get(idx, 0) - pos_count
        if len(wanted) == 1 and unknown == 0:
            want = next(iter(wanted))
            if want != official[idx]:
                new_strings[idx] = want
                stats["slot_rewritten"] += 1
            text_to_slot.setdefault(want, idx)
    ref_edits = []
    for pos, refs in by_pos.items():
        want = target[pos]
        got = text_to_slot.get(want)
        if got is None:
            got = len(pf.string_offsets) + len(extra_strings)
            extra_strings.append(want)
            text_to_slot[want] = got
            stats["slot_appended"] += 1
        cur = refs[0][2]
        if got == cur:
            continue
        width = refs[0][1]
        if width and got < (1 << (8 * width)):
            ref_edits.append((pos, width, got, "str"))
            stats["ref_repointed"] += 1
        else:
            page = refs[0][0]
            ref_skipped.append({"script": script, "scene": page[0], "row": page[1],
                                "page": page[2], "width": width, "new_index": got})
            stats["ref_width_too_small"] += 1

    # choice labels (`scenes[].selects[].text`) -- one reference each, no sharing
    sel_edits = 0
    for key, (pos, width, idx) in select_text_slots(pf).items():
        want = chs_sel.get((script,) + key)
        if want is None:
            continue
        if want == steam_sel[(script,) + key]["text"]:
            continue
        if consumers.get(idx, 0) <= 1:
            new_strings[idx] = want
        else:
            got = text_to_slot.get(want)
            if got is None:
                got = len(pf.string_offsets) + len(extra_strings)
                extra_strings.append(want)
                text_to_slot[want] = got
                stats["slot_appended"] += 1
            if width and got < (1 << (8 * width)):
                ref_edits.append((pos, width, got, "str"))
            else:
                ref_skipped.append({"script": script, "scene": key[0], "row": -1,
                                    "page": key[1], "width": width, "new_index": got})
                stats["ref_width_too_small"] += 1
        sel_edits += 1
    stats["select_written"] += sel_edits

    edits = list(ref_edits)
    len_by_pos = collections.defaultdict(list)
    for key_page, ref in len_slots.items():
        len_by_pos[ref[0]].append((key_page, ref))
    for pos, entries in len_by_pos.items():
        # a shared page list also shares its `length` slot: use the page that won the text
        chosen = None
        for key_page, ref in entries:
            if key_page in expect and expect[key_page] == target.get(
                    text_slots[key_page][0]):
                chosen = (key_page, ref)
                break
        if chosen is None:
            chosen = next(((k, r) for k, r in entries if k in expect), None)
        if chosen is None:
            continue
        key_page, (pos, width, old_len) = chosen
        want = expect[key_page]
        new_len = scn.page_length(want)
        if new_len == old_len:
            stats["len_already_ok"] += 1
        elif width and new_len < (1 << (8 * width)):
            edits.append((pos, width, new_len))
            stats["len_patched"] += 1
        else:
            len_skipped.append({"script": script, "scene": key_page[0], "row": key_page[1],
                                "page": key_page[2], "width": width,
                                "old": old_len, "new": new_len})
            stats["len_width_too_small"] += 1

    body = pf.patch_ints(edits) if edits else pf.body
    new_body = pf.rebuild(new_strings, extra_strings, body=body)
    if pf.is_mdf:      # keep the container form the archive shipped
        raw = psb.PsbFile(new_body).body
        new_body = psb.MDF_SIG + struct.pack("<I", len(raw)) + zlib.compress(raw, 9)
    with open(os.path.join(outdir, script + ".scn"), "wb") as fp:
        fp.write(new_body)
    per_script = {script: {"pages": len(len_slots),
                           "slots_rewritten": len(new_strings),
                           "slots_appended": len(extra_strings),
                           "refs_repointed": len(ref_edits)}}
    return script, dict(stats), per_script, len_skipped, ref_skipped, shared_conflicts


def cmd_apply(args):
    global APPLY_OUT
    manifest = load_manifest()
    APPLY_OUT = args.out
    os.makedirs(APPLY_OUT, exist_ok=True)
    scripts = sorted(manifest)
    jobs = max(1, min(args.jobs, len(scripts)))

    results = []
    if jobs == 1:
        _apply_init(APPLY_OUT)
        for script in scripts:
            results.append(_apply_one(script))
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=jobs, initializer=_apply_init,
                                 initargs=(APPLY_OUT,)) as pool:
            for item in pool.map(_apply_one, scripts, chunksize=1):
                results.append(item)

    stats = collections.Counter()
    per_script = {}
    len_skipped, ref_skipped, shared_conflicts = [], [], []
    for script, st, ps, ls, rs, sc in results:
        stats.update(st)
        per_script.update(ps)
        len_skipped.extend(ls)
        ref_skipped.extend(rs)
        shared_conflicts.extend(sc)

    with open(REPORT, "w", encoding="utf-8", newline=NL) as fp:
        json.dump({"scripts": per_script, "totals": dict(stats),
                   "len_width_skipped": len_skipped, "ref_width_skipped": ref_skipped,
                   "page_list_shared_conflicts": shared_conflicts},
                  fp, ensure_ascii=False, indent=1, sort_keys=True)
    log(f"apply totals ({jobs} job(s)): " + json.dumps(dict(stats), ensure_ascii=False))
    log(f"wrote {len(per_script)} script(s) -> {os.path.relpath(args.out, ROOT)}")
    for tag, lst in (("length", len_skipped), ("text reference", ref_skipped)):
        if lst:
            log(f"WARNING {len(lst)} page(s) could not patch the {tag} in place "
                f"(see {os.path.relpath(REPORT, ROOT)})")


# ---------------------------------------------------------------------------- pack

def cmd_pack(args):
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    xp3.pack_xp3(args.src, args.out, compress="index", show_log=False)
    log(f"packed {os.path.relpath(args.out, ROOT)} "
        f"({os.path.getsize(args.out)} bytes) from {os.path.relpath(args.src, ROOT)}")


# -------------------------------------------------------------------------- verify

def cmd_verify(args):
    manifest = load_manifest()
    d = load_text_maps()
    chs, status = d["chs"], d["status"]
    chs_sel, steam_sel = d["chs_sel"], d["steam_sel"]

    fp_ = open(args.xp3, "rb")
    import mmap
    mm = mmap.mmap(fp_.fileno(), 0, access=mmap.ACCESS_READ)
    entries = {}
    try:
        for entry in xp3.parse_xp3(mm, show_log=False):
            if entry.name:
                entries[entry.name] = bytes(xp3.extract_entry(mm, entry))
    finally:
        mm.close()
        fp_.close()

    known = set()
    if os.path.exists(REPORT):
        with open(REPORT, encoding="utf-8") as fp:
            rep = json.load(fp)
        for rec in rep.get("ref_width_skipped", []):
            known.add((rec["script"], rec["scene"], rec["row"], rec["page"]))
        for rec in rep.get("page_list_shared_conflicts", []):
            known.add((rec["script"], rec["scene"], rec["row"], rec["page"]))
        log(f"known documented deviations: {len(known)} page(s) "
            f"(shared page-list object / inline index too small)")

    problems = []
    scripts_ok = 0
    pages_ok = 0
    selects_ok = 0
    known_skipped = 0
    for name, data in sorted(entries.items()):
        script = name[:-4] if name.endswith(".scn") else name
        if script not in manifest:
            problems.append(f"{name}: not a fetched script")
            continue
        if name_hash(name) != manifest[script]["file_hash"]:
            problems.append(f"{name}: name hash does not match the Steam 简中 entry")
            continue
        new = scn.ScnFile(data)
        with open(os.path.join(FETCH_DIR, script + ".scn"), "rb") as fp:
            old = scn.ScnFile(fp.read())
        old_msgs, new_msgs = list(old.iter_messages()), list(new.iter_messages())
        if len(old_msgs) != len(new_msgs):
            problems.append(f"{name}: row count {len(old_msgs)} -> {len(new_msgs)}")
            continue
        bad = None
        for om, nm in zip(old_msgs, new_msgs):
            if om.row != nm.row or om.label != nm.label or len(om.pages) != len(nm.pages):
                bad = f"structure changed at row {om.row}"
                break
            for op, np_ in zip(om.pages, nm.pages):
                key = (script, om.label, om.row, op.index)
                want, st = chs.get(key), status.get(key)
                if st != "source_untranslated" and want is not None and st is not None:
                    if key in known:
                        known_skipped += 1
                        if op.voice != np_.voice:
                            bad = f"row {om.row} page {op.index}: voice changed"
                            break
                        continue
                    if np_.text != want:
                        bad = f"row {om.row} page {op.index}: text != CN patch"
                        break
                    if sorted(scn.MARKUP.findall(want)) != sorted(scn.MARKUP.findall(np_.text)):
                        bad = f"row {om.row} page {op.index}: markup drift"
                        break
                    if np_.length != scn.page_length(np_.text):
                        bad = f"row {om.row} page {op.index}: length {np_.length} != " \
                              f"{scn.page_length(np_.text)}"
                        break
                elif np_.text != op.text:
                    bad = f"row {om.row} page {op.index}: should have kept the official text"
                    break
                if op.voice != np_.voice:
                    bad = f"row {om.row} page {op.index}: voice changed"
                    break
                pages_ok += 1
            if bad:
                break
        if not bad:
            # choice labels
            got_sel = {(script, s.label, s.index): s.text for s in new.iter_selects()}
            want_sel = {k: v for k, v in chs_sel.items() if k[0] == script}
            for key, want in want_sel.items():
                if got_sel.get(key) != want:
                    bad = f"select {key[1]}[{key[2]}]: text != CN patch"
                    break
            if not bad and set(got_sel) != {k for k in steam_sel if k[0] == script}:
                bad = "select keys changed"
            if not bad:
                selects_ok += len(want_sel)
        if bad:
            problems.append(f"{name}: {bad}")
        else:
            scripts_ok += 1
    log(f"verify: {scripts_ok}/{len(entries)} script(s) ok "
        f"(name hash + texts + markup + length + voice), {pages_ok} page(s) compared, "
        f"{selects_ok} select(s), {known_skipped} known deviation(s), "
        f"{len(problems)} problem(s)")
    for p in problems[:15]:
        log("  - " + p)
    if problems:
        raise SystemExit(1)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch")
    p.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1),
                   help="worker processes (1 = serial)")
    p.set_defaults(func=cmd_fetch)
    sub.add_parser("roundtrip").set_defaults(func=cmd_roundtrip)

    p = sub.add_parser("apply")
    p.add_argument("--out", default=os.path.join(ROOT, "tmp", "out_zhcn"))
    p.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1),
                   help="parallel worker processes (one script at a time each)")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("pack")
    p.add_argument("--src", default=os.path.join(ROOT, "tmp", "out_zhcn"))
    p.add_argument("--out", default=os.path.join(ROOT, "releases", "retranslate_zhcn.xp3"))
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("verify")
    p.add_argument("--xp3", default=os.path.join(ROOT, "releases", "retranslate_zhcn.xp3"))
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
