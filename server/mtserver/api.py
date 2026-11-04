"""Telegram API (TL layer 232) method handlers implemented by our backend.

Each handler: async def handler(ctx, request) -> TLObject (the rpc_result body).
`ctx` is the Session; `ctx.server` is the Server (config/db/sessions).
"""
import hashlib
import os
import time
from datetime import datetime, timezone

from telethon.tl import types as T
from telethon.tl import functions as F
from telethon.tl.types import messages as TM
from telethon.tl.types import auth as TA
from telethon.tl.types import updates as TU
from telethon.tl.types import RpcError

HANDLERS = {}


def handler(*classes):
    def deco(fn):
        for c in classes:
            HANDLERS[c] = fn
        return fn
    return deco


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- helpers ----
def peer_of_input(peer):
    """InputPeer* -> (kind, id)"""
    if isinstance(peer, (T.InputPeerUser, T.PeerUser)):
        return "user", peer.user_id
    if isinstance(peer, T.InputPeerSelf):
        return "user", None  # resolved by caller to session user
    if isinstance(peer, (T.InputPeerChat, T.PeerChat)):
        return "chat", peer.chat_id
    if isinstance(peer, (T.InputPeerChannel, T.PeerChannel)):
        return "channel", peer.channel_id
    raise ValueError("unknown peer %r" % peer)


def peer_obj(kind, pid):
    if kind == "user":
        return T.PeerUser(user_id=pid)
    if kind == "chat":
        return T.PeerChat(chat_id=pid)
    return T.PeerChannel(channel_id=pid)


def input_peer_obj(kind, pid, access_hash=0):
    if kind == "user":
        return T.InputPeerUser(user_id=pid, access_hash=access_hash)
    if kind == "chat":
        return T.InputPeerChat(chat_id=pid)
    return T.InputPeerChannel(channel_id=pid, access_hash=access_hash)


def dialog_key(me: int, kind: str, pid: int) -> str:
    if kind == "user":
        a, b = sorted((me, pid))
        return "d%d_%d" % (a, b)
    if kind == "chat":
        return "c%d" % pid
    return "ch%d" % pid


def user_obj(server, uid: int, self_id=None) -> T.User:
    u = server.db.get_user(uid)
    if u is None:
        return T.User(id=uid, deleted=True)
    return T.User(
        id=u["id"], is_self=(self_id == u["id"]),
        access_hash=u["access_hash"], first_name=u["first_name"],
        username=u["username"] or None, phone=u["phone"],
    )


def message_obj(server, row: dict, viewer: int) -> T.Message:
    dialog = row["dialog"]
    if dialog.startswith("d"):
        a, b = dialog[1:].split("_")
        other = int(b) if int(a) == viewer else int(a)
        peer = T.PeerUser(user_id=other)
        pid = other
    elif dialog.startswith("c"):
        pid = int(dialog[1:])
        peer = T.PeerChat(chat_id=pid)
    else:
        pid = int(dialog[2:])
        peer = T.PeerChannel(channel_id=pid)
    return T.Message(
        id=row["msg_id"], peer_id=peer,
        date=datetime.fromtimestamp(row["date"], timezone.utc),
        message=row["text"], out=(row["from_id"] == viewer),
        from_id=T.PeerUser(user_id=row["from_id"]),
    )


def recipients_of(server, me: int, kind: str, pid: int):
    if kind == "user":
        return [pid] if pid != me else []
    if kind == "chat":
        return [m for m in server.db.chat_members(pid) if m != me]
    if kind == "channel":
        return [m for m in server.db.chat_members(pid) if m != me]
    return []


# ---------------------------------------------------------------- handlers ---
@handler(F.help.GetConfigRequest)
async def get_config(ctx, req):
    cfg = ctx.server.config
    return T.Config(
        date=now(), expires=now(), test_mode=False, this_dc=cfg.dc_id,
        dc_options=[T.DcOption(id=cfg.dc_id, ip_address=cfg.public_host, port=cfg.public_port)],
        dc_txt_domain_name="", chat_size_max=200, megagroup_size_max=200000,
        forwarded_count_max=100, online_update_period_ms=6000, offline_blur_timeout_ms=5000,
        offline_idle_timeout_ms=60000, online_cloud_timeout_ms=10000,
        notify_cloud_delay_ms=500, notify_default_delay_ms=1500,
        push_chat_period_ms=60000, push_chat_limit=2,
        edit_time_limit=172800, revoke_time_limit=604800, revoke_pm_time_limit=86400,
        rating_e_decay=4838400, stickers_recent_limit=30, channels_read_media_period=604800,
        call_receive_timeout_ms=20000, call_ring_timeout_ms=60000,
        call_connect_timeout_ms=30000, call_packet_timeout_ms=10000,
        me_url_prefix="", caption_length_max=1024, message_length_max=4096,
        webfile_dc_id=cfg.dc_id,
    )


@handler(F.PingRequest)
async def ping(ctx, req):
    return T.Pong(msg_id=0, ping_id=req.ping_id)


@handler(F.auth.SendCodeRequest)
async def send_code(ctx, req):
    phone = req.phone_number
    code = ctx.server.config.dev_code or ("%05d" % (int.from_bytes(os.urandom(4), "big") % 100000))
    code_hash = hashlib.sha256(("%s:%s:%s" % (phone, code, os.urandom(8).hex())).encode()).hexdigest()
    ctx.server.db.save_code(phone, code_hash, code)
    print("[server] login code for %s: %s (hash %s)" % (phone, code, code_hash[:12]), flush=True)
    return TA.SentCode(type=TA.SentCodeTypeApp(length=len(code)), phone_code_hash=code_hash, timeout=300)


@handler(F.auth.SignInRequest)
async def sign_in(ctx, req):
    if not ctx.server.db.check_code(req.phone_number, req.phone_code_hash, req.phone_code or ""):
        return RpcError(error_code=400, error_message="PHONE_CODE_INVALID")
    u = ctx.server.db.get_or_create_user(req.phone_number)
    ctx.user_id = u["id"]
    ctx.server.register_session(ctx)
    print("[server] user signed in: id=%d phone=%s" % (u["id"], u["phone"]), flush=True)
    return TA.Authorization(user=user_obj(ctx.server, u["id"], self_id=u["id"]))


@handler(F.auth.SignUpRequest)
async def sign_up(ctx, req):
    return RpcError(error_code=400, error_message="SIGNUP_NOT_SUPPORTED_USE_SIGNIN")


@handler(F.auth.LogOutRequest)
async def log_out(ctx, req):
    ctx.server.unregister_session(ctx)
    return TA.LoggedOut()


@handler(F.updates.GetStateRequest)
async def get_state(ctx, req):
    return TU.State(pts=ctx.server.pts, qts=0, date=now(), seq=0, unread_count=0)


@handler(F.messages.SendMessageRequest)
async def send_message(ctx, req):
    server = ctx.server
    me = ctx.user_id
    kind, pid = peer_of_input(req.peer)
    if kind == "user" and pid is None:
        pid = me
    dialog = dialog_key(me, kind, pid)
    mid = server.db.next_msg_id(dialog)
    date = int(time.time())
    server.db.add_message(dialog, mid, me, date, req.message, req.random_id or 0)
    pts = server.bump_pts()

    def make_updates(viewer):
        row = dict(dialog=dialog, msg_id=mid, from_id=me, date=date, text=req.message)
        msg = message_obj(server, row, viewer)
        users = [user_obj(server, me), user_obj(server, pid)] if kind == "user" else [user_obj(server, me)]
        return T.Updates(updates=[T.UpdateNewMessage(message=msg, pts=pts, pts_count=1)],
                         users=users, chats=[], date=now(), seq=0)

    # deliver to other sessions of the recipients (and the sender's other devices)
    targets = set(recipients_of(server, me, kind, pid)) | ({me} - {me} if False else set())
    for uid in recipients_of(server, me, kind, pid):
        for s in server.sessions_of(uid):
            if s is not ctx:
                try:
                    await s.push(make_updates(uid))
                except Exception as e:
                    print("[server] push failed: %s" % e, flush=True)
    return make_updates(me)


@handler(F.messages.GetHistoryRequest)
async def get_history(ctx, req):
    server = ctx.server
    me = ctx.user_id
    kind, pid = peer_of_input(req.peer)
    if kind == "user" and pid is None:
        pid = me
    dialog = dialog_key(me, kind, pid)
    limit = min(req.limit or 20, 100)
    rows = server.db.history(dialog, limit)
    msgs = [message_obj(server, r, me) for r in rows]
    users = [user_obj(server, me)]
    if kind == "user":
        users.append(user_obj(server, pid))
    return TM.Messages(messages=msgs, topics=[], chats=[], users=users)


@handler(F.messages.GetDialogsRequest)
async def get_dialogs(ctx, req):
    server = ctx.server
    me = ctx.user_id
    limit = min(req.limit or 20, 100)
    drows = server.db.dialogs_for(me)[:limit]
    dialogs, msgs, users = [], [], [user_obj(server, me)]
    for d in drows:
        dialog = d["dialog"]
        if dialog.startswith("d"):
            a, b = dialog[1:].split("_")
            other = int(b) if int(a) == me else int(a)
            peer = T.PeerUser(user_id=other)
            users.append(user_obj(server, other))
        elif dialog.startswith("c"):
            peer = T.PeerChat(chat_id=int(dialog[1:]))
        else:
            peer = T.PeerChannel(channel_id=int(dialog[2:]))
        hist = server.db.history(dialog, 1)
        if hist:
            msgs.append(message_obj(server, hist[0], me))
        dialogs.append(T.Dialog(
            peer=peer, top_message=d["top"],
            read_inbox_max_id=0, read_outbox_max_id=0,
            unread_count=0, unread_mentions_count=0,
            unread_reactions_count=0, unread_poll_votes_count=0,
            notify_settings=T.PeerNotifySettings(),
        ))
    return TM.Dialogs(dialogs=dialogs, messages=msgs, chats=[], users=users)


@handler(F.messages.ReadHistoryRequest)
async def read_history(ctx, req):
    ctx.server.bump_pts()
    return TM.AffectedMessages(pts=ctx.server.pts, pts_count=0)


@handler(F.messages.ReceivedMessagesRequest)
async def received_messages(ctx, req):
    return TM.ReceivedMessages(max_id=req.max_id)


@handler(F.messages.GetDialogFiltersRequest)
async def get_dialog_filters(ctx, req):
    return T.VectorDialogFilters(dialog_filters=[]) if hasattr(T, "VectorDialogFilters") else T.VectorDialogFilter(dialog_filters=[])


async def dispatch(session, obj, req_msg_id: int):
    fn = HANDLERS.get(type(obj))
    if fn is None:
        await session.send_rpc_error(req_msg_id, 400, "NOT_IMPLEMENTED: %s" % type(obj).__name__)
        print("[server] unimplemented method: %s" % type(obj).__name__, flush=True)
        return
    try:
        result = await fn(session, obj)
    except RpcError as e:
        await session.send_rpc_error(req_msg_id, e.error_code, e.error_message)
        return
    except Exception as e:
        import traceback; traceback.print_exc()
        await session.send_rpc_error(req_msg_id, 500, "INTERNAL: %s" % e)
        return
    if isinstance(result, RpcError):
        await session.send_rpc_error(req_msg_id, result.error_code, result.error_message)
    else:
        await session.send_rpc_result(req_msg_id, result)
