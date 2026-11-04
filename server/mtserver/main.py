"""Custom MTProto backend server — entry point.

Usage:
    python3 -m mtserver.main --listen-port 8443 --public-host 127.0.0.1 --public-port 8443

Then patch the Android app to point at it (see docs/CUSTOM_BACKEND.md).
"""
import argparse
import asyncio
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .config import ServerConfig
from .db import Database
from .handshake import handle_handshake
from .session import Session
from .tlmini import parse_object, serialize_object


class Server:
    def __init__(self, config: ServerConfig):
        self.config = config
        self.db = Database(config.db_path)
        self.sessions = {}          # auth_key_id hex -> Session
        self.user_sessions = {}     # user_id -> set(Session)
        self.pts = 1

    def bump_pts(self) -> int:
        self.pts += 1
        return self.pts

    def register_session(self, s: Session):
        self.sessions[s.key_id.hex()] = s
        if s.user_id is not None:
            self.user_sessions.setdefault(s.user_id, set()).add(s)

    def unregister_session(self, s: Session):
        self.sessions.pop(s.key_id.hex(), None)
        if s.user_id is not None:
            self.user_sessions.get(s.user_id, set()).discard(s)

    def sessions_of(self, user_id: int):
        return list(self.user_sessions.get(user_id, ()))


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    hdr = await reader.readexactly(4)
    (length,) = struct.unpack("<i", hdr)
    if length <= 0 or length > 4 * 1024 * 1024:
        raise ValueError("bad frame length %d" % length)
    return await reader.readexactly(length)


async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, server: Server):
    peer = writer.get_extra_info("peername")
    print("[server] connection from %s" % (peer,), flush=True)
    hs_state = {}
    session = None
    resp_msg_id = (int(asyncio.get_event_loop().time()) << 32) | 1
    try:
        while True:
            payload = await read_frame(reader)
            if payload[:8] == b"\x00" * 8:
                # ---- unencrypted (handshake) message ----
                msg_id, ln = struct.unpack("<qi", payload[8:20])
                body = payload[20:20 + ln]
                obj = parse_object(body)
                resp, auth_key = await handle_handshake(obj, hs_state, server)
                rbody = serialize_object(resp)
                resp_msg_id += 2
                frame = b"\x00" * 8 + struct.pack("<q", resp_msg_id) + struct.pack("<i", len(rbody)) + rbody
                writer.write(struct.pack("<i", len(frame)) + frame)
                await writer.drain()
                if auth_key is not None:
                    session = Session(server, writer, auth_key)
                    server.sessions[session.key_id.hex()] = session
                    print("[server] auth key established with %s" % (peer,), flush=True)
            else:
                # ---- encrypted message ----
                if session is None:
                    print("[server] encrypted data before handshake, closing", flush=True)
                    break
                try:
                    msg_id, seq, body = session.decrypt(payload)
                except ValueError as e:
                    print("[server] decrypt error: %s | got key_id=%s want=%s" % (e, payload[:8].hex(), session.key_id.hex()), flush=True)
                    break
                await session.handle_body(body, msg_id)
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:
        import traceback; traceback.print_exc()
    finally:
        if session is not None:
            server.unregister_session(session)
        writer.close()
        print("[server] connection closed %s" % (peer,), flush=True)


async def amain():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen-host", default="0.0.0.0")
    ap.add_argument("--listen-port", type=int, default=443)
    ap.add_argument("--public-host", default="127.0.0.1", help="address advertised to clients in help.getConfig")
    ap.add_argument("--public-port", type=int, default=None)
    ap.add_argument("--dc-id", type=int, default=1)
    ap.add_argument("--dev-code", default=None, help="fixed SMS code accepted by auth.signIn (dev mode)")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    config = ServerConfig(listen_host=args.listen_host, listen_port=args.listen_port,
                          public_host=args.public_host,
                          public_port=args.public_port or args.listen_port,
                          dc_id=args.dc_id, dev_code=args.dev_code, db_path=args.db)
    server = Server(config)

    print("=" * 70, flush=True)
    print("[server] custom MTProto backend listening on %s:%d" % (args.listen_host, args.listen_port), flush=True)
    print("[server] clients should dial: %s:%d (dc id %d)" % (config.public_host, config.public_port, config.dc_id), flush=True)
    print("[server] RSA fingerprint: 0x%016x" % (config.fingerprint & 0xFFFFFFFFFFFFFFFF), flush=True)
    print("[server] dev SMS code: %s" % (config.dev_code or "(random, see log)"), flush=True)
    print("-" * 70, flush=True)
    print("Paste this into TMessagesProj/jni/tgnet/CustomBackendConfig.h:", flush=True)
    print(config.client_header_snippet(), flush=True)
    print("=" * 70, flush=True)

    srv = await asyncio.start_server(lambda r, w: handle_connection(r, w, server),
                                     args.listen_host, args.listen_port)
    async with srv:
        await srv.serve_forever()


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
