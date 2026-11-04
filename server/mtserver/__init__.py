"""mtserver — a custom, self-hosted MTProto 2.0 backend for the Telegram Android app.

The official Telegram server code is closed-source; this package implements the
server side of the MTProto 2.0 protocol (https://core.telegram.org/mtproto) plus a
subset of the Telegram API (TL layer 232, identical to TLRPC.java in this repo),
so the unmodified app (patched only to point at this server) can run fully on your
own infrastructure.
"""

__version__ = "0.1.0"
