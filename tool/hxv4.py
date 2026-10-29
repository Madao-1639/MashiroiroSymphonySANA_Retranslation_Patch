"""Hxv4 (Wamsoft / KiriKiriZ) archive support: XChaCha20-Poly1305 index + DRIP filter.

Algorithm reference: YuaExtract's offline implementation
(`CxdecRepacker/core/drip.cpp`, `filter.cpp`, `mapping.cpp`, `tjs_variant.cpp`,
`crypto/chacha.cpp`, `crypto/aead.cpp`) and `docs/hxv4/01..06`.

Key material (key / nonces / holder words / DRIP table / lanes) is per game build and can
only be read out of the *running* game -- see `doc/hxv4.md` §3 and
`script/capture_live_crypto.py`. Static derivation from the exe yields a key and a DRIP
context table that do **not** match the shipping archives.

Layout recap:
  * archive header: XP3 magic, then a decoy index offset; the real one sits at 0x20.
  * index: chunk tree, `Hxv4` descriptor (14 bytes) + `File` entries with fake names.
  * `Hxv4` payload: [16B Poly1305 tag][XChaCha20 ciphertext] -> u32 len + zlib(TJS variant).
  * each record: domain_hash[8], file_hash[32], packed u32 (hi16 slot, lo16 flag), key u64.
  * file content: optional zlib, then the DRIP XOR filter seeded by `key`.
"""

from __future__ import annotations

import io
import json
import mmap
import struct
import zlib
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- chacha

_SIGMA = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)  # "expand 32-byte k"


def _rotl(v: int, c: int) -> int:
    v &= 0xFFFFFFFF
    return ((v << c) | (v >> (32 - c))) & 0xFFFFFFFF


def _quarter(x: list[int], a: int, b: int, c: int, d: int) -> None:
    x[a] = (x[a] + x[b]) & 0xFFFFFFFF
    x[d] = _rotl(x[d] ^ x[a], 16)
    x[c] = (x[c] + x[d]) & 0xFFFFFFFF
    x[b] = _rotl(x[b] ^ x[c], 12)
    x[a] = (x[a] + x[b]) & 0xFFFFFFFF
    x[d] = _rotl(x[d] ^ x[a], 8)
    x[c] = (x[c] + x[d]) & 0xFFFFFFFF
    x[b] = _rotl(x[b] ^ x[c], 7)


def _permute(x: list[int]) -> None:
    for _ in range(10):  # 20 rounds
        _quarter(x, 0, 4, 8, 12)
        _quarter(x, 1, 5, 9, 13)
        _quarter(x, 2, 6, 10, 14)
        _quarter(x, 3, 7, 11, 15)
        _quarter(x, 0, 5, 10, 15)
        _quarter(x, 1, 6, 11, 12)
        _quarter(x, 2, 7, 8, 13)
        _quarter(x, 3, 4, 9, 14)


def chacha20_block(key: bytes, counter: int, nonce8: bytes) -> bytes:
    """One 64-byte keystream block, DJB layout (64-bit counter, 64-bit nonce)."""
    s = list(_SIGMA)
    s += list(struct.unpack("<8I", key))
    s.append(counter & 0xFFFFFFFF)
    s.append((counter >> 32) & 0xFFFFFFFF)
    s += list(struct.unpack("<2I", nonce8))
    x = s[:]
    _permute(x)
    return struct.pack("<16I", *[(x[i] + s[i]) & 0xFFFFFFFF for i in range(16)])


def chacha20_xor(key: bytes, counter: int, nonce8: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    off = 0
    while off < len(data):
        ks = chacha20_block(key, counter, nonce8)
        n = min(64, len(data) - off)
        for i in range(n):
            out[off + i] = data[off + i] ^ ks[i]
        off += n
        counter += 1
    return bytes(out)


def hchacha20(key: bytes, nonce16: bytes) -> bytes:
    s = list(_SIGMA) + list(struct.unpack("<8I", key)) + list(struct.unpack("<4I", nonce16))
    _permute(s)
    return struct.pack("<4I", *s[0:4]) + struct.pack("<4I", *s[12:16])


# --------------------------------------------------------------------------- poly1305

_P1305 = (1 << 130) - 5


def poly1305(key: bytes, msg: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    acc = 0
    for i in range(0, len(msg), 16):
        chunk = msg[i:i + 16]
        n = int.from_bytes(chunk + b"\x01", "little")
        acc = ((acc + n) * r) % _P1305
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


# --------------------------------------------------------------------------- aead

def _xchacha_keys(key: bytes, nonce24: bytes):
    subkey = hchacha20(key, nonce24[:16])
    poly_key = chacha20_block(subkey, 0, nonce24[16:24])[:32]
    return subkey, poly_key


def _mac(poly_key: bytes, ciphertext: bytes) -> bytes:
    pad = (16 - (len(ciphertext) % 16)) % 16
    msg = ciphertext + b"\x00" * pad + struct.pack("<Q", 0) + struct.pack("<Q", len(ciphertext))
    return poly1305(poly_key, msg)


def xchacha_open(key: bytes, nonce24: bytes, sealed: bytes) -> bytes | None:
    """Verify + decrypt `[16B tag][ciphertext]`; returns None when the tag fails."""
    if len(sealed) < 16:
        return None
    subkey, poly_key = _xchacha_keys(key, nonce24)
    ct = sealed[16:]
    if _mac(poly_key, ct) != sealed[:16]:
        return None
    return chacha20_xor(subkey, 1, nonce24[16:24], ct)


def xchacha_seal(key: bytes, nonce24: bytes, plaintext: bytes) -> bytes:
    subkey, poly_key = _xchacha_keys(key, nonce24)
    ct = chacha20_xor(subkey, 1, nonce24[16:24], plaintext)
    return _mac(poly_key, ct) + ct


# --------------------------------------------------------------------------- tjs variant

class TjsError(Exception):
    pass


@dataclass
class TjsValue:
    type: str
    value: object = None

    def __repr__(self):
        return f"TjsValue({self.type}, {str(self.value)[:40]})"


class _Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise TjsError(f"truncated at 0x{self.pos:x}")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def u8(self) -> int:
        return self.take(1)[0]


def _utf16be_to_str(raw: bytes) -> str:
    return raw.decode("utf-16-be", errors="replace")


def _parse_value(c: _Cursor, depth: int = 0) -> TjsValue:
    if depth > 64:
        raise TjsError("depth")
    tag = c.u8()
    if tag in (0, 1):
        return TjsValue("null")
    if tag == 2:
        units = c.i32()
        return TjsValue("string", _utf16be_to_str(c.take(units * 2)))
    if tag == 3:
        size = c.i32()
        return TjsValue("octet", c.take(size))
    if tag == 4:
        return TjsValue("int", int.from_bytes(c.take(8), "big"))
    if tag == 5:
        return TjsValue("real", struct.unpack(">d", c.take(8))[0])
    if tag == 0x81:
        count = c.i32()
        return TjsValue("array", [_parse_value(c, depth + 1) for _ in range(count)])
    if tag == 0xC1:
        count = c.i32()
        out = {}
        for _ in range(count):
            k = _parse_value(c, depth + 1)
            if k.type != "string":
                raise TjsError("dict key is not a string")
            out[k.value] = _parse_value(c, depth + 1)
        return TjsValue("dict", out)
    raise TjsError(f"unknown variant tag 0x{tag:02x}")


def tjs_parse(data: bytes) -> TjsValue:
    return _parse_value(_Cursor(data))


# --------------------------------------------------------------------------- drip vm

# Opcode identities are module RVAs; the profile stores absolute VAs.
OP_STOP = 0x51D90
OP_ADD_IMM = 0x17C50
OP_RECURSE = 0x17C60
OP_ADD_SCRATCH = 0x17CB0
OP_MUL_SCRATCH = 0x17CD0
OP_SCRATCH_MINUS_RESULT = 0x17CF0
OP_SHL_SCRATCH = 0x17D10
OP_SHR_SCRATCH = 0x17D30
OP_SUB_SCRATCH = 0x17D50
OP_BIT_SHUFFLE = 0x17D70
OP_SET_IMM = 0x17DA0
OP_SET_SEED = 0x17DB0
OP_DEC = 0x17DD0
OP_INC = 0x17DE0
OP_NEG = 0x17DF0
OP_NOT = 0x17E00
OP_TABLE_IMM = 0x17E10
OP_TABLE_MASKED = 0x17E30
OP_SUB_IMM = 0x17E50
OP_STORE_SCRATCH = 0x17E60
OP_XOR_IMM = 0x17E80

_M32 = 0xFFFFFFFF


class DripProgram:
    """128 lane programs + a 1024-entry table: 64-bit key -> 48-byte filter seed."""

    def __init__(self, holder_words, context, lanes):
        self.holder = list(holder_words)
        self.context = list(context)
        self.lanes = lanes
        if len(self.holder) < 6 or len(self.context) < 1024 or len(self.lanes) != 128:
            raise ValueError("incomplete DRIP program profile")

    # -- lane evaluation
    def _eval(self, lane, pc: int, result: int, scratch: int, seed: int, depth: int):
        if depth > 64:
            raise ValueError("drip recursion too deep")
        while pc < len(lane):
            param, op = lane[pc]
            pc += 1
            if op == OP_STOP:
                break
            if op == OP_RECURSE:
                result, pc = self._eval(lane, pc, result, scratch, seed, depth + 1)
                continue
            if op == OP_ADD_IMM:
                result = (result + param) & _M32
            elif op == OP_ADD_SCRATCH:
                result = (result + scratch) & _M32
            elif op == OP_MUL_SCRATCH:
                result = (result * scratch) & _M32
            elif op == OP_SCRATCH_MINUS_RESULT:
                result = (scratch - result) & _M32
            elif op == OP_SHL_SCRATCH:
                result = (result << (scratch & 0xF)) & _M32
            elif op == OP_SHR_SCRATCH:
                result = (result & _M32) >> (scratch & 0xF)
            elif op == OP_SUB_SCRATCH:
                result = (result - scratch) & _M32
            elif op == OP_BIT_SHUFFLE:
                result = ((2 * (result & ~param)) | ((param >> 1) & (result >> 1))) & _M32
            elif op == OP_SET_IMM:
                result = param & _M32
            elif op == OP_SET_SEED:
                result = seed & _M32
            elif op == OP_DEC:
                result = (result - 1) & _M32
            elif op == OP_INC:
                result = (result + 1) & _M32
            elif op == OP_NEG:
                result = (-result) & _M32
            elif op == OP_NOT:
                result = (~result) & _M32
            elif op == OP_TABLE_IMM:
                result = self.context[param]
            elif op == OP_TABLE_MASKED:
                result = self.context[param & result]
            elif op == OP_SUB_IMM:
                result = (result - param) & _M32
            elif op == OP_STORE_SCRATCH:
                scratch = result
            elif op == OP_XOR_IMM:
                result = result ^ param
            else:
                raise ValueError(f"unknown drip opcode 0x{op:x}")
        return result & _M32, pc

    def eval_lane(self, lane_index: int, seed: int) -> int:
        return self._eval(self.lanes[lane_index], 0, 0, 0, seed & _M32, 0)[0]

    def get64(self, value: int) -> int:
        lane_index = value & 0x7F
        seed = (value >> 7) & _M32
        lo = self.eval_lane(lane_index, seed)
        hi = self.eval_lane(lane_index, (~seed) & _M32)
        return lo | (hi << 32)

    # -- seed state
    def build_filter_state(self, key: int, open_flag: int) -> bytes:
        key_lo = key & _M32
        key_hi = (key >> 32) & _M32
        if (open_flag & 1) == 0:
            key_lo ^= self.holder[2]
            key_hi ^= self.holder[3]
        key64 = key_lo | (key_hi << 32)

        out = bytearray(48)
        out[0:8] = self.get64(key_lo).to_bytes(8, "little")
        out[8:16] = self.get64(key_hi).to_bytes(8, "little")
        bulk_offset = (self.holder[5] + (self.holder[4] & ((key64 >> 16) & _M32))) & _M32
        out[16:20] = bulk_offset.to_bytes(4, "little")

        cur = (~key64) & 0xFFFFFFFFFFFFFFFF
        bitpos = -1
        pos = 24
        while pos < 40:
            if bitpos < 0:
                cur = (~self.get64(cur & _M32)) & 0xFFFFFFFFFFFFFFFF
                bitpos = 64
            else:
                out[pos] = (cur >> bitpos) & 0xFF
                pos += 1
            bitpos -= 8

        out[44] = 1
        out[45] = 0
        return bytes(out)


# --------------------------------------------------------------------------- filter

@dataclass
class _Boundary:
    pos0: int = 0
    pos1: int = 0
    key: int = 0
    byte0: int = 0
    byte1: int = 0


class FilterState:
    """Four-layer XOR filter; encryption and decryption are the same operation."""

    def __init__(self, seed_state: bytes):
        if len(seed_state) < 46:
            raise ValueError("filter seed state too short")
        null_mode = seed_state[45] != 0
        self.b0 = self._parse(int.from_bytes(seed_state[0:8], "little"), null_mode)
        self.b1 = self._parse(int.from_bytes(seed_state[8:16], "little"), null_mode)
        self.split = int.from_bytes(seed_state[16:24], "little")
        self.has_bulk = seed_state[44] != 0
        self.bulk = seed_state[24:40]

    @staticmethod
    def _parse(value: int, null_mode: bool) -> _Boundary:
        b = _Boundary()
        b.pos0 = (value >> 48) & 0xFFFF
        b.pos1 = (value >> 32) & 0xFFFF
        if b.pos0 == b.pos1:
            b.pos1 = (b.pos1 + 1) & 0xFFFF
        key_byte = value & 0xFF
        b.byte0 = (value >> 8) & 0xFF
        b.byte1 = (value >> 16) & 0xFF
        if key_byte == 0:
            key_byte = 0 if null_mode else 0xA5
        b.key = key_byte * 0x01010101
        if null_mode:
            b.byte0 = 0
            b.byte1 = 0
        return b

    @staticmethod
    def _apply_boundary(buf: bytearray, b: _Boundary, chunk_start: int, buf_start: int,
                        size: int) -> None:
        if size <= 0:
            return
        for i in range(size):
            shift = ((chunk_start + i) & 3) * 8
            buf[buf_start + i] ^= (b.key >> shift) & 0xFF
        if b.byte0 and b.pos0 >= chunk_start and b.pos0 < chunk_start + size:
            buf[buf_start + b.pos0 - chunk_start] ^= b.byte0
        if b.byte1 and b.pos1 >= chunk_start and b.pos1 < chunk_start + size:
            buf[buf_start + b.pos1 - chunk_start] ^= b.byte1

    def apply(self, data: bytes, offset: int = 0) -> bytes:
        buf = bytearray(data)
        n = len(buf)
        if n == 0:
            return bytes(buf)
        end = offset + n
        if self.has_bulk and offset < 16:
            for logical in range(offset, min(end, 16)):
                buf[logical - offset] ^= self.bulk[logical]
        split = self.split
        if split <= offset:
            self._apply_boundary(buf, self.b1, offset, 0, n)
        elif split < end:
            first = split - offset
            self._apply_boundary(buf, self.b0, offset, 0, first)
            self._apply_boundary(buf, self.b1, split, first, n - first)
        else:
            self._apply_boundary(buf, self.b0, offset, 0, n)
        return bytes(buf)


# --------------------------------------------------------------------------- profile

@dataclass
class Hxv4Profile:
    key: bytes
    nonce0: bytes
    nonce1: bytes
    hash_key: bytes
    salt: str
    drip: DripProgram
    source: str = ""

    @classmethod
    def load_json(cls, path: str) -> "Hxv4Profile":
        with open(path, encoding="utf-8") as fp:
            d = json.load(fp)
        base = d.get("source_module_base", 0)
        lanes = []
        for lane in d["lanes"]:
            lanes.append([(int(rec[0]) & 0xFFFFFFFF, int(rec[1]) - base) for rec in lane["records"]])
        drip = DripProgram(d["holder_words"], d["context_u32"], lanes)
        return cls(
            key=bytes.fromhex(d["hxv4_key"]),
            nonce0=bytes.fromhex(d["hxv4_nonce0"]),
            nonce1=bytes.fromhex(d["hxv4_nonce1"]),
            hash_key=bytes.fromhex(d.get("hash_key", "") or "00" * 32),
            salt=d.get("hash_domain") or d.get("salt", ""),
            drip=drip,
            source=path,
        )


# --------------------------------------------------------------------------- archive

XP3_SIG = bytes.fromhex("58 50 33 0D 0A 20 0A 1A 8B 67 01")


@dataclass
class Record:
    domain_hash: bytes
    file_hash: bytes
    packed: int
    key: int

    @property
    def slot(self) -> int:
        return (self.packed >> 16) & 0xFFFF

    @property
    def flag(self) -> int:
        return self.packed & 0xFFFF


@dataclass
class Entry:
    index: int
    name: str
    flags: int
    fsize: int
    segms: list  # [(flags, offset, fsize, zsize)]
    adler: int | None = None
    record: Record | None = None


def _is_scrambled(data: bytes) -> bool:
    return len(data) >= 5 and data[0] == 0xFE and data[1] == 0xFE and data[3] == 0xFF and data[4] == 0xFE


def _descramble(data: bytes) -> bytes:
    """tvp text scramble (FE FE mode FF FE): bit-swap pairs back into UTF-16LE."""
    mode = data[2]
    body = bytearray(data[5:])
    if mode == 2:
        return zlib.decompress(bytes(body))
    if mode == 1:
        for i in range(0, len(body) - 1, 2):
            c = body[i] | (body[i + 1] << 8)
            s = ((c & 0xAAAA) >> 1) | ((c & 0x5555) << 1)
            body[i] = s & 0xFF
            body[i + 1] = (s >> 8) & 0xFF
        return bytes(body)
    raise ValueError(f"unknown text scramble mode {mode}")


class Hxv4Archive:
    """Read-only view of one Hxv4-xp3 archive."""

    def __init__(self, path: str, profile: Hxv4Profile):
        self.path = path
        self.profile = profile
        # 用 mmap：启动档案 1.7~1.9 GB，整份读进内存要几十秒且占 GB 级 RAM；
        # mmap 只在真正访问某条 entry 时触发分页，语义与 bytes 一致（都支持切片/unpack_from）。
        self._fp = open(path, "rb")
        try:
            self.data = mmap.mmap(self._fp.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):  # 空文件/不支持 mmap 的载体
            self.data = self._fp.read()
        self.entries: list[Entry] = []
        self.open_flag = 0
        self.records: list[Record] = []
        self._parse()

    def close(self):
        if isinstance(self.data, mmap.mmap):
            self.data.close()
            self.data = b""
        if getattr(self, "_fp", None):
            self._fp.close()
            self._fp = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- container
    def _parse(self):
        d = self.data
        if d[:11] != XP3_SIG:
            raise ValueError("not an xp3 archive")
        index_offset = struct.unpack_from("<Q", d, 0x0B)[0]
        if struct.unpack_from("<I", d, 0x17)[0] == 0x80:  # hxv4 variant: real offset at 0x20
            index_offset = struct.unpack_from("<Q", d, 0x20)[0]
        comp = d[index_offset]
        if comp == 1:
            zsize, fsize = struct.unpack_from("<QQ", d, index_offset + 1)
            index = zlib.decompress(d[index_offset + 17:index_offset + 17 + zsize])
        else:
            fsize = struct.unpack_from("<Q", d, index_offset + 1)[0]
            index = d[index_offset + 9:index_offset + 9 + fsize]
        self._parse_index(index)

    def _parse_index(self, index: bytes):
        pos = 0
        hvx4 = None
        idx = 0
        while pos + 12 <= len(index):
            sig = index[pos:pos + 4]
            if sig == b"\xff\xff\xff\xff" or sig[0] == 0xFF:
                break
            size = struct.unpack_from("<q", index, pos + 4)[0]
            body = index[pos + 12:pos + 12 + size]
            if sig == b"Hxv4":
                hvx4 = struct.unpack_from("<qIH", body, 0)
            elif sig == b"File":
                self.entries.append(self._parse_file(idx, body))
                idx += 1
            pos += 12 + size
        if hvx4 is None:
            raise ValueError("no Hxv4 descriptor: archive is not encrypted this way")
        offset, fsize, flags = hvx4
        self.open_flag = flags & 1
        payload = self.data[offset:offset + fsize]
        nonce = self.profile.nonce1 if self.open_flag else self.profile.nonce0
        plain = xchacha_open(self.profile.key, nonce, payload)
        if plain is None:
            raise ValueError("Hxv4 mapping table tag mismatch: wrong key/nonce for this game")
        length = struct.unpack_from("<I", plain, 0)[0]
        raw = zlib.decompress(plain[4:])
        if len(raw) != length:
            raw = zlib.decompress(plain[4:], bufsize=length)
        self.records = self._parse_mapping(tjs_parse(raw))
        self._bind_records()

    @staticmethod
    def _parse_file(idx: int, body: bytes) -> Entry:
        pos = 0
        info = flags = None
        name = ""
        segms = []
        adler = None
        while pos + 12 <= len(body):
            sig = body[pos:pos + 4]
            size = struct.unpack_from("<q", body, pos + 4)[0]
            chunk = body[pos + 12:pos + 12 + size]
            if sig == b"info":
                flags, fsize = struct.unpack_from("<Iq", chunk, 0)
                namelen = struct.unpack_from("<H", chunk, 20)[0]
                name = chunk[22:22 + namelen * 2].decode("utf-16-le", errors="replace")
            elif sig == b"segm":
                for i in range(0, size, 28):
                    segms.append(struct.unpack_from("<IQQQ", chunk, i))
            elif sig == b"adlr":
                adler = struct.unpack_from("<I", chunk, 0)[0]
            pos += 12 + size
        return Entry(index=idx, name=name, flags=flags or 0, fsize=fsize or 0,
                     segms=segms, adler=adler)

    @staticmethod
    def _parse_mapping(root: TjsValue) -> list[Record]:
        if root.type != "array" or len(root.value) % 2:
            raise ValueError("mapping table root is not a (domain, group) array")
        recs = []
        top = root.value
        for i in range(0, len(top), 2):
            domain, group = top[i], top[i + 1]
            if domain.type != "octet" or len(domain.value) != 8:
                raise ValueError("bad domain hash")
            if group.type != "array":
                raise ValueError("bad record group")
            g = group.value
            for j in range(0, len(g), 2):
                fh, ent = g[j], g[j + 1]
                if fh.type != "octet" or len(fh.value) != 32:
                    raise ValueError("bad file hash")
                if ent.type != "array" or len(ent.value) != 2:
                    raise ValueError("bad record entry")
                recs.append(Record(domain.value, fh.value,
                                   int(ent.value[0].value) & 0xFFFFFFFF,
                                   int(ent.value[1].value) & 0xFFFFFFFFFFFFFFFF))
        return recs

    def _bind_records(self):
        """Attach mapping records to `File` entries.

        Entry 0 is the warning placeholder. Some archives additionally carry a real name
        on entry 1 (literally `startup.tjs`); those hold one more entry than records and
        the records start at entry 1.

        The record order in the mapping is **not** the entry order: the low 16 bits of
        `packed` are the entry index (verified on `data.xp3`, where the permutation is
        heavily shuffled). Bind by that index, never by list position.
        """
        named_second = len(self.entries) > 1 and self.entries[1].name == "startup.tjs"
        offset = 1 if named_second and len(self.entries) == len(self.records) + 1 else 0
        if len(self.entries) != len(self.records) + offset:
            raise ValueError(f"entry/record count mismatch: {len(self.entries)} entries vs "
                             f"{len(self.records)} records (offset {offset})")
        for rec in self.records:
            idx = rec.flag + offset
            if not (offset <= idx < len(self.entries)):
                raise ValueError(f"record entry index out of range: {rec.flag} (+{offset})")
            if self.entries[idx].record is not None:
                raise ValueError(f"duplicate record for entry {idx}")
            self.entries[idx].record = rec
        for entry in self.entries[offset:]:
            if entry.record is None:
                raise ValueError(f"entry {entry.index} has no mapping record")
        self._record_offset = offset

    # -- content
    def read_entry(self, entry: Entry) -> bytes:
        raw = bytearray()
        for segm_flags, offset, fsize, zsize in entry.segms:
            seg = self.data[offset:offset + zsize]
            if segm_flags & 1:
                seg = zlib.decompress(seg)
            raw += seg
        data = bytes(raw)
        if entry.record is not None and entry.index != 0:
            state = FilterState(self.profile.drip.build_filter_state(entry.record.key, self.open_flag))
            data = state.apply(data, 0)
        elif entry.record is not None:
            # entry 0 (warning placeholder) is stored as-is
            pass
        if _is_scrambled(data):
            data = _descramble(data)
        return data
