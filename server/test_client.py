"""End-to-end test: two MTProto clients (A and B) against our own server.

Simulates exactly what the patched Android app does:
  DH handshake -> auth.sendCode -> auth.signIn -> help.getConfig ->
  messages.sendMessage (A->B) -> push received by B -> messages.getHistory / getDialogs (A)

Run:  ./venv/bin/python server/test_client.py [--host 127.0.0.1] [--port 8443] [--code 12345]
"""
import argparse
import asyncio
import os
import struct
import sys
import time
from hashlib import sha1

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telethon.tl import types as T
from telethon.tl import functions as F
from telethon.tl.core.messagecontainer import MessageContainer
from telethon.tl.core.rpcresult import RpcResult

from mtserver.crypto import (RSAKey, calc_key, calc_new_nonce_hash, factorize,
                             generate_key_data_from_nonce, get_byte_array,
                             ige_decrypt, ige_encrypt, msg_key_of, auth_key_id)
from mtserver.tlmini import parse_object

LAYER = 232


class Client:
    def __init__(self, rsa: RSAKey, host, port):
        self.rsa = rsa
        self.host, self.port = host, port
        self.auth_key = None
        self.key_id = None
        self.salt = 0
        self.session_id = struct.unpack("<q", os.urandom(8))[0]
        self._msg_id = (int(time.time()) << 32)
        self._seq = 0
        self.pushes = []

    # ---------------------------------------------------------- transport ---
    async def _send_raw(self, payload: bytes):
        self.writer.write(struct.pack("<i", len(payload)) + payload)
        await self.writer.drain()

    async def _recv_raw(self) -> bytes:
        hdr = await self.reader.readexactly(4)
        (ln,) = struct.unpack("<i", hdr)
        return await self.reader.readexactly(ln)

    def _next_msg_id(self):
        self._msg_id += 4
        return self._msg_id

    # ---------------------------------------------------------- handshake ---
    async def connect_and_handshake(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

        async def rpc_plain(obj):
            mid = self._next_msg_id()
            body = bytes(obj)
            await self._send_raw(b"\x00" * 8 + struct.pack("<qi", mid, len(body)) + body)
            payload = await self._recv_raw()
            assert payload[:8] == b"\x00" * 8, "expected unencrypted reply"
            _, ln = struct.unpack("<qi", payload[8:20])
            return parse_object(payload[20:20 + ln])

        nonce = int.from_bytes(os.urandom(16), "little", signed=True)
        res_pq = await rpc_plain(F.ReqPqMultiRequest(nonce=nonce))
        assert isinstance(res_pq, T.ResPQ)
        assert res_pq.server_public_key_fingerprints == [self.rsa.fingerprint], \
            "server advertised wrong fingerprint: %r" % res_pq.server_public_key_fingerprints

        pq = int.from_bytes(res_pq.pq, "big")
        p, q = factorize(pq)
        new_nonce = int.from_bytes(os.urandom(32), "little", signed=True)
        inner = bytes(T.PQInnerData(pq=res_pq.pq, p=get_byte_array(p), q=get_byte_array(q),
                                     nonce=res_pq.nonce, server_nonce=res_pq.server_nonce,
                                     new_nonce=new_nonce))
        # MTProto 2.0 style data-with-hash (SHA-256), like the Android app
        data = sha1(inner).digest() + inner + os.urandom(235 - len(inner))
        ct = self.rsa.encrypt_raw(data)
        dh = await rpc_plain(F.ReqDHParamsRequest(
            nonce=res_pq.nonce, server_nonce=res_pq.server_nonce,
            p=get_byte_array(p), q=get_byte_array(q),
            public_key_fingerprint=res_pq.server_public_key_fingerprints[0],
            encrypted_data=ct))
        assert isinstance(dh, T.ServerDHParamsOk), dh
        key, iv = generate_key_data_from_nonce(res_pq.server_nonce, new_nonce)
        answer = ige_decrypt(dh.encrypted_answer, key, iv)
        inner2 = parse_object(answer[20:])
        assert isinstance(inner2, T.ServerDHInnerData)
        dh_prime = int.from_bytes(inner2.dh_prime, "big")
        g, g_a = inner2.g, int.from_bytes(inner2.g_a, "big")
        b = int.from_bytes(os.urandom(256), "big")
        g_b = pow(g, b, dh_prime)
        auth_key = get_byte_array(pow(g_a, b, dh_prime))
        if len(auth_key) < 256:
            auth_key = b"\x00" * (256 - len(auth_key)) + auth_key
        self.auth_key = auth_key
        self.key_id = auth_key_id(auth_key)

        cinner = bytes(T.ClientDHInnerData(nonce=res_pq.nonce, server_nonce=res_pq.server_nonce,
                                            retry_id=0, g_b=get_byte_array(g_b)))
        cdata = sha1(cinner).digest() + cinner
        enc = ige_encrypt(cdata, key, iv)
        gen = await rpc_plain(F.SetClientDHParamsRequest(
            nonce=res_pq.nonce, server_nonce=res_pq.server_nonce, encrypted_data=enc))
        assert isinstance(gen, T.DhGenOk), gen
        assert gen.new_nonce_hash1 == calc_new_nonce_hash(auth_key, new_nonce, 1), "new_nonce_hash mismatch"
        print("[client] handshake OK, key_id=%s" % self.key_id.hex())

    # ----------------------------------------------------- encrypted rpc -----
    def _encrypt(self, body: bytes, content_related=True) -> bytes:
        mid = self._next_msg_id()
        self._seq += 1
        seq = 2 * self._seq - (1 if content_related else 0)
        pt = struct.pack("<qqqii", self.salt, self.session_id, mid, seq, len(body)) + body
        pad = os.urandom((-(len(pt) + 12)) % 16 + 12)
        mk = msg_key_of(self.auth_key, pt + pad, 0)  # x=0 client -> server
        k, iv = calc_key(self.auth_key, mk, 0)
        ct = ige_encrypt(pt + pad, k, iv)
        frame = self.key_id + mk + ct
        return struct.pack("<i", len(frame)) + frame, mid

    def _decrypt(self, payload: bytes):
        assert payload[:8] == self.key_id
        mk = payload[8:24]
        pt = ige_decrypt(payload[24:], *calc_key(self.auth_key, mk, 8))
        assert msg_key_of(self.auth_key, pt, 8) == mk, "bad server msg_key"
        salt, sid, mid, seq, ln = struct.unpack("<qqqii", pt[:32])
        return mid, pt[32:32 + ln]

    async def _recv_obj(self):
        payload = await self._recv_raw()
        mid, body = self._decrypt(payload)
        obj = parse_object(body)
        if isinstance(obj, bytes):
            obj = parse_object(obj)
        if isinstance(obj, MessageContainer):
            return [m.obj for m in obj.messages]
        return [obj]

    async def rpc(self, query, timeout=10.0):
        """Send a request (wrapped like the app does) and wait for its rpc_result."""
        wrapped = F.InvokeWithLayerRequest(
            layer=LAYER,
            query=F.InitConnectionRequest(
                api_id=6, device_model="TestClient", system_version="1.0",
                app_version="1.0", system_lang_code="en", lang_pack="", lang_code="en",
                query=query))
        body = bytes(wrapped)
        # _encrypt already prepends the 4-byte frame length; write it directly.
        # It returns (frame, transport_msg_id); rpc_result must echo the transport id.
        frame, mid = self._encrypt(body)
        self._pending = mid
        self.writer.write(frame)
        await self.writer.drain()
        deadline = time.time() + timeout
        while time.time() < deadline:
            objs = await asyncio.wait_for(self._recv_obj(), timeout=deadline - time.time())
            for o in objs:
                if isinstance(o, RpcResult) and o.req_msg_id == mid:
                    if o.error is not None:
                        raise RuntimeError("rpc error %d: %s" % (o.error.error_code, o.error.error_message))
                    body = o.body
                    if isinstance(body, bytes):  # telethon returns the raw rpc_result body
                        body = parse_object(body)
                    return body
                self.pushes.append(o)
        raise TimeoutError("no rpc_result for %d" % mid)

    async def wait_push(self, pred, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for p in list(self.pushes):
                if pred(p):
                    self.pushes.remove(p)
                    return p
            objs = await asyncio.wait_for(self._recv_obj(), timeout=max(0.1, deadline - time.time()))
            for o in objs:
                if isinstance(o, RpcResult):
                    self.pushes.append(o)
                else:
                    self.pushes.append(o)
        raise TimeoutError("no matching push")

    async def close(self):
        self.writer.close()


def peer_user(uid):
    return T.InputPeerUser(user_id=uid, access_hash=0)


async def scenario(host, port, code):
    from mtserver.config import KEY_FILE
    with open(KEY_FILE, "rb") as f:
        rsa = RSAKey.load(f.read())

    a = Client(rsa, host, port)
    await a.connect_and_handshake()
    cfg = await a.rpc(F.help.GetConfigRequest())
    print("[A] help.getConfig: this_dc=%d dc_options=%s" % (cfg.this_dc, [(d.id, d.ip_address, d.port) for d in cfg.dc_options]))
    assert cfg.dc_options[0].ip_address == host and cfg.dc_options[0].port == port

    sent = await a.rpc(F.auth.SendCodeRequest(phone_number="+1001", api_id=6, api_hash="x", settings=T.CodeSettings()))
    print("[A] auth.sendCode -> hash=%s" % sent.phone_code_hash[:12])
    authz = await a.rpc(F.auth.SignInRequest(phone_number="+1001", phone_code_hash=sent.phone_code_hash, phone_code=code))
    a_id = authz.user.id
    print("[A] auth.signIn OK, user id=%d" % a_id)

    b = Client(rsa, host, port)
    await b.connect_and_handshake()
    sent_b = await b.rpc(F.auth.SendCodeRequest(phone_number="+1002", api_id=6, api_hash="x", settings=T.CodeSettings()))
    authz_b = await b.rpc(F.auth.SignInRequest(phone_number="+1002", phone_code_hash=sent_b.phone_code_hash, phone_code=code))
    b_id = authz_b.user.id
    print("[B] signed in, user id=%d" % b_id)

    # A sends a message to B
    ups = await a.rpc(F.messages.SendMessageRequest(peer=peer_user(b_id), message="salam az server-e ma! 🚀", random_id=12345))
    print("[A] messages.sendMessage -> %d update(s)" % len(ups.updates))

    # B should receive a push
    push = await b.wait_push(lambda o: isinstance(o, T.Updates) and any(isinstance(u, T.UpdateNewMessage) for u in o.updates))
    msg = [u for u in push.updates if isinstance(u, T.UpdateNewMessage)][0].message
    print("[B] push received: from=%s text=%r" % (msg.from_id.user_id, msg.message))
    assert msg.message == "salam az server-e ma! 🚀"

    # A reads history
    hist = await a.rpc(F.messages.GetHistoryRequest(peer=peer_user(b_id), offset_id=0, offset_date=None,
                                                     add_offset=0, limit=20, max_id=0, min_id=0, hash=0))
    print("[A] getHistory: %d message(s): %r" % (len(hist.messages), [m.message for m in hist.messages]))
    assert any(m.message == "salam az server-e ma! 🚀" for m in hist.messages)

    # --- photo: upload in parts -> sendMedia -> push -> history -> download ---
    photo_bytes = b"\xff\xd8\xff" + os.urandom(200000) + b"\xff\xd9"  # fake JPEG, ~200 KB
    file_id = int.from_bytes(os.urandom(8), "little", signed=True)
    part_size = 1024 * 1024
    parts = [photo_bytes[i:i + part_size] for i in range(0, len(photo_bytes), part_size)]
    for idx, p in enumerate(parts):
        await a.rpc(F.upload.SaveFilePartRequest(file_id=file_id, file_part=idx, bytes=p))
    print("[A] upload.saveFilePart x%d (%d bytes uploaded)" % (len(parts), len(photo_bytes)))
    media = T.InputMediaUploadedPhoto(file=T.InputFile(id=file_id, parts=len(parts), name="photo.jpg", md5_checksum=""))
    ups2 = await a.rpc(F.messages.SendMediaRequest(peer=peer_user(b_id), media=media, message="ax! 📷", random_id=999))
    print("[A] messages.sendMedia(photo) -> %d update(s)" % len(ups2.updates))
    push2 = await b.wait_push(lambda o: isinstance(o, T.Updates) and any(
        isinstance(u, T.UpdateNewMessage) and getattr(u.message, "media", None) is not None for u in o.updates))
    m2 = [u for u in push2.updates if isinstance(u, T.UpdateNewMessage)][0].message
    photo = m2.media.photo
    print("[B] push photo received: caption=%r size=%d dc=%d" % (m2.message, photo.sizes[0].size, photo.dc_id))
    assert photo.sizes[0].size == len(photo_bytes)
    got = b""
    off = 0
    while True:
        chunk = await b.rpc(F.upload.GetFileRequest(
            location=T.InputPhotoFileLocation(id=photo.id, access_hash=photo.access_hash,
                                              file_reference=photo.file_reference, thumb_size="x"),
            offset=off, limit=1024 * 1024))
        got += chunk.bytes
        if len(chunk.bytes) < 1024 * 1024:
            break
        off += len(chunk.bytes)
    print("[B] upload.getFile: downloaded %d bytes, content match=%s" % (len(got), got == photo_bytes))
    assert got == photo_bytes
    hist2 = await a.rpc(F.messages.GetHistoryRequest(peer=peer_user(b_id), offset_id=0, offset_date=None,
                                                      add_offset=0, limit=20, max_id=0, min_id=0, hash=0))
    assert any(getattr(m, "media", None) is not None for m in hist2.messages)
    print("[A] getHistory contains the photo message ✓")

    # A lists dialogs
    dlgs = await a.rpc(F.messages.GetDialogsRequest(offset_date=None, offset_id=0, offset_peer=T.InputPeerEmpty(), limit=20, hash=0))
    print("[A] getDialogs: %d dialog(s)" % len(dlgs.dialogs))
    assert len(dlgs.dialogs) >= 1

    st = await a.rpc(F.updates.GetStateRequest())
    print("[A] updates.getState: pts=%d" % st.pts)

    await a.close()
    await b.close()
    print("\nALL TESTS PASSED ✅")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--code", default="12345")
    args = ap.parse_args()
    asyncio.run(scenario(args.host, args.port, args.code))


if __name__ == "__main__":
    main()
