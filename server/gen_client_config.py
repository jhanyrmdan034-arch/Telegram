#!/usr/bin/env python3
"""Print a ready-to-paste CustomBackendConfig.h for the Android app.

Uses the RSA key in server_key.pem (the one the running server uses).

Usage:
    python3 server/gen_client_config.py --public-host chat.example.ir --public-port 8443
    python3 server/gen_client_config.py --public-host 5.6.7.8 --public-port 8443 > TMessagesProj/jni/tgnet/CustomBackendConfig.h
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mtserver.config import ServerConfig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--public-host", required=True, help="IP or domain clients dial (your VPS)")
    ap.add_argument("--public-port", type=int, default=8443)
    ap.add_argument("--dc-id", type=int, default=1)
    ap.add_argument("--key", default=None, help="server RSA private key PEM (default: server/server_key.pem)")
    args = ap.parse_args()

    # load existing key (do NOT regenerate); db path unused here
    cfg = ServerConfig(public_host=args.public_host, public_port=args.public_port,
                       dc_id=args.dc_id, db_path=":memory:", key_file=args.key)
    snippet = cfg.client_header_snippet()
    print(snippet)
    print("// --- paste the block ABOVE into TMessagesProj/jni/tgnet/CustomBackendConfig.h ---",
          file=sys.stderr)


if __name__ == "__main__":
    main()
