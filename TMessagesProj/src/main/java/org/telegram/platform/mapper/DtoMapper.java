package org.telegram.platform.mapper;

import com.google.gson.JsonObject;

import org.telegram.tgnet.TLRPC;

/**
 * Converts backend JSON into the TL objects the existing UI already renders.
 *
 * <p>This is the seam required by the mission: no screen is rewritten, the data
 * simply arrives through {@code TLRPC} instances filled from our own API instead
 * of from an MTProto update. Only read paths are mapped — every write goes
 * through {@code org.telegram.platform.repo.*}.
 */
public final class DtoMapper {

    private final IdMapper ids;

    public DtoMapper(IdMapper ids) {
        this.ids = ids;
    }

    // --------------------------------------------------------------- users

    public TLRPC.TL_user toUser(JsonObject json) {
        TLRPC.TL_user user = new TLRPC.TL_user();
        user.id = ids.toLong(string(json, "id"));
        String display = string(json, "display_name");
        if (display == null) {
            display = string(json, "username");
        }
        if (display != null) {
            int space = display.indexOf(' ');
            if (space > 0) {
                user.first_name = display.substring(0, space);
                user.last_name = display.substring(space + 1);
            } else {
                user.first_name = display;
                user.last_name = "";
            }
        }
        user.username = string(json, "username");
        user.phone = string(json, "phone");

        boolean online = bool(json, "is_online");
        if (online) {
            TLRPC.TL_userStatusOnline status = new TLRPC.TL_userStatusOnline();
            status.expires = (int) (System.currentTimeMillis() / 1000L) + 60;
            user.status = status;
        } else if (string(json, "last_seen_at") != null) {
            TLRPC.TL_userStatusOffline status = new TLRPC.TL_userStatusOffline();
            status.expires = parseUnix(string(json, "last_seen_at"));
            user.status = status;
        } else {
            user.status = new TLRPC.TL_userStatusRecently();
        }
        return user;
    }

    // ---------------------------------------------------------------- chats

    /**
     * Maps a chat row. {@code last_message} and {@code peer} are included in the
     * list response, so the dialog row can be rendered without further calls.
     */
    public TLRPC.TL_chat toChat(JsonObject json) {
        TLRPC.TL_chat chat = new TLRPC.TL_chat();
        chat.id = ids.toLong(string(json, "id"));
        chat.title = titleFor(json);
        chat.date = parseUnix(string(json, "created_at"));
        chat.participants_count = (int) number(json, "member_count");
        chat.username = string(json, "username");
        String type = string(json, "type");
        chat.megagroup = "group".equals(type);
        chat.broadcast = "channel".equals(type);
        return chat;
    }

    /** Dialog title for a row: group title, otherwise the peer's display name. */
    public String titleFor(JsonObject chat) {
        String title = string(chat, "title");
        if (title != null && !title.isEmpty()) {
            return title;
        }
        JsonObject peer = object(chat, "peer");
        if (peer != null) {
            String display = string(peer, "display_name");
            if (display != null) {
                return display;
            }
            String username = string(peer, "username");
            if (username != null) {
                return username;
            }
        }
        String type = string(chat, "type");
        if ("saved".equals(type)) {
            return "Saved Messages";
        }
        return "";
    }

    /**
     * Dialog id convention used across the app: private chats use the peer's user
     * id, groups/channels use the negated chat id.
     */
    public long dialogIdFor(JsonObject chat) {
        JsonObject peer = object(chat, "peer");
        if (peer != null) {
            return ids.toLong(string(peer, "id"));
        }
        String type = string(chat, "type");
        long chatId = ids.toLong(string(chat, "id"));
        if ("private".equals(type) || "saved".equals(type)) {
            return chatId;
        }
        return -chatId;
    }

    // ------------------------------------------------------------- messages

    public TLRPC.TL_message toMessage(JsonObject json, long dialogId, boolean out) {
        TLRPC.TL_message message = new TLRPC.TL_message();
        message.id = ids.toInt(string(json, "id"));
        message.dialog_id = dialogId;
        message.message = string(json, "text") == null ? "" : string(json, "text");
        message.date = parseUnix(string(json, "created_at"));
        message.out = out;
        message.unread = !out;
        if (json.has("edited_at") && !json.get("edited_at").isJsonNull()) {
            message.edit_date = parseUnix(string(json, "edited_at"));
        }

        TLRPC.TL_peerChat peer = new TLRPC.TL_peerChat();
        peer.chat_id = ids.toLong(string(json, "chat_id"));
        message.peer_id = peer;

        String sender = string(json, "sender_id");
        if (sender != null) {
            TLRPC.TL_peerUser from = new TLRPC.TL_peerUser();
            from.user_id = ids.toLong(sender);
            message.from_id = from;
        }

        JsonObject meta = object(json, "meta");
        if (meta != null && meta.has("forwarded_from") && meta.get("forwarded_from").isJsonObject()) {
            JsonObject forwarded = meta.getAsJsonObject("forwarded_from");
            message.flags |= TLRPC.MESSAGE_FLAG_FWD;
            message.fwd_from = new TLRPC.TL_messageFwdHeader();
            message.fwd_from.date = message.date;
            String originalSender = string(forwarded, "sender_id");
            if (originalSender != null) {
                TLRPC.TL_peerUser origin = new TLRPC.TL_peerUser();
                origin.user_id = ids.toLong(originalSender);
                message.fwd_from.from_id = origin;
            }
        }
        return message;
    }

    // ------------------------------------------------------------- helpers

    static String string(JsonObject json, String field) {
        if (json == null || !json.has(field) || json.get(field).isJsonNull()) {
            return null;
        }
        return json.get(field).getAsString();
    }

    static long number(JsonObject json, String field) {
        if (json == null || !json.has(field) || json.get(field).isJsonNull()) {
            return 0L;
        }
        try {
            return json.get(field).getAsLong();
        } catch (RuntimeException e) {
            return 0L;
        }
    }

    static boolean bool(JsonObject json, String field) {
        return json != null && json.has(field) && !json.get(field).isJsonNull()
                && json.get(field).getAsBoolean();
    }

    static JsonObject object(JsonObject json, String field) {
        if (json == null || !json.has(field) || !json.get(field).isJsonObject()) {
            return null;
        }
        return json.getAsJsonObject(field);
    }

    /** Parses ISO-8601 timestamps emitted by the API; falls back to "now". */
    static int parseUnix(String iso) {
        if (iso == null) {
            return (int) (System.currentTimeMillis() / 1000L);
        }
        try {
            java.time.OffsetDateTime parsed = java.time.OffsetDateTime.parse(iso);
            return (int) parsed.toEpochSecond();
        } catch (RuntimeException ignored) {
            return (int) (System.currentTimeMillis() / 1000L);
        }
    }
}
