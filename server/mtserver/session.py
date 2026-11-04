"""An authorized MTProto session: encrypted message framing + dispatch."""
import os
import struct
import time
import zlib
from hashlib import sha256

from telethon.tl import TLObject
from telethon.tl.core.messagecontainer import MessageContainer
from telethon.tl.core.rpcresult import RpcResult
from telethon.tl.core.tlmessage import TLMessage
from telethon.tl.types import MsgsAck, RpcError

from .crypto import auth_key_id, calc_key, ige_decrypt, ige_encrypt, msg_key_of
from .tlmini import parse_object, w_bytes


# ------------------------------------------- manual TL serialization --------
# Telethon's core types (MsgsAck / RpcResult / TLMessage / MessageContainer) do not
# implement __bytes__, so we serialize them by hand per the TL schema (layer 232).
def _vector_long(items) -> bytes:
    return struct.pack("<II", 0x1CB5C415, len(items)) + b"".join(struct.pack("<q", i) for i in items)


def serialize(obj) -> bytes:
    name = type(obj).__name__
    if name == "MsgsAck":
        return struct.pack("<I", 0x62D6B459) + _vector_long(obj.msg_ids)
    if name == "RpcResult":
        if obj.error is not None:
            return struct.pack("<Iq", 0xF35C6D01, obj.req_msg_id) + serialize(obj.error)
        return struct.pack("<Iq", 0xF35C6D01, obj.req_msg_id) + serialize(obj.body)
    if name == "RpcError":
        return struct.pack("<Ii", 0x2144CA19, obj.error_code) + w_bytes(obj.error_message.encode())
    return bytes(obj)  # generated Telethon TL types serialize themselves


def _tl_message(msg_id: int, seq: int, obj) -> bytes:
    b = serialize(obj)
    return struct.pack("<qi", msg_id, seq) + struct.pack("<i", len(b)) + b


def _container(messages) -> bytes:
    # NOTE: in MTProto, msg_container's vector<message> is serialized WITHOUT the
    # 0x1cb5c415 vector ctor — see TL_msg_container::readParams in MTProtoScheme.cpp
    out = struct.pack("<I", 0x73F1F8DC) + struct.pack("<I", len(messages))
    for m in messages:
        out += m
    return out


class Session:
    def __init__(self, server, writer, auth_key: bytes):
        self.server = server
        self.writer = writer
        self.auth_key = auth_key
        self.key_id = auth_key_id(auth_key)
        self.salt = struct.unpack("<q", os.urandom(8))[0]
        self.session_id = struct.unpack("<q", os.urandom(8))[0]
        self.user_id = None
        self._msg_id = (int(time.time()) << 32) | 1
        self._seq = 0
        self.alive = True

    # ------------------------------------------------------------ ids/seq ----
    def new_msg_id(self) -> int:
        self._msg_id += 4
        mid = self._msg_id | 1  # server -> client ids are odd
        self._msg_id = mid
        return mid

    def next_seq(self, content_related: bool) -> int:
        self._seq += 1
        return 2 * self._seq - (1 if content_related else 0)

    # ------------------------------------------------------- encryption ------
    def encrypt(self, body: bytes, content_related: bool = True) -> bytes:
        msg_id = self.new_msg_id()
        seq = self.next_seq(content_related)
        pt = struct.pack("<qqqii", self.salt, self.session_id, msg_id, seq, len(body)) + body
        pad = os.urandom((-(len(pt) + 12)) % 16 + 12)
        mk = msg_key_of(self.auth_key, pt + pad, 8)  # x=8: server -> client
        key, iv = calc_key(self.auth_key, mk, 8)
        ct = ige_encrypt(pt + pad, key, iv)
        frame = self.key_id + mk + ct
        return struct.pack("<i", len(frame)) + frame

    def decrypt(self, payload: bytes):
        if len(payload) < 24:
            raise ValueError("short frame")
        key_id = payload[:8]
        if key_id != self.key_id:
            raise ValueError("bad key id")
        mk = payload[8:24]
        pt = ige_decrypt(payload[24:], *calc_key(self.auth_key, mk, 0))
        if msg_key_of(self.auth_key, pt, 0) != mk:
            raise ValueError("bad msg_key")
        salt, sid, msg_id, seq, ln = struct.unpack("<qqqii", pt[:32])
        body = pt[32:32 + ln]
        return msg_id, seq, body

    # ------------------------------------------------------------ sending ----
    async def send_raw_body(self, body: bytes, content_related: bool = True):
        self.writer.write(self.encrypt(body, content_related))
        await self.writer.drain()

    async def send_obj(self, obj, content_related: bool = True):
        await self.send_raw_body(serialize(obj), content_related)

    async def send_rpc_result(self, req_msg_id: int, result):
        ack = _tl_message(self.new_msg_id(), self.next_seq(False), MsgsAck(msg_ids=[req_msg_id]))
        res = _tl_message(self.new_msg_id(), self.next_seq(True),
                          RpcResult(req_msg_id=req_msg_id, body=result, error=None))
        await self.send_raw_body(_container([ack, res]), True)

    async def send_rpc_error(self, req_msg_id: int, code: int, message: str):
        ack = _tl_message(self.new_msg_id(), self.next_seq(False), MsgsAck(msg_ids=[req_msg_id]))
        res = _tl_message(self.new_msg_id(), self.next_seq(True),
                          RpcResult(req_msg_id=req_msg_id, body=None,
                                    error=RpcError(error_code=code, error_message=message)))
        await self.send_raw_body(_container([ack, res]), True)

    async def push(self, obj: TLObject):
        """Server-initiated update (e.g. new message for another session)."""
        await self.send_obj(obj)

    # --------------------------------------------------------- dispatch ------
    async def handle_body(self, body: bytes, envelope_msg_id: int):
        obj = parse_object(body)
        if isinstance(obj, bytes):  # gzip_packed returns decompressed bytes
            obj = parse_object(obj)
        if isinstance(obj, MessageContainer):
            for m in obj.messages:
                inner = m.obj
                if isinstance(inner, bytes):
                    inner = parse_object(inner)
                if isinstance(inner, MsgsAck):
                    continue
                await self.dispatch(inner, m.msg_id)
            return
        if isinstance(obj, MsgsAck):
            return
        await self.dispatch(obj, envelope_msg_id)

    async def dispatch(self, obj, req_msg_id: int):
        # unwrap invokeWithLayer / initConnection / invokeAfterMsg / gzip
        depth = 0
        while depth < 6:
            depth += 1
            name = type(obj).__name__
            if name == "InvokeWithLayerRequest" or name == "InvokeAfterMsgRequest":
                obj = obj.query
            elif name == "InitConnectionRequest":
                obj = obj.query
            elif isinstance(obj, bytes):
                obj = parse_object(obj)
            else:
                break
        from .api import dispatch
        await dispatch(self, obj, req_msg_id)
