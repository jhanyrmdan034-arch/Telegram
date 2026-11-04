package org.telegram.platform.tl;

import android.util.SparseArray;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import org.telegram.messenger.AccountInstance;
import org.telegram.messenger.BuildVars;
import org.telegram.messenger.FileLog;
import org.telegram.messenger.MessagesController;
import org.telegram.messenger.NotificationCenter;
import org.telegram.platform.PlatformBridge;
import org.telegram.platform.PlatformException;
import org.telegram.platform.repo.ChatRepository;
import org.telegram.tgnet.TLRPC;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Fills the dialog list of the existing UI from our own backend.
 *
 * <p>The list screen renders {@code MessagesController.dialogsByFolder}, so this
 * class builds real {@link TLRPC.TL_dialog} / {@link TLRPC.TL_user} /
 * {@link TLRPC.TL_chat} instances from the API rows and installs them there, then
 * posts {@code dialogsNeedReload} — the same notification MTProto updates use.
 * No layout, adapter or menu is touched.
 *
 * <p>Enabled only in debug builds ({@link BuildVars#DIALOGS_FROM_PLATFORM}); in a
 * release build the Telegram data path stays exactly as it was.
 */
public final class PlatformDialogsSync {

    private static final int FOLDER_MAIN = 0;

    private PlatformDialogsSync() {
    }

    public static boolean isEnabled() {
        return BuildVars.DIALOGS_FROM_PLATFORM && PlatformBridge.isReady();
    }

    /** Loads the first page(s) of dialogs and installs them into the UI. */
    public static void refresh(final int account) {
        if (!isEnabled()) {
            return;
        }
        final PlatformBridge bridge = PlatformBridge.get();
        final ChatRepository chats = bridge.chats();
        bridge.executor().execute(() -> {
            try {
                List<JsonObject> rows = new ArrayList<>();
                int offset = 0;
                final int pageSize = 100;
                while (true) {
                    JsonArray page = chats.dialogPage(pageSize, offset);
                    for (JsonElement element : page) {
                        rows.add(element.getAsJsonObject());
                    }
                    if (page.size() < pageSize) {
                        break;
                    }
                    offset += pageSize;
                    if (offset >= 500) { // hard stop: the UI scrolls lazily after this
                        break;
                    }
                }
                apply(account, rows);
            } catch (PlatformException e) {
                FileLog.e("platform dialogs: " + e);
                if (e.requiresLogin()) {
                    NotificationCenter.getGlobalInstance()
                            .postNotificationName(NotificationCenter.needShowAlert, 3);
                }
            }
        });
    }

    /** Builds TL objects from API rows and installs them; must run on the UI thread. */
    private static void apply(final int account, final List<JsonObject> rows) {
        org.telegram.messenger.AndroidUtilities.runOnUIThread(() -> {
            try {
                final PlatformBridge bridge = PlatformBridge.get();
                final MessagesController controller =
                        AccountInstance.getInstance(account).getMessagesController();
                final ArrayList<TLRPC.Dialog> dialogs = new ArrayList<>(rows.size());
                final List<TLRPC.User> users = new ArrayList<>();
                final List<TLRPC.Chat> chats = new ArrayList<>();

                long pinnedCounter = rows.size();
                for (JsonObject row : rows) {
                    final String type = optString(row, "type");
                    final long dialogId = bridge.mapper().dialogIdFor(row);

                    if ("private".equals(type) || "saved".equals(type)) {
                        JsonObject peer = row.has("peer") && row.get("peer").isJsonObject()
                                ? row.getAsJsonObject("peer") : null;
                        if (peer != null) {
                            users.add(bridge.mapper().toUser(peer));
                        }
                    } else {
                        chats.add(bridge.mapper().toChat(row));
                    }

                    TLRPC.TL_dialog dialog = new TLRPC.TL_dialog();
                    dialog.id = dialogId;
                    dialog.peer = peerFor(type, dialogId);
                    dialog.unread_count = (int) optLong(row, "unread_count", 0);
                    dialog.read_inbox_max_id = (int) optLong(row, "last_read_seq", 0);
                    dialog.last_message_date = lastMessageDate(row);
                    dialog.top_message = topMessageId(row);
                    dialog.pinned = row.has("pinned") && row.get("pinned").getAsBoolean();
                    if (dialog.pinned) {
                        dialog.pinnedNum = (int) pinnedCounter--;
                        dialog.flags |= 1;
                    }
                    dialogs.add(dialog);
                }

                if (!users.isEmpty()) {
                    controller.putUsers(new ArrayList<>(users), false);
                }
                for (TLRPC.Chat chat : chats) {
                    controller.putChat(chat, false);
                }

                controller.sortDialogsList(dialogs);
                // Install atomically: readers see either the old or the new list.
                controller.dialogsByFolder.put(FOLDER_MAIN, dialogs);
                controller.dialogsServerOnly.clear();
                controller.dialogsServerOnly.addAll(dialogs);
                controller.dialogs_dict.clear();
                for (int i = 0; i < dialogs.size(); i++) {
                    controller.dialogs_dict.put(dialogs.get(i).id, dialogs.get(i));
                }

                NotificationCenter.getGlobalInstance()
                        .postNotificationName(NotificationCenter.dialogsNeedReload);
                FileLog.d("platform dialogs: installed " + dialogs.size() + " rows");
            } catch (Throwable t) {
                FileLog.e(t);
            }
        });
    }

    /**
     * Applies one realtime event. The list is refreshed from the API afterwards,
     * so the visible row (last message, counter, presence) always matches the
     * server's own ordering instead of a client-side guess.
     */
    public static void onRealtimeEvent(final int account, String type, JsonObject data) {
        if (!isEnabled() || type == null) {
            return;
        }
        switch (type) {
            case "message.new":
            case "message.updated":
            case "message.edited":
            case "message.deleted":
            case "presence.updated":
            case "read.updated":
            case "message.read":
            case "dialog.updated":
                refresh(account);
                break;
            default:
                break;
        }
    }

    // ------------------------------------------------------------------ helpers

    private static TLRPC.Peer peerFor(String type, long dialogId) {
        if ("private".equals(type) || "saved".equals(type)) {
            TLRPC.TL_peerUser peer = new TLRPC.TL_peerUser();
            peer.user_id = dialogId;
            return peer;
        }
        TLRPC.TL_peerChat peer = new TLRPC.TL_peerChat();
        peer.chat_id = -dialogId;
        return peer;
    }

    private static int topMessageId(JsonObject row) {
        JsonObject last = row.has("last_message") && row.get("last_message").isJsonObject()
                ? row.getAsJsonObject("last_message") : null;
        if (last == null) {
            return 0;
        }
        return PlatformBridge.get().ids().toInt(optString(last, "id"));
    }

    private static int lastMessageDate(JsonObject row) {
        JsonObject last = row.has("last_message") && row.get("last_message").isJsonObject()
                ? row.getAsJsonObject("last_message") : null;
        if (last == null) {
            return 0;
        }
        return org.telegram.platform.mapper.DtoMapper.parseUnix(optString(last, "created_at"));
    }

    private static String optString(JsonObject json, String field) {
        if (json == null || !json.has(field) || json.get(field).isJsonNull()) {
            return null;
        }
        return json.get(field).getAsString();
    }

    private static long optLong(JsonObject json, String field, long fallback) {
        if (json == null || !json.has(field) || json.get(field).isJsonNull()) {
            return fallback;
        }
        try {
            return json.get(field).getAsLong();
        } catch (RuntimeException e) {
            return fallback;
        }
    }

    /** Folder id -> dialogs, kept for the folder tabs (see docs). */
    public static void installFolder(int account, int folderId, ArrayList<TLRPC.Dialog> dialogs) {
        if (!isEnabled()) {
            return;
        }
        MessagesController controller =
                AccountInstance.getInstance(account).getMessagesController();
        controller.dialogsByFolder.put(folderId, dialogs);
        NotificationCenter.getGlobalInstance()
                .postNotificationName(NotificationCenter.dialogsNeedReload);
    }
}
