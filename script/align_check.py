"""Stage-2 gate: assert that the Steam scripts and the CN patch scripts are structurally identical.

Usage (from project root):
    mamba run -n GalRev python script/align_check.py
    # also verify that every script name resolves in the game archives (needed by the pack stage):
    mamba run -n GalRev python script/align_check.py --game "E:/SteamLibrary/steamapps/common/MashiroiroSymphonySANA"

What is checked, per shared script:
    rows, (scene, row) keys, page counts, voice ids (when both sides carry one), char_id,
    speaker presence, select indexes.
A mismatch is a hard error (exit 1): the positional mapping used by script/build_pairs.py
is only valid while the structures agree.

Scripts present on one side only are reported, not fatal: the CN patch is a superset
(it also carries the other heroines' route fragments, which this game does not have).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from tool import hxv4, textset  # noqa: E402
from tool.hxv4hash import domain_hash, file_hash  # noqa: E402

SCRIPT_DOMAIN = "9d31bc7aa367a8f1"


def index_of(base):
    with open(os.path.join(base, "_index.json"), encoding="utf-8") as fp:
        return json.load(fp)["scripts"]


def per_scene(records):
    """[(scene, row)] order as stored, plus a lookup for page comparison."""
    return [(r["scene"], r["row"]) for r in records]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--left", default="resource/chs_text", help="CN patch text set")
    ap.add_argument("--right", default="resource/steam_text_zhcn", help="Steam text set")
    ap.add_argument("--game", default=None, help="game dir; adds the name-hash coverage check")
    ap.add_argument("--profile", default="resource/hxv4/profile_live.json")
    args = ap.parse_args()

    left_idx, right_idx = index_of(args.left), index_of(args.right)
    left_names, right_names = set(left_idx), set(right_idx)
    shared = sorted(left_names & right_names)
    only_left = sorted(left_names - right_names)
    only_right = sorted(right_names - left_names)

    print(f"CN patch scripts={len(left_names)}  Steam scripts={len(right_names)}  shared={len(shared)}")
    if only_right:
        print(f"!! Steam-only scripts (would keep the official text): {only_right}")

    errors = []
    tot = dict(rows=0, pages=0, voiced=0, selects=0)
    for script in shared:
        a = textset.records_for(args.left, script)
        b = textset.records_for(args.right, script)
        if len(a) != len(b):
            errors.append(f"{script}: rows {len(a)} != {len(b)}")
            continue
        if per_scene(a) != per_scene(b):
            errors.append(f"{script}: (scene, row) key sequence differs")
        for ra, rb in zip(a, b):
            tot["rows"] += 1
            if bool(ra["speaker"]) != bool(rb["speaker"]):
                errors.append(f"{script} row {rb['row']}: speaker presence differs")
            pa, pb = ra["pages"], rb["pages"]
            if len(pa) != len(pb):
                errors.append(f"{script} row {rb['row']}: pages {len(pa)} != {len(pb)}")
                continue
            for x, y in zip(pa, pb):
                tot["pages"] += 1
                if x.get("voice") or y.get("voice"):
                    tot["voiced"] += 1
                    if x.get("voice") != y.get("voice"):
                        errors.append(f"{script} row {rb['row']}: voice {x.get('voice')} != {y.get('voice')}")
                if x.get("char_id") != y.get("char_id"):
                    errors.append(f"{script} row {rb['row']}: char_id {x.get('char_id')} != {y.get('char_id')}")
        sa = textset.records_for(args.left, script, textset.SELECTS)
        sb = textset.records_for(args.right, script, textset.SELECTS)
        if [r["index"] for r in sa] != [r["index"] for r in sb]:
            errors.append(f"{script}: select indexes differ ({len(sa)} vs {len(sb)})")
        tot["selects"] += len(sb)

    print(f"checked: rows={tot['rows']} pages={tot['pages']} voiced={tot['voiced']} selects={tot['selects']}")

    if only_left:
        rows = 0
        print(f"CN-patch-only scripts ({len(only_left)}, no landing spot in this game):")
        for n in only_left:
            cnt = len(textset.records_for(args.left, n))
            rows += cnt
            print(f"   {n:40s} {cnt:5d} rows")
        print(f"   total {rows} rows (excluded from the patch)")

    if args.game:
        prof = hxv4.Hxv4Profile.load_json(args.profile)
        have = {}
        for name in ("data.xp3", "patch.xp3"):
            path = os.path.join(args.game, name)
            if not os.path.exists(path):
                print(f"!! missing archive: {path}")
                continue
            arc = hxv4.Hxv4Archive(path, prof)
            for e in arc.entries:
                if e.record is not None and e.record.domain_hash.hex() == SCRIPT_DOMAIN:
                    have[e.record.file_hash] = name
            arc.close()
        missing = [s for s in shared if file_hash(s + ".scn") not in have]
        print(f"name-hash coverage: {len(shared) - len(missing)}/{len(shared)} scripts exist in the game's cn/scn domain")
        for s in missing:
            errors.append(f"{s}: file_hash not present in cn/scn domain")

    if errors:
        print(f"\nFAILED with {len(errors)} problem(s):")
        for e in errors[:60]:
            print("   " + e)
        return 1
    print("\nOK: structures identical, positional mapping is valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
