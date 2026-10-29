"""Unpack an Hxv4 xp3 **offline** (no game, no injection).

Usage (from project root):
    mamba run -n GalRev python script/unpack_xp3.py \
        --profile resource/hxv4/profile_live.json \
        --xp3 "<game dir>/patch.xp3" --out tmp/unpacked/patch

Output layout mirrors what the injected extractor produces:
    <out>/<domain_hash>/<file_hash>          (file_hash = upper-case hex, 32 bytes)
    <out>/<archive>.alst                     (domain/file hash manifest)
"""

from __future__ import annotations

import argparse
import binascii
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool import hxv4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--xp3", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="only the first N entries (debug)")
    ap.add_argument("--skip-entry0", action="store_true", default=True,
                    help="entry 0 is the warning placeholder (default: skip)")
    args = ap.parse_args()

    profile = hxv4.Hxv4Profile.load_json(args.profile)
    arc = hxv4.Hxv4Archive(args.xp3, profile)
    os.makedirs(args.out, exist_ok=True)
    alst = os.path.join(args.out, os.path.basename(args.xp3) + ".alst")
    written = skipped = 0
    with open(alst, "w", encoding="utf-16") as manifest:
        for entry in arc.entries:
            if args.limit and entry.index >= args.limit:
                break
            if args.skip_entry0 and entry.index == 0:
                skipped += 1
                continue
            data = arc.read_entry(entry)
            rec = entry.record
            domain = binascii.hexlify(rec.domain_hash).decode().upper() if rec else "NODOMAIN"
            name = binascii.hexlify(rec.file_hash).decode().upper() if rec else f"entry{entry.index}"
            folder = os.path.join(args.out, domain)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, name), "wb") as fp:
                fp.write(data)
            manifest.write(f"{domain}##YSig##{domain}##YSig##{name}##YSig##{name}##YSig##\n")
            written += 1
            if written % 200 == 0:
                print(f"  {written} files ...", flush=True)
    print(f"{args.xp3}: entries={len(arc.entries)} written={written} skipped_entry0={skipped} -> {args.out}")


if __name__ == "__main__":
    main()
