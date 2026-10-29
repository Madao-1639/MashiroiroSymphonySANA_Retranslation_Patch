"""Write Hxv4 (wamsoft) xp3 archives.

This build's archive reader only accepts the Hxv4 variant (a plain xp3 is opened and then
silently dropped), so a patch that has to be loaded by the engine must be written in this
format.  Everything needed is already available in `tool/hxv4.py` as *read* code:

* the index tree is a plain zlib blob (chunk list: one `Hxv4` descriptor + one `File` per entry)
* the mapping table (domain -> file_hash -> packed/key) is `xchacha_seal`ed; `xchacha_seal`
  is the exact inverse of the reader's `xchacha_open`
* the per-entry payload filter is a four-layer **XOR** stream, so encrypting is the same
  call as decrypting (`FilterState.apply`)
* `adlr` is the adler32 of the *plaintext* (verified against a stock archive)
* entry names are pseudo names (`U+5000 + index`); the engine binds entries to names only
  through the mapping table, so any `file_hash` in the table is what the engine hashes a
  requested name to

Round-trip guarantee: writing an entry back with the key/open_flag it came from reproduces
the original stored bytes byte for byte (checked in `script/writeback.py`).
"""

from __future__ import annotations

import struct
import zlib

from . import hxv4

XP3_SIG = hxv4.XP3_SIG
HEADER_LEN = 0x28


# ----------------------------------------------------------------------- tjs variant

def _i32(v: int) -> bytes:
    return struct.pack(">i", v)


def tjs_int(value: int) -> bytes:
    return b"" + struct.pack(">Q", value & 0xFFFFFFFFFFFFFFFF)


def tjs_octet(data: bytes) -> bytes:
    return b"\x03" + _i32(len(data)) + data


def tjs_string(text: str) -> bytes:
    return b"\x02" + _i32(len(text)) + text.encode("utf-16-be")


def tjs_array(items) -> bytes:
    return b"\x81" + _i32(len(items)) + b"".join(items)


# ------------------------------------------------------------------------ index parts

def _chunk(sig: bytes, body: bytes) -> bytes:
    return sig + struct.pack("<q", len(body)) + body


def _file_chunk(rec) -> bytes:
    """One `File` chunk.

    Layout copied from a stock archive: the name inside `info` is NUL terminated (the chunk
    is 2 bytes longer than `22 + 2*namelen`), and the sub-chunks are ordered
    `adlr, segm, info`.  Both details are tolerated by our reader but *not* by the engine.
    """
    name = rec["name"].encode("utf-16-le") + bytes((0, 0))
    info_fsize = rec.get("info_fsize", rec["fsize"])
    info_zsize = rec.get("info_zsize", rec["zsize"])
    info = struct.pack("<IqqH", rec["flags"], info_fsize, info_zsize, len(rec["name"]))
    info += name
    segs = []
    for i, (off, size) in enumerate(rec["segs"]):
        seg_flags = rec.get("seg_flags", 0)
        seg_fsize = rec.get("seg_fsize", size) if i == 0 else size
        seg_zsize = rec.get("seg_zsize", size) if i == 0 else size
        segs.append(struct.pack("<IQQQ", seg_flags, off, seg_fsize, seg_zsize))
    segm = b"".join(segs)
    adlr = struct.pack("<I", rec["adler"])
    return _chunk(b"File", _chunk(b"adlr", adlr) + _chunk(b"segm", segm) + _chunk(b"info", info))


def build_mapping(pairs) -> bytes:
    """`pairs` = [(domain8, file_hash32, packed, key)] -> TJS variant bytes."""
    groups = {}
    order = []
    for domain, file_hash, packed, key in pairs:
        if domain not in groups:
            groups[domain] = []
            order.append(domain)
        # the group is a flat (file_hash, [packed, key]) pair stream, not a nested array
        groups[domain].append(tjs_octet(file_hash))
        groups[domain].append(tjs_array([tjs_int(packed), tjs_int(key)]))
    top = []
    for domain in order:
        top.append(tjs_octet(domain))
        top.append(tjs_array(groups[domain]))
    return tjs_array(top)


# ------------------------------------------------------------------------- the writer

def write_archive(out_path, entries, profile, open_flag=0, warn=None):
    """Write an Hxv4 archive.

    `entries` = list of dicts:
        domain (bytes8), file_hash (bytes32), key (int), plaintext (bytes),
        flags (int, default 0x80000000), name (optional)
    Entry 0 is reserved for the warning placeholder when `warn_payload` is given; it is
    stored unfiltered and needs its own mapping record (the reader requires one record per
    entry), so pass `warn={payload, domain, file_hash, key, flags}` copied from a stock archive.
    """
    out = bytearray()
    out += XP3_SIG
    out += struct.pack("<Q", 0x17)          # decoy index offset
    out += struct.pack("<I", 1)             # 0x13
    out += b"\x80"                          # 0x17: Hxv4 variant flag
    out += b"\x00" * 8                      # 0x18..0x1F
    out += b"\x00" * 8                      # 0x20: real index offset (patched later)
    assert len(out) == HEADER_LEN

    files = []
    pairs = []
    index = 0
    if warn is not None:
        payload = warn.get("stored", warn["payload"])
        off = len(out)
        out += payload
        adler = warn.get("adler")
        if adler is None:
            adler = zlib.adler32(warn["payload"], 1) & 0xFFFFFFFF
        files.append(dict(index=0, name=chr(0x5000), flags=warn.get("flags", 0),
                          fsize=len(payload), zsize=len(payload),
                          segs=[(off, len(payload))], adler=adler,
                          info_fsize=warn.get("info_fsize", len(payload)),
                          info_zsize=warn.get("info_zsize", len(payload)),
                          seg_flags=warn.get("seg_flags", 0),
                          seg_fsize=warn.get("seg_fsize", len(payload)),
                          seg_zsize=warn.get("seg_zsize", len(payload))))
        pairs.append((warn["domain"], warn["file_hash"], 0, warn.get("key", 0)))
        index = 1

    for item in entries:
        key = item["key"]
        if "stored" in item:
            enc = item["stored"]                      # already filtered (copied verbatim)
        else:
            state = hxv4.FilterState(profile.drip.build_filter_state(key, open_flag))
            enc = bytes(state.apply(bytearray(item["plaintext"]), 0))
        off = len(out)
        out += enc
        adler = item.get("adler")
        if adler is None:
            adler = zlib.adler32(item["plaintext"], 1) & 0xFFFFFFFF
        files.append(dict(index=index, name=item.get("name") or chr(0x5000 + index),
                          flags=item.get("flags", 0x80000000), fsize=len(enc), zsize=len(enc),
                          segs=[(off, len(enc))], adler=adler,
                          info_fsize=item.get("info_fsize", len(enc)),
                          info_zsize=item.get("info_zsize", len(enc)),
                          seg_flags=item.get("seg_flags", 0),
                          seg_fsize=item.get("seg_fsize", len(enc)),
                          seg_zsize=item.get("seg_zsize", len(enc))))
        pairs.append((item["domain"], item["file_hash"], index, key))
        index += 1

    raw_mapping = build_mapping(pairs)
    payload_plain = struct.pack("<I", len(raw_mapping)) + zlib.compress(raw_mapping, 9)
    sealed = hxv4.xchacha_seal(profile.key,
                               profile.nonce1 if (open_flag & 1) else profile.nonce0,
                               payload_plain)
    payload_off = len(out)
    out += sealed

    tree = _chunk(b"Hxv4", struct.pack("<qIH", payload_off, len(sealed), open_flag & 1))
    for rec in files:
        tree += _file_chunk(rec)
    compressed = zlib.compress(tree, 9)
    index_blob = b"\x01" + struct.pack("<QQ", len(compressed), len(tree)) + compressed

    struct.pack_into("<Q", out, 0x20, len(out))
    out += index_blob
    with open(out_path, "wb") as fp:
        fp.write(out)
    return len(out)
