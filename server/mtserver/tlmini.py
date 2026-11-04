"""Minimal raw TL reader/writer helpers for the low-level handshake messages.

For full API objects we use Telethon's generated TL layer (identical schema to this
repo's TLRPC.java, layer 232). These helpers are only for handshake payloads where
we need byte-level control (hash-prefix detection etc.).
"""
import struct


class TLReader:
    def __init__(self, data: bytes, offset: int = 0):
        self.b = data
        self.o = offset

    def read(self, n: int) -> bytes:
        r = self.b[self.o:self.o + n]
        if len(r) < n:
            raise ValueError("TLReader: out of bytes")
        self.o += n
        return r

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.read(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.read(8))[0]

    def raw(self, n: int) -> bytes:
        return self.read(n)

    def tbytes(self) -> bytes:
        """Read a TL 'bytes' value, consuming its padding."""
        first = self.read(1)[0]
        if first == 254:
            length = struct.unpack("<I", self.read(3) + b"\x00")[0]
            header = 4
        else:
            length = first
            header = 1
        data = self.read(length)
        pad = (-(length + header)) % 4
        if pad:
            self.read(pad)
        return data


def w_bytes(data: bytes) -> bytes:
    if len(data) < 254:
        pad = (-(len(data) + 1)) % 4
        return bytes([len(data)]) + data + b"\x00" * pad
    pad = (-len(data)) % 4
    return b"\xfe" + struct.pack("<I", len(data))[:3] + data + b"\x00" * pad


def w_vector_long(items) -> bytes:
    out = struct.pack("<II", 0x1CB5C415, len(items))
    for i in items:
        out += struct.pack("<q", i)
    return out


def parse_object(data: bytes):
    """Parse one TL object using Telethon's generated schema (layer 232)."""
    from telethon.extensions import BinaryReader
    with BinaryReader(data) as r:
        return r.tgread_object()


def serialize_object(obj) -> bytes:
    return bytes(obj)
