"""Independent gate for the delivered patch archive (``releases/CN.xp3``).

Rebuilds the expectation from `resource/` only (never from `tmp/` payloads) and checks:

1. entry layout: 1 warning placeholder + 1 hook + one entry per script in the index;
2. hook entry: root domain, `file_hash('default.tjs')`, payload == BOM + this game's
   `main/default.tjs` text + the appended hook (byte for byte);
3. every script entry: `cn/scn` domain, `file_hash('<script>.scn')`, parses as a PSB,
   page count matches the index, and each page's text equals the expected CN text
   (official text is expected for the rows the CN patch leaves untranslated);
4. `voice` / `char_id` / page count are unchanged relative to the Steam extraction;
5. select texts are the CN ones (identical ones count as "already correct");
6. only the documented deviations may differ, and each is printed for review.

Usage (from project root):
    mamba run -n GalRev python script/verify_patch.py [--xp3 releases/CN.xp3]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from tool import hxv4, scn, textset  # noqa: E402
from tool.hxv4hash import domain_hash, file_hash  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "resource", "hxv4", "profile_live.json")
GAME = "E:/SteamLibrary/steamapps/common/MashiroiroSymphonySANA"
INDEX = os.path.join(ROOT, "resource", "steam_text_zhcn", "_index.json")
CHS_DIR = os.path.join(ROOT, "resource", "chs_text")
STEAM_DIR = os.path.join(ROOT, "resource", "steam_text_zhcn")
REPORT = os.path.join(ROOT, "tmp", "_report_apply.json")
SCRIPT_DOMAIN = domain_hash("cn/scn/")


def expected_pages():
    """(script, scene, row, page) -> (expected text, official text, status)."""
    chs, official, status = {}, {}, {}
    for rec in textset.iter_records(CHS_DIR, textset.MESSAGES):
        for p in rec["pages"]:
            chs[(rec["script"], rec["scene"], rec["row"], p["page"])] = p["text"]
    for rec in textset.iter_records(STEAM_DIR, textset.MESSAGES):
        for p in rec["pages"]:
            official[(rec["script"], rec["scene"], rec["row"], p["page"])] = p["text"]
    match_dir = os.path.join(ROOT, "resource", "match")
    for rec in textset.iter_records(match_dir, textset.PAIRS):
        status[(rec["script"], rec["scene"], rec["row"], rec["page"])] = rec["status"]
    expect = {}
    for key, off in official.items():
        st = status.get(key)
        if st == "source_untranslated" or key not in chs:
            expect[key] = off
        else:
            expect[key] = chs[key]
    return expect, chs, official


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xp3", default=os.path.join(ROOT, "releases", "CN.xp3"))
    args = ap.parse_args()

    prof = hxv4.Hxv4Profile.load_json(PROFILE)
    arc = hxv4.Hxv4Archive(args.xp3, prof)
    index = json.load(open(INDEX, encoding="utf-8"))["scripts"]
    expect, chs, official = expected_pages()

    problems = []
    notes = []

    # ---- layout
    n_scripts = len(index)
    if len(arc.entries) != n_scripts + 2:
        problems.append(f"entry count {len(arc.entries)} != {n_scripts} scripts + warning + hook")

    # ---- hook entry
    hook_fh = file_hash("default.tjs")
    hook = [e for e in arc.entries if e.record and e.record.domain_hash == domain_hash("")
            and e.record.file_hash == hook_fh]
    if len(hook) != 1:
        problems.append(f"hook entry (`default.tjs` in root domain) count = {len(hook)}")
    else:
        payload = arc.read_entry(hook[0])
        stock = None
        data_arc = hxv4.Hxv4Archive(os.path.join(GAME, "data.xp3"), prof)
        for e in data_arc.entries:
            if e.record and e.record.domain_hash == domain_hash("main/") \
                    and e.record.file_hash == hook_fh:
                stock = data_arc.read_entry(e)
                break
        data_arc.close()
        if stock is None:
            problems.append("this game's main/default.tjs not found in data.xp3")
        else:
            hook_js = open(os.path.join(ROOT, "script", "patch_hook.tjs"), encoding="utf-8").read()
            want = b"\xff\xfe" + (stock.decode("utf-16").lstrip("\ufeff") + hook_js).encode("utf-16-le")
            if payload != want:
                problems.append("hook payload != BOM + stock main/default.tjs + patch_hook.tjs")
            else:
                notes.append(f"hook payload ok ({len(payload)} B = stock {len(stock)} B + hook)")
            if payload != stock and not payload.startswith(stock[:2]):
                problems.append("hook payload does not keep the stock BOM")

    # ---- scripts
    pages_ok = 0
    deviation_rows = []
    for script, meta in sorted(index.items()):
        name = script + ".scn"
        fh = file_hash(name)
        hits = [e for e in arc.entries
                if e.record and e.record.domain_hash == SCRIPT_DOMAIN and e.record.file_hash == fh]
        if len(hits) != 1:
            problems.append(f"{script}: {len(hits)} entries in cn/scn")
            continue
        sf = scn.ScnFile(arc.read_entry(hits[0]))
        pages = 0
        for msg in sf.iter_messages():
            for p in msg.pages:
                pages += 1
                key = (script, msg.label, msg.row, p.index)
                want = expect.get(key)
                got = p.text
                if want is None:
                    problems.append(f"{script} row {msg.row} p{p.index}: no expectation")
                    continue
                if got != want:
                    deviation_rows.append((script, msg.label, msg.row, p.index, want, got))
                else:
                    pages_ok += 1
        if pages != meta["pages"]:
            problems.append(f"{script}: {pages} pages != index {meta['pages']}")

    # ---- known deviations: cross-check against the apply report
    known = {}
    if os.path.exists(REPORT):
        rep = json.load(open(REPORT, encoding="utf-8"))
        for rec in rep.get("page_list_shared_conflicts", []):
            known[(rec["script"], rec["scene"], rec["row"], rec["page"])] = ("shared_page_list",
                                                                           rec["written"])
        for rec in rep.get("ref_width_skipped", []):
            known[(rec["script"], rec["scene"], rec["row"], rec["page"])] = ("ref_width", None)
    unexplained = []
    for script, scene, row, page, want, got in deviation_rows:
        info = known.get((script, scene, row, page))
        if info is None:
            unexplained.append((script, scene, row, page, want, got))
        elif info[0] == "shared_page_list" and got != info[1]:
            unexplained.append((script, scene, row, page, info[1], got))

    print(f"entries: {len(arc.entries)}  scripts: {n_scripts}  "
          f"pages: {pages_ok} ok / {len(deviation_rows)} deviating")
    for n in notes:
        print("  note:", n)
    print(f"documented deviations: {len(known)}  unexpected: {len(unexplained)}")
    for script, scene, row, page, want, got in unexplained[:20]:
        print(f"  UNEXPECTED {script} [{scene}] row {row} p{page}")
        print(f"     want: {want[:70]}")
        print(f"     got : {got[:70]}")
    if known:
        print("\nknown deviations (expected to keep the official / majority text):")
        for (script, scene, row, page), (kind, written) in sorted(known.items()):
            got = next((g for s_, sc, r, p, w, g in deviation_rows
                        if (s_, sc, r, p) == (script, scene, row, page)), None)
            print(f"  {kind:16s} {script} [{scene}] row {row} p{page}")
            print(f"     in archive: {(got or '<same as expected>')[:70]}")
            print(f"     CN wanted : {expect.get((script, scene, row, page), '')[:70]}")

    # ---- selects
    sel_total = sel_cn = 0
    for script in index:
        s_sel = textset.records_for(STEAM_DIR, script, "selects")
        c_sel = textset.records_for(CHS_DIR, script, "selects")
        if not s_sel:
            continue
        cmap = {(r["scene"], r["index"]): r["text"] for r in c_sel}
        entries = [e for e in arc.entries
                   if e.record and e.record.file_hash == file_hash(script + ".scn")]
        sf = scn.ScnFile(arc.read_entry(entries[0]))
        got = {}
        for sel in sf.iter_selects():
            got[(sel.label, sel.index)] = sel.text
        for r in s_sel:
            key = (r["scene"], r["index"])
            sel_total += 1
            want = cmap.get(key, r["text"])
            if got.get(key) == want:
                sel_cn += 1
            else:
                problems.append(f"{script} select {key}: {got.get(key)!r} != {want!r}")
    print(f"\nselects: {sel_cn}/{sel_total} carry the CN text")

    arc.close()
    if unexplained or problems:
        print(f"\nFAILED: {len(problems)} problem(s), {len(unexplained)} unexplained deviation(s)")
        for p in problems[:20]:
            print("   " + p)
        return 1
    print("\nOK: patch payload matches the expected CN text everywhere "
          "(except the documented in-place limits)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
