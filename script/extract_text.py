"""Extract translatable text from krkr xp3 archives, or from an unpacked directory.

Usage (from project root):
    # plain xp3 (the CN fan patch) -- no profile needed
    mamba run -n GalRev python script/extract_text.py <a.xp3> [<b.xp3> ...] --out resource/chs_text
    # encrypted (Hxv4) archives -- needs the captured profile
    mamba run -n GalRev python script/extract_text.py <a.xp3> ...         --profile resource/hxv4/profile_live.json --domain 9D31BC7AA367A8F1 --out resource/steam_text_zhcn
    # from an unpacked directory of hash-named files (script/unpack_xp3.py output)
    mamba run -n GalRev python script/extract_text.py <unpacked dir> --out resource/steam_text_zhtw

When the same script appears in more than one container, the copy from the
*later* container wins (krkrz loads archives in ascending name order and the last
one registered takes precedence, which is why `patch2.xp3` overrides `patch.xp3`
overrides `data.xp3`). Every copy is recorded in `_index.json` -> `duplicate_copies`
so an override is visible instead of silent.

Outputs (inside --out):
    scripts/<script>.jsonl          one record per message row (per-script split)
    （数据按脚本分文件，直接用任意编辑器/jq 翻开单个脚本即可）
    scripts/<script>.selects.jsonl  choice labels (only if any)
    _index.json      per-script summary (name, hash, scenes, counts, anomalies)
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool import hxv4, textset, scn, xp3


def _looks_like_script(head: bytes) -> bool:
    """Accept both `*.scn` files and hash-named files dumped by an unpacker."""
    return head in (b"PSB" + bytes([0]), b"mdf" + bytes([0]))


def iter_scn_from_xp3(path, profile=None, domains=None):
    """Yield (arcname, data) for every script in one archive.

    Plain xp3 is read directly; Hxv4 archives need `profile` (see script/unpack_xp3.py).
    For Hxv4 the "arcname" is `<domain>/<file_hash>` so the domain filter keeps working.
    """
    import binascii
    import mmap
    with open(path, "rb") as fp:
        head = fp.read(3)
    if head.lower() == b"mdf":
        return
    if profile is not None:
        try:
            arc = hxv4.Hxv4Archive(path, profile)
        except ValueError:
            arc = None
        if arc is not None:
            for entry in arc.entries:
                rec = entry.record
                domain = binascii.hexlify(rec.domain_hash).decode() if rec else ""
                if domains and domain not in domains:
                    continue  # skip other language domains before paying the decrypt cost
                data = arc.read_entry(entry)
                if not _looks_like_script(data[:4]):
                    continue
                arcname = (f"{domain}/"
                           f"{binascii.hexlify(rec.file_hash).decode()}") if rec else entry.name
                yield arcname, data
            return
    fp = open(path, "rb")
    mm = mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        for entry in xp3.parse_xp3(mm, show_log=False):
            if not entry.name or entry.segms is None:
                continue
            data = bytes(xp3.extract_entry(mm, entry))
            if not _looks_like_script(data[:4]):
                continue
            yield entry.name, data
    finally:
        mm.close()
        fp.close()


def iter_scn_from_dir(path):
    """Walk a directory of *unpacked* files; scripts are recognised by magic bytes.

    Only the first 4 bytes are read to classify a file, so pointing this at a
    multi-gigabyte unpack output stays cheap.
    """
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            try:
                with open(full, "rb") as fp:
                    if not _looks_like_script(fp.read(4)):
                        continue
                    fp.seek(0)
                    data = fp.read()
            except OSError as exc:
                print(f"skip {full}: {exc}", file=sys.stderr)
                continue
            yield os.path.relpath(full, path).replace("\\", "/"), data


def split_container(src, arcname):
    """(container, domain) for a file.

    For xp3 input the container is the archive name; the Hxv4 hash domain (which for this
    game doubles as the language split) is carried in the arcname produced by
    ``iter_scn_from_xp3`` as ``<domain>/<filehash>``.  Dropping it there silently disabled the
    ``--domain`` filter and let an arbitrary domain win the duplicate-copy race
    (doc/lessons-learned.md §10).
    For an unpacked directory the layout is `<archive>/<domain>/<filehash>`
    (script/unpack_xp3.py output).
    """
    parts = [p for p in arcname.split("/") if p]
    if src.lower().endswith(".xp3"):
        domain = parts[0] if len(parts) >= 2 and _looks_like_domain(parts[0]) else ""
        return os.path.basename(src), domain.lower()
    if len(parts) >= 3:
        return parts[0], parts[1].lower()
    if len(parts) == 2:
        return parts[0], ""
    return os.path.basename(src), ""


_DOMAIN_RE = re.compile(r"^[0-9A-Fa-f]{16}$")


def _looks_like_domain(text):
    """Hxv4 domain keys in this game are 16 hex chars (e.g. `9D31BC7AA367A8F1`)."""
    return bool(_DOMAIN_RE.match(text))


def gather(inputs, domains=None, skipped=None, profile=None):
    """script name -> list of candidate copies ordered by container priority.

    Entries that carry the PSB/MDF magic but cannot be parsed (e.g. PSB files with an
    encrypted header that live in the same archive but are not scripts) are collected
    in `skipped` instead of aborting the run.
    """
    candidates: dict[str, list] = {}
    for src in inputs:
        if src.lower().endswith(".xp3"):
            source = iter_scn_from_xp3(src, profile, domains)
        else:
            source = iter_scn_from_dir(src)
        for arcname, data in source:
            try:
                sf = scn.ScnFile(data)
            except Exception as exc:  # noqa: BLE001 - report, do not abort
                if skipped is not None:
                    skipped.append(f"{arcname}: {type(exc).__name__}: {exc}")
                continue
            if not sf.scenes:
                # e.g. ``*.pimg`` payloads that also start with the PSB magic but are
                # image containers, not scripts
                if skipped is not None:
                    skipped.append(f"{arcname}: no scenes (not a script)")
                continue
            name = sf.script_name or os.path.basename(arcname)
            container, domain = split_container(src, arcname)
            if domains and not domain:
                # an unfilterable candidate would make `--domain` a silent no-op
                raise ValueError(
                    f"cannot tell the Hxv4 domain of {arcname!r} from {src}: "
                    "refusing to apply --domain blindly")
            if domains and domain not in domains:
                continue
            candidates.setdefault(name, []).append({
                "container": container,
                "domain": domain,
                "arcname": arcname,
                "hash": sf.script_hash,
                "data": data,
            })
    return candidates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="xp3 file or directory")
    ap.add_argument("--out", default="resource/chs_text")
    ap.add_argument("--domain", action="append", default=None,
                    help="only keep these Hxv4 domains (repeatable)")
    ap.add_argument("--profile", default=None,
                    help="Hxv4 profile json (resource/hxv4/profile_live.json) to read "
                         "encrypted archives offline")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    idx_path = os.path.join(args.out, "_index.json")

    skipped: list[str] = []
    profile = hxv4.Hxv4Profile.load_json(args.profile) if args.profile else None
    wanted = {d.lower() for d in args.domain} if args.domain else None
    candidates = gather(args.inputs, wanted, skipped, profile)
    index = {}
    dupes = []
    n_msg = n_page = n_sel = 0

    for name in sorted(candidates):
        copies = candidates[name]
        # archive priority: krkrz registers archives by ascending file name and
        # the last registered one is searched first, so keep the last copy.
        copies.sort(key=lambda c: (c["container"], c["domain"]))
        winner = copies[-1]
        if len(copies) > 1:
            dupes.append({
                "script": name,
                "winner": f'{winner["container"]}/{winner["domain"]}'.rstrip("/"),
                "overridden": [{"container": f'{c["container"]}/{c["domain"]}'.rstrip("/"),
                                "hash": c["hash"],
                                "same_content": c["hash"] == winner["hash"]}
                               for c in copies[:-1]],
            })
        rows, sels = [], []
        sf = scn.ScnFile(winner["data"])
        info = {
            "container": winner["container"],
            "domain": winner["domain"],
            "arcname": winner["arcname"],
            "hash": sf.script_hash,
            "scenes": [sf.scene_label(s) for s in sf.scenes],
            "messages": 0,
            "pages": 0,
            "selects": 0,
            "length_mismatch": 0,
            "missing_plain": 0,
        }
        for msg in sf.iter_messages():
            n_msg += 1
            info["messages"] += 1
            rec = {
                "script": name,
                "scene": msg.label,
                "row": msg.row,
                "speaker": msg.speaker,
                "flag": msg.flag,
                "pages": [],
            }
            for p in msg.pages:
                n_page += 1
                info["pages"] += 1
                if not p.length_ok:
                    info["length_mismatch"] += 1
                if p.plain is None:
                    info["missing_plain"] += 1
                rec["pages"].append({
                    "page": p.index,
                    "text": p.text,
                    "text_idx": p.text_idx,
                    "plain": p.plain,
                    "plain_idx": p.plain_idx,
                    "len": p.length,
                    "alt_speaker": p.alt_name,
                    "alt_speaker_idx": p.alt_name_idx,
                    "voice": p.voice,
                    "char_id": p.char_id,
                })
                rows.append(rec)
        for sel in sf.iter_selects():
            n_sel += 1
            info["selects"] += 1
            sels.append({
                "script": name, "scene": sel.label, "index": sel.index,
                "text": sel.text, "text_idx": sel.text_idx,
            })
        textset.write_script(args.out, name, rows, sels)
        index[name] = info

    with open(idx_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"scripts": index,
                   "duplicate_copies": dupes,
                   "domains": dict(collections.Counter(i["domain"] for i in index.values())),
                   "totals": {"scripts": len(index), "messages": n_msg,
                              "pages": n_page, "selects": n_sel}},
                  f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"scripts={len(index)} messages={n_msg} pages={n_page} selects={n_sel} "
          f"overridden={len(dupes)} -> {args.out}")
    if wanted:
        got = {i["domain"] for i in index.values()}
        print(f"domains requested={sorted(wanted)} present={sorted(got)}")
        if not got or wanted - got:
            raise SystemExit(f"requested domain(s) produced no script: {sorted(wanted - got)}")
    if skipped:
        print(f"skipped {len(skipped)} payload(s) that are not parseable scripts:", file=sys.stderr)
        for line in skipped[:5]:
            print("  " + line, file=sys.stderr)


if __name__ == "__main__":
    main()
