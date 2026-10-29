"""SipHash-2-4 / BLAKE2s helpers for Hxv4 domain & file hashes (pure Python)."""
MASK = (1 << 64) - 1

def _rotl(x, b):
    return ((x << b) | (x >> (64 - b))) & MASK

class SipHash24:
    def __init__(self, key: bytes, c_rounds=2, d_rounds=4):
        assert len(key) == 16
        k0 = int.from_bytes(key[0:8], "little")
        k1 = int.from_bytes(key[8:16], "little")
        self.v0 = 0x736f6d6570736575 ^ k0
        self.v1 = 0x646f72616e646f6d ^ k1
        self.v2 = 0x6c7967656e657261 ^ k0
        self.v3 = 0x7465646279746573 ^ k1
        self.buf = b""
        self.length = 0
        self.c, self.d = c_rounds, d_rounds

    def _round(self):
        v0, v1, v2, v3 = self.v0, self.v1, self.v2, self.v3
        v0 = (v0 + v1) & MASK; v1 = _rotl(v1, 13); v1 ^= v0; v0 = _rotl(v0, 32)
        v2 = (v2 + v3) & MASK; v3 = _rotl(v3, 16); v3 ^= v2
        v0 = (v0 + v3) & MASK; v3 = _rotl(v3, 21); v3 ^= v0
        v2 = (v2 + v1) & MASK; v1 = _rotl(v1, 17); v1 ^= v2; v2 = _rotl(v2, 32)
        self.v0, self.v1, self.v2, self.v3 = v0, v1, v2, v3

    def _block(self, m):
        self.v3 ^= m
        for _ in range(self.c):
            self._round()
        self.v0 ^= m

    def update(self, data: bytes):
        data = self.buf + data
        self.length += len(data) - len(self.buf)
        self.buf = b""
        i = 0
        while i + 8 <= len(data):
            self._block(int.from_bytes(data[i:i + 8], "little"))
            i += 8
        self.buf = data[i:]
        return self

    def digest(self):
        b = (self.length & 0xFF) << 56
        tail = self.buf + b"\x00" * (8 - len(self.buf))
        b |= int.from_bytes(tail[:8], "little") & 0x00FFFFFFFFFFFFFF
        self._block(b)
        self.v2 ^= 0xFF
        for _ in range(self.d):
            self._round()
        return (self.v0 ^ self.v1 ^ self.v2 ^ self.v3).to_bytes(8, "little")

    def hexdigest(self):
        return self.digest().hex()


def domain_hash(directory: str, salt: str = "xp3hnp") -> bytes:
    """directory must be lowercase with trailing '/'; root = ''."""
    h = SipHash24(b"\x00" * 16)
    h.update(directory.encode("utf-16-le"))
    h.update(salt.encode("utf-16-le"))
    return h.digest()


def file_hash(name: str, salt: str = "xp3hnp") -> bytes:
    from hashlib import blake2s
    h = blake2s(digest_size=32)
    h.update(name.encode("utf-16-le"))
    h.update(salt.encode("utf-16-le"))
    return h.digest()
