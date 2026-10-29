"""Verify an extraction directory produced by script/extract_text.py.

Usage (from project root):
    mamba run -n GalRev python script/verify_extract.py --data resource/chs_text
    # also re-read the source archives and diff against the extraction:
    mamba run -n GalRev python script/verify_extract.py --data resource/chs_text --source <a.xp3> [<b.xp3> ...]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool import textset, scn, xp3

# dummy.txt.scn is a developer scratch file: its page lengths do not track the text.
LENGTH_EXEMPT = {"dummy.txt"}


def load_jsonl(path):
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                yield json.loads(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="resource/chs_text")
    ap.add_argument("--source", nargs="*", default=None)
    args = ap.parse_args()

    errors = []
    warnings = []

    with open(os.path.join(args.data, "_index.json"), encoding="utf-8") as fp:
        index = json.load(fp)
    scripts = index["scripts"]
    totals = index["totals"]

    # ---- per-script messages
    n_msg = n_page = 0
    per_script = collections.Counter()
    per_script_pages = collections.Counter()
    bad_len = collections.Counter()
    markup_len = collections.Counter()
    empty_text = 0
    plain_eq = 0
    plain_diff = 0
    for rec in textset.iter_records(args.data, textset.MESSAGES):
        n_msg += 1
        name = rec["script"]
        per_script[name] += 1
        if not name or not rec.get("scene"):
            errors.append(f"record without script/scene: {rec}")
        if not rec.get("pages"):
            errors.append(f"record without pages: {name}|{rec.get('scene')}|{rec.get('row')}")
        for p in rec["pages"]:
            n_page += 1
            per_script_pages[name] += 1
            if not p["text"]:
                empty_text += 1
            if p["text_idx"] is None:
                errors.append(f"page without text_idx: {name}|{rec['scene']}|{rec['row']}|{p['page']}")
            if p["len"] != scn.u16len(p["text"]):
                if p["len"] == scn.page_length(p["text"]):
                    markup_len[name] += 1
                else:
                    bad_len[name] += 1
            if p["plain"] is not None:
                if p["text"].replace("\\n", "") == p["plain"]:
                    plain_eq += 1
                else:
                    plain_diff += 1

    if n_msg != totals["messages"]:
        errors.append(f"messages.jsonl has {n_msg} rows, _index.json says {totals['messages']}")
    if n_page != totals["pages"]:
        errors.append(f"pages {n_page} != _index.json {totals['pages']}")
    empty_scripts = {n for n, v in scripts.items() if v["messages"] == 0}
    unexpected = (set(scripts) - set(per_script)) - empty_scripts
    if unexpected:
        errors.append(f"scripts in index but missing from jsonl: {sorted(unexpected)[:5]}")
    if set(per_script) - set(scripts):
        errors.append(f"scripts in jsonl but not in index: {sorted(set(per_script) - set(scripts))[:5]}")
    for name, cnt in per_script.items():
        if name in scripts and cnt != scripts[name]["messages"]:
            errors.append(f"{name}: {cnt} messages but index says {scripts[name]['messages']}")
    if empty_text:
        warnings.append(f"{empty_text} pages with empty text")
    if markup_len:
        warnings.append(f"{sum(markup_len.values())} pages where length excludes markup "
                        f"(official convention) in {len(markup_len)} scripts")
    for name, cnt in bad_len.items():
        if name in LENGTH_EXEMPT:
            warnings.append(f"{name}: {cnt} pages where length != utf16len(text) (whitelisted)")
        else:
            errors.append(f"{name}: {cnt} pages where length != utf16len(text)")
    warnings.append(f"plain vs text\\n-less: equal={plain_eq} differ={plain_diff}")

    # ---- per-script selects
    n_sel = sum(1 for _ in textset.iter_records(args.data, textset.SELECTS))
    n_sel_index = sum(v["selects"] for v in scripts.values())
    if n_sel != n_sel_index:
        errors.append(f"selects {n_sel} != index {n_sel_index}")

    # ---- overridden copies: structure + the winner must be the kept one
    identical_overrides = 0
    for dup in index.get("duplicate_copies", []):
        name = dup.get("script")
        if name not in scripts:
            errors.append(f"duplicate copy of unknown script: {name}")
            continue
        winner = dup.get("winner")
        if not winner:
            errors.append(f"{name}: duplicate record without winner")
        kept = f'{scripts[name]["container"]}/{scripts[name].get("domain", "")}'.rstrip("/")
        if winner != kept:
            errors.append(f"{name}: winner {winner} != kept copy {kept}")
        for ov in dup.get("overridden", []):
            if ov.get("same_content"):
                identical_overrides += 1
    if index.get("duplicate_copies"):
        warnings.append(f"overridden copies: {len(index['duplicate_copies'])} scripts, "
                        f"{identical_overrides} of them byte-identical")

    # ---- optional: re-read the sources and compare
    if args.source:
        seen_hashes = {}
        src_msg = src_page = src_sel = 0
        for src in args.source:
            fp = open(src, "rb")
            mm = __import__("mmap").mmap(fp.fileno(), 0, access=__import__("mmap").ACCESS_READ)
            try:
                for entry in xp3.parse_xp3(mm, show_log=False):
                    if not entry.name or not entry.name.lower().endswith(".scn"):
                        continue
                    sf = scn.ScnFile(bytes(xp3.extract_entry(mm, entry)))
                    name = sf.script_name
                    if name in seen_hashes:
                        if seen_hashes[name] != sf.script_hash:
                            errors.append(f"conflicting copies of {name} while verifying")
                        continue
                    seen_hashes[name] = sf.script_hash
                    msgs = list(sf.iter_messages())
                    src_msg += len(msgs)
                    src_page += sum(len(m.pages) for m in msgs)
                    src_sel += len(list(sf.iter_selects()))
            finally:
                mm.close()
                fp.close()
        if src_msg != n_msg:
            errors.append(f"source messages {src_msg} != extracted {n_msg}")
        if src_page != n_page:
            errors.append(f"source pages {src_page} != extracted {n_page}")
        if src_sel != n_sel:
            errors.append(f"source selects {src_sel} != extracted {n_sel}")
        warnings.append(f"re-read sources: scripts={len(seen_hashes)} messages={src_msg} pages={src_page} selects={src_sel}")

    print(f"scripts={len(scripts)} messages={n_msg} pages={n_page} selects={n_sel}")
    for w in warnings:
        print("WARN", w)
    if errors:
        for e in errors:
            print("ERROR", e)
        print(f"FAILED: {len(errors)} error(s)")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
