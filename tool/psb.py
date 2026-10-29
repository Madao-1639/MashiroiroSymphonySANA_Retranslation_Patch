"""PSB (M2 "Packaged Structure Binary") v3 reader / string-level rewriter.

Reference: FreeMote (https://github.com/UlyssesWu/FreeMote), files
  FreeMote.Psb/Psb.cs          (LoadFromStream / Unpack / LoadNames)
  FreeMote.Psb/PsbValues.cs    (PsbArray / PsbNumber / PsbString)
  FreeMote/PsbHeader.cs        (header layout, checksum)

Only what this project needs is implemented:
  * parse the whole object graph (dict / list / int / float / bool / None / str / resource)
  * rewrite the string pool in place (same string count, arbitrary new lengths)
  * MDF shell ("mdf\\0" + u32 raw length + zlib stream) used by this game's *.scn

All integers inside PSB are little-endian.
"""

from __future__ import annotations

import io
import struct
import zlib

PSB_SIG = b"PSB\x00"
MDF_SIG = b"mdf\x00"

# PsbObjType
NONE = 0x00
NULL = 0x01
FALSE = 0x02
TRUE = 0x03
NUMBER_N0 = 0x04
NUMBER_N1 = 0x05
NUMBER_N8 = 0x0C
ARRAY_N1 = 0x0D
ARRAY_N8 = 0x14
STRING_N1 = 0x15
STRING_N4 = 0x18
RESOURCE_N1 = 0x19
RESOURCE_N4 = 0x1C
FLOAT0 = 0x1D
FLOAT = 0x1E
DOUBLE = 0x1F
LIST = 0x20
OBJECTS = 0x21
EXTRA_CHUNK_N1 = 0x22
EXTRA_CHUNK_N4 = 0x25


class PsbError(Exception):
    pass


class _Root:
    """Sentinel: "walk from the root" (distinct from a real `None` node)."""


_ROOT = _Root()


class PsbString:
    """A string slot: `index` is the offset-table index, `value` the decoded text."""

    __slots__ = ("index", "value")

    def __init__(self, index: int, value: str | None = None):
        self.index = index
        self.value = value

    def __repr__(self):
        return f"PsbString(#{self.index}, {self.value!r})"

    def __eq__(self, other):
        return isinstance(other, PsbString) and self.index == other.index

    def __hash__(self):
        return hash(("psbstr", self.index))


class PsbResource:
    __slots__ = ("index", "is_extra", "data")

    def __init__(self, index: int, is_extra: bool = False):
        self.index = index
        self.is_extra = is_extra
        self.data = None

    def __repr__(self):
        return f"PsbResource(#{self.index}{'x' if self.is_extra else ''})"


def _read_le(br: io.BytesIO, n: int) -> int:
    b = br.read(n)
    if len(b) != n:
        raise PsbError("unexpected EOF")
    return int.from_bytes(b, "little")


def _read_array(br: io.BytesIO) -> list[int]:
    """PsbArray: type byte (ArrayN1..N8) -> count bytes, entry-type byte -> entry width."""
    t = br.read(1)
    if not t:
        raise PsbError("unexpected EOF in array")
    t = t[0]
    if not (ARRAY_N1 <= t <= ARRAY_N8):
        raise PsbError(f"not an array type byte: 0x{t:02x} at 0x{br.tell() - 1:x}")
    count = _read_le(br, t - ARRAY_N1 + 1)
    entry_type = br.read(1)[0]
    entry_len = entry_type - NUMBER_N8
    if entry_len == 0 and count == 0:
        return []
    if not (1 <= entry_len <= 8):
        raise PsbError(f"bad array entry width {entry_len}")
    buf = br.read(entry_len * count)
    if len(buf) != entry_len * count:
        raise PsbError("unexpected EOF in array body")
    return [int.from_bytes(buf[i * entry_len:(i + 1) * entry_len], "little") for i in range(count)]


def _array_type(count: int) -> tuple[int, int]:
    """Return (type byte, count-width) for a count value."""
    for width in range(1, 9):
        if count < (1 << (8 * width)):
            return ARRAY_N1 + width - 1, width
    raise PsbError("array too long")


def _read_array_style(br: io.BytesIO) -> tuple[list[int], int, int]:
    """Like _read_array but also reports (count_width, entry_width)."""
    t = br.read(1)[0]
    if not (ARRAY_N1 <= t <= ARRAY_N8):
        raise PsbError(f"not an array type byte: 0x{t:02x}")
    count_width = t - ARRAY_N1 + 1
    count = _read_le(br, count_width)
    entry_width = br.read(1)[0] - NUMBER_N8
    if entry_width == 0 and count == 0:
        return [], count_width, 0
    if not (1 <= entry_width <= 8):
        raise PsbError(f"bad array entry width {entry_width}")
    buf = br.read(entry_width * count)
    if len(buf) != entry_width * count:
        raise PsbError("unexpected EOF in array body")
    return ([int.from_bytes(buf[i * entry_width:(i + 1) * entry_width], "little") for i in range(count)],
            count_width, entry_width)


def _pack_array_styled(values: list[int], count_width: int | None = None,
                       entry_width: int | None = None) -> bytes:
    """Encode a PsbArray, keeping the original field widths when they still fit."""
    needed_entry = 0
    for v in values:
        for w in range(1, 9):
            if v < (1 << (8 * w)):
                needed_entry = max(needed_entry, w)
                break
    if entry_width is not None:
        needed_entry = max(needed_entry, entry_width)
    needed_count = 1
    for w in range(1, 9):
        if len(values) < (1 << (8 * w)):
            needed_count = w
            break
    if count_width is not None:
        needed_count = max(needed_count, count_width)
    out = bytearray([ARRAY_N1 + needed_count - 1])
    out += len(values).to_bytes(needed_count, "little")
    out.append(NUMBER_N8 + needed_entry)
    for v in values:
        out += v.to_bytes(needed_entry, "little")
    return bytes(out)


def _pack_array(values: list[int]) -> bytes:
    if not values:
        return bytes([ARRAY_N1, 0, NUMBER_N1])
    width = 1
    for v in values:
        for w in range(1, 9):
            if v < (1 << (8 * w)):
                width = max(width, w)
                break
    type_byte, count_width = _array_type(len(values))
    out = bytearray([type_byte])
    out += len(values).to_bytes(count_width, "little")
    out.append(NUMBER_N8 + width)
    for v in values:
        out += v.to_bytes(width, "little")
    return bytes(out)


class PsbFile:
    """Parsed PSB (optionally wrapped in an MDF shell)."""

    def __init__(self, data: bytes):
        self.raw = data
        self.is_mdf = data[:3].lower() == b"mdf"
        body = data
        if self.is_mdf:
            self.mdf_length = struct.unpack_from("<I", data, 4)[0]
            body = zlib.decompress(data[8:])
            if len(body) != self.mdf_length:
                raise PsbError(f"mdf length mismatch: {len(body)} != {self.mdf_length}")
        self.body = body
        self.int_slots: list[tuple[str, int, int, int]] = []
        self.str_slots: list[tuple[str, int, int, int]] = []
        self._record_ints = False
        self._parse()

    # ---------------------------------------------------------------- parse
    def _parse(self):
        body = self.body
        if body[:4] != PSB_SIG:
            raise PsbError(f"not a PSB: {body[:4]!r}")
        self.version = struct.unpack_from("<H", body, 4)[0]
        self.header_encrypt = struct.unpack_from("<H", body, 6)[0]
        if self.header_encrypt:
            raise PsbError("encrypted header is not supported")
        if self.version != 3:
            raise PsbError(f"unsupported PSB version {self.version}")
        (self.header_length, self.offset_names, self.offset_strings,
         self.offset_strings_data, self.offset_chunk_offsets, self.offset_chunk_lengths,
         self.offset_chunk_data, self.offset_entries, self.checksum) = struct.unpack_from("<9I", body, 8)

        br = io.BytesIO(body)
        # --- names (charset / trie data / trie roots)
        br.seek(self.offset_names)
        self.charset = _read_array(br)
        self.names_data = _read_array(br)
        self.name_indexes = _read_array(br)
        self.names = [self._decode_name(i) for i in self.name_indexes]

        # --- string offset table
        br.seek(self.offset_strings)
        self.string_offsets, self._str_count_width, self._str_entry_width = _read_array_style(br)
        self.strings: dict[int, PsbString] = {}
        self.string_order: list[int] = []

        # --- resources
        br.seek(self.offset_chunk_offsets)
        self.chunk_offsets = _read_array(br)
        br.seek(self.offset_chunk_lengths)
        self.chunk_lengths = _read_array(br)
        self.resources: dict[object, PsbResource] = {}

        # --- object graph
        br.seek(self.offset_entries)
        self.root = self._unpack(br)

    def _note_int(self, path, pos, width, value):
        if self._record_ints:
            self.int_slots.append((path, pos, width, value))

    def _note_str(self, path, pos, width, index):
        if self._record_ints:
            self.str_slots.append((path, pos, width, index))

    def record_slots(self, prefix: str = "$"):
        """One traversal -> (int_slots, str_slots), both filtered by `prefix`.

        Use this instead of calling `find_ints` and `find_str_refs` one after the other:
        each of those re-walks the whole object graph (with a path string per node), which
        dominates the cost of a write-back run.
        """
        self.int_slots = []
        self.str_slots = []
        self._record_ints = True
        br = io.BytesIO(self.body)
        br.seek(self.offset_entries)
        try:
            self._unpack(br, "$")
        finally:
            self._record_ints = False
        ints = [s for s in self.int_slots if s[0].startswith(prefix)]
        strs = [s for s in self.str_slots if s[0].startswith(prefix)]
        return ints, strs

    def find_ints(self, prefix: str = "$") -> list[tuple[str, int, int, int]]:
        """[(path, byte_pos, byte_width, value)] for every integer in the graph.

        The positions are absolute offsets inside `body`; the type byte sits at `byte_pos`
        and the little-endian value follows it (`byte_width` bytes, 0 for the value 0 type).
        """
        self.int_slots = []
        self.str_slots = []
        self._record_ints = True
        br = io.BytesIO(self.body)
        br.seek(self.offset_entries)
        try:
            self._unpack(br, "$")
        finally:
            self._record_ints = False
        return [slot for slot in self.int_slots if slot[0].startswith(prefix)]

    def find_str_refs(self, prefix: str = "$") -> list[tuple[str, int, int, int]]:
        """[(path, byte_pos, byte_width, string_index)] for every string *reference*.

        Same shape as `find_ints`; lets a caller re-point one reference at another slot
        provided the new index fits the existing inline width.
        """
        if not self._record_ints:
            self.find_ints(prefix)
        return [slot for slot in self.str_slots if slot[0].startswith(prefix)]

    def _decode_name(self, root_idx: int) -> str:
        out = []
        node = self.names_data[root_idx]
        for _ in range(4096):
            if node == 0:
                break
            nxt = self.names_data[node]
            delta = self.charset[nxt]
            out.append((node - delta) & 0xFF)
            node = nxt
        else:
            raise PsbError("name trie loop")
        out.reverse()
        return bytes(out).decode("utf-8", errors="replace")

    def get_string(self, index: int) -> PsbString:
        """Resolve a string slot by index (cached)."""
        s = self.strings.get(index)
        if s is not None:
            return s
        start = self.offset_strings_data + self.string_offsets[index]
        end = self.body.index(b"\x00", start)
        s = PsbString(index, self.body[start:end].decode("utf-8", errors="replace"))
        self.strings[index] = s
        self.string_order.append(index)
        return s

    def string_value(self, index: int) -> str:
        return self.get_string(index).value

    def _unpack(self, br: io.BytesIO, path: str = "$"):
        pos = br.tell()
        t = br.read(1)
        if not t:
            raise PsbError("unexpected EOF in object")
        t = t[0]
        if t in (NONE, NULL):
            return None
        if t == FALSE:
            return False
        if t == TRUE:
            return True
        if t == NUMBER_N0:
            self._note_int(path, pos, 0, 0)
            return 0
        if NUMBER_N1 <= t <= NUMBER_N8:
            width = t - NUMBER_N0
            value = _read_le(br, width)
            self._note_int(path, pos, width, value)
            return value
        if ARRAY_N1 <= t <= ARRAY_N8:
            br.seek(pos)
            return _read_array(br)
        if STRING_N1 <= t <= STRING_N4:
            width = t - STRING_N1 + 1
            index = _read_le(br, width)
            self._note_str(path, pos, width, index)
            return self.get_string(index)
        if RESOURCE_N1 <= t <= RESOURCE_N4:
            idx = _read_le(br, t - RESOURCE_N1 + 1)
            res = self.resources.get(idx)
            if res is None:
                res = self.resources[idx] = PsbResource(idx)
            return res
        if t == FLOAT0:
            return 0.0
        if t == FLOAT:
            return struct.unpack("<f", br.read(4))[0]
        if t == DOUBLE:
            return struct.unpack("<d", br.read(8))[0]
        if t == LIST:
            offsets = _read_array(br)
            base = br.tell()
            out = []
            for i, off in enumerate(offsets):
                br.seek(base + off)
                out.append(self._unpack(br, f"{path}[{i}]"))
            br.seek(base)
            return out
        if t == OBJECTS:
            name_refs = _read_array(br)
            offsets = _read_array(br)
            base = br.tell()
            out = {}
            for i, nref in enumerate(name_refs):
                br.seek(base + offsets[i])
                key = self.names[nref]
                out[key] = self._unpack(br, f"{path}.{key}")
            br.seek(base)
            return out
        if EXTRA_CHUNK_N1 <= t <= EXTRA_CHUNK_N4:
            idx = _read_le(br, t - EXTRA_CHUNK_N1 + 1)
            key = ("x", idx)
            res = self.resources.get(key)
            if res is None:
                res = self.resources[key] = PsbResource(idx, True)
            return res
        raise PsbError(f"unknown object type 0x{t:02x} at 0x{pos:x}")

    # ---------------------------------------------------------------- write
    def rebuild(self, new_strings: dict[int, str] | None = None,
                extra_strings: list[str] | None = None, body: bytes | None = None) -> bytes:
        """Re-emit the file, replacing the given slots and optionally appending new ones.

        Everything except the string pool is copied verbatim, so the object graph,
        name trie and resource chunks stay byte-identical. Appended slots take the indexes
        `len(string_offsets) + k`; a caller that wants to use them must re-point the
        referencing inline index in place (same byte width) before calling rebuild.
        """
        new_strings = new_strings or {}
        extra_strings = extra_strings or []
        # `body` lets a caller hand over a copy whose graph was already patched in place
        # (e.g. re-pointed string references); the layout is unchanged, only values differ.
        src = self.body if body is None else body
        count = len(self.string_offsets)
        data = bytearray()
        offsets = []
        for i in range(count + len(extra_strings)):
            offsets.append(len(data))
            if i >= count:
                raw = extra_strings[i - count].encode("utf-8")
                data += raw + b"\x00"
                continue
            value = new_strings.get(i)
            if value is None:
                start = self.offset_strings_data + self.string_offsets[i]
                end = self.body.index(b"\x00", start)
                raw = self.body[start:end]
            else:
                raw = value.encode("utf-8")
            data += raw + b"\x00"

        offsets_blob = _pack_array_styled(offsets, self._str_count_width, self._str_entry_width)
        header = bytearray(src[:self.header_length])
        names_region = src[self.header_length:self.offset_strings]
        tail = src[self.offset_chunk_offsets:]

        new_offsets_strings_data = self.offset_strings + len(offsets_blob)
        shift = (len(offsets_blob) - (self.offset_strings_data - self.offset_strings)) + \
                (len(data) - (self.offset_chunk_offsets - self.offset_strings_data))

        # `offset_entries` points *inside* the names region (before the string tables), so it
        # must NOT be shifted -- shifting it silently corrupted every grown file while the
        # zero-change round-trip (shift == 0) still looked perfect.
        if self.offset_entries >= self.offset_strings:
            raise PsbError("unexpected layout: object graph is not inside the names region")
        vals = [self.header_length, self.offset_names, self.offset_strings,
                new_offsets_strings_data,
                self.offset_chunk_offsets + shift, self.offset_chunk_lengths + shift,
                self.offset_chunk_data + shift, self.offset_entries]
        struct.pack_into("<8I", header, 8, *vals)
        checksum = zlib.adler32(bytes(header[8:8 + 32])) & 0xFFFFFFFF
        struct.pack_into("<I", header, 0x28, checksum)

        body = bytes(header) + names_region + offsets_blob + bytes(data) + tail
        if self.is_mdf:
            return MDF_SIG + struct.pack("<I", len(body)) + zlib.compress(body, 9)
        return body

    def patch_ints(self, edits) -> bytes:
        """Rewrite inline integer / string-index slots in place (same byte width).

        `edits` = iterable of (byte_pos, byte_width, new_value) for an integer slot, or
        (byte_pos, byte_width, new_value, "str") for a string-reference slot. Raises when the
        new value does not fit the existing width -- growing it would move every following
        offset and would need a full object-graph serialiser.
        """
        body = bytearray(self.body)
        for edit in edits:
            pos, width, value = edit[0], edit[1], edit[2]
            kind = edit[3] if len(edit) > 3 else "int"
            if kind == "str":
                if width == 0:
                    raise PsbError(f"string slot at 0x{pos:x} has no inline width")
                if not 0 <= value < (1 << (8 * width)):
                    raise PsbError(f"string index {value} does not fit {width} byte(s) "
                                   f"at 0x{pos:x}")
                body[pos] = STRING_N1 + width - 1
            else:
                if width == 0:
                    if value != 0:
                        raise PsbError(f"slot at 0x{pos:x} is the constant 0, cannot store {value}")
                    continue
                if not 0 <= value < (1 << (8 * width)):
                    raise PsbError(f"value {value} does not fit {width} byte(s) at 0x{pos:x}")
                body[pos] = NUMBER_N0 + width
            body[pos + 1:pos + 1 + width] = value.to_bytes(width, "little")
        return bytes(body)

    # ---------------------------------------------------------------- helpers
    def iter_strings(self, node=_ROOT, path: str = "$"):
        """Yield (path, PsbString) for every string slot in the graph.

        Iterative on purpose: a `node=None` default would collide with the many
        real `null` values inside the graph and silently restart the walk from
        the root, which recurses until the stack dies.
        """
        if node is _ROOT:
            node = self.root
        stack = [(node, path)]
        while stack:
            cur, cur_path = stack.pop()
            if isinstance(cur, PsbString):
                yield cur_path, cur
            elif isinstance(cur, dict):
                for k, v in reversed(list(cur.items())):
                    stack.append((v, f"{cur_path}.{k}"))
            elif isinstance(cur, list):
                for i in range(len(cur) - 1, -1, -1):
                    stack.append((cur[i], f"{cur_path}[{i}]"))


def load(path: str) -> PsbFile:
    with open(path, "rb") as fp:
        return PsbFile(fp.read())
