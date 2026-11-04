package org.telegram.platform.tl;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import org.telegram.messenger.AccountInstance;
import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.BuildVars;
import org.telegram.messenger.FileLog;
import org.telegram.messenger.MessageObject;
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
 * Fills the existing dialog list from our own backend.
 *
 * <p>Three structures of {@link MessagesController} are what the untouched UI
 * reads, and all three are filled here:
 * <ul>
 *   <li>{@code dialogsByFolder} — the rows themselves (avatar, title, counters)</li>
 *   <li>{@code dialogMessage} — the {@link MessageObject} used for the preview
 *       line (text, sender prefix, date), exactly what MTProto fills</li>
 *   <li>{@code dialogFilters} — the folder tabs, one per backend folder</li>
 * </ul>
 * Avatars are downloaded by {@link PlatformAvatars} and remembered per dialog.
 */
public final class PlatformDialogsSync {

    private static final int FOLDER_MAIN = 0;
    private static final int PAGE_SIZE = 100;
    private static final int MAX_ROWS = 500;

    /** Dialog id -> backend chat id, so actions can address the right row. */
    private static final Map<Long, String> CHAT_IDS = new java.util.concurrent.ConcurrentHashMap<>();

    private PlatformDialogsSync() {
    }

    public static boolean isEnabled() {
        return BuildVars.DIALOGS_FROM_PLATFORM && PlatformBridge.isReady();
    }

    public static void refresh(final int account) {
        if (!isEnabled()) {
            return;
        }
        final PlatformBridge bridge = PlatformBridge.get();
        final ChatRepository chats = bridge.chats();
        bridge.executor().execute(() -> {
            try {
                List<JsonObject> rows = loadAll(chats);
                JsonArray folders = null;
                try {
                    folders = bridge.api().getArray(org.telegram.platform.net.PlatformApi.API_PREFIX + "/folders", null);
                } catch (PlatformException e) {
                    FileLog.e("platform folders: " + e);
                }
                apply(account, rows, folders);
            } catch (PlatformException e) {
                FileLog.e("platform dialogs: " + e);
                if (e.requiresLogin()) {
                    NotificationCenter.getGlobalInstance()
                            .postNotificationName(NotificationCenter.needShowAlert, 3);
                }
            }
        });
    }

    private static List<JsonObject> loadAll(ChatRepository chats) throws PlatformException {
        List<JsonObject> rows = new ArrayList<>();
        int offset = 0;
        while (true) {
            JsonArray page = chats.dialogPage(PAGE_SIZE, offset);
            for (JsonElement element : page) {
                if (element.isJsonObject()) {
                    rows.add(element.getAsJsonObject());
                }
            }
            if (page.size() < PAGE_SIZE || rows.size() >= MAX_ROWS) {
                break;
            }
            offset += PAGE_SIZE;
        }
        return rows;
    }

    private static void apply(final int account, final List<JsonObject> rows, final JsonArray folders) {
        AndroidUtilities.runOnUIThread(() -> {
            try {
                install(account, rows, folders);
            } catch (Throwable t) {
                FileLog.e(t);
            }
        });
    }

    private static void install(int account, List<JsonObject> rows, JsonArray folders) {
        final PlatformBridge bridge = PlatformBridge.get();
        final MessagesController controller = AccountInstance.getInstance(account).getMessagesController();

        final ArrayList<TLRPC.Dialog> dialogs = new ArrayList<>(rows.size());
        final ArrayList<TLRPC.User> users = new ArrayList<>();
        final ArrayList<TLRPC.Chat> chats = new ArrayList<>();
        final Map<Long, List<MessageObject>> previews = new HashMap<>();
        final Map<Long, String> backendChatIds = new HashMap<>();

        long pinnedCounter = rows.size();
        for (JsonObject row : rows) {
            final String type = optString(row, "type");
            final String backendId = optString(row, "id");
            final long dialogId = bridge.mapper().dialogIdFor(row);
            if (backendId != null) {
                backendChatIds.put(dialogId, backendId);
                CHAT_IDS.put(dialogId, backendId);
            }

            if ("private".equals(type) || "saved".equals(type)) {
                JsonObject peer = row.has("peer") && row.get("peer").isJsonObject()
                        ? row.getAsJsonObject("peer") : null;
                if (peer != null) {
                    TLRPC.TL_user user = bridge.mapper().toUser(peer);
                    user.id = dialogId;
                    users.add(user);
                }
            } else {
                TLRPC.TL_chat chat = bridge.mapper().toChat(row);
                chat.id = -dialogId;
                chats.add(chat);
            }

            PlatformAvatars.rememberFromRow(dialogId, row);

            TLRPC.TL_dialog dialog = new TLRPC.TL_dialog();
            dialog.id = dialogId;
            dialog.peer = peerFor(type, dialogId);
            dialog.unread_count = (int) optLong(row, "unread_count", 0);
            dialog.read_inbox_max_id = (int) optLong(row, "last_read_seq", 0);
            dialog.last_message_date = lastMessageDate(row);
            dialog.top_message = 0;
            dialog.pinned = row.has("pinned") && row.get("pinned").getAsBoolean();
            if (dialog.pinned) {
                dialog.pinnedNum = (int) pinnedCounter--;
                dialog.flags |= 1;
            }
            dialogs.add(dialog);

            MessageObject preview = buildPreview(account, bridge, row, dialogId, ownUserId(account));
            if (preview != null) {
                dialog.top_message = preview.getId();
                dialog.last_message_date = preview.messageOwner.date;
                List<MessageObject> group = new ArrayList<>(1);
                group.add(preview);
                previews.put(dialogId, group);
            }
        }

        if (!users.isEmpty()) {
            controller.putUsers(new ArrayList<>(users), false);
        }
        for (TLRPC.Chat chat : chats) {
            controller.putChat(chat, false);
        }

        controller.sortDialogsList(dialogs);

        // 1) the rows
        controller.dialogsByFolder.put(FOLDER_MAIN, dialogs);
        controller.dialogsServerOnly.clear();
        controller.dialogsServerOnly.addAll(dialogs);
        controller.dialogs_dict.clear();
        for (int i = 0; i < dialogs.size(); i++) {
            controller.dialogs_dict.put(dialogs.get(i).id, dialogs.get(i));
        }

        // 2) the preview lines
        controller.dialogMessage.clear();
        for (Map.Entry<Long, List<MessageObject>> entry : previews.entrySet()) {
            controller.dialogMessage.put(entry.getKey(), new ArrayList<>(entry.getValue()));
        }

        // 3) the folder tabs
        installFilters(controller, dialogs, folders, backendChatIds);

        NotificationCenter.getGlobalInstance()
                .postNotificationName(NotificationCenter.dialogsNeedReload);
        FileLog.d("platform dialogs: installed " + dialogs.size() + " rows, "
                + previews.size() + " previews");
    }

    /**
     * Folder tabs: one {@code DialogFilter} per backend folder, plus the per-folder
     * dialog list the pager reads. Names/order come from the API, so the tab strip
     * keeps its native look while showing real folders.
     */
    private static void installFilters(MessagesController controller,
                                       ArrayList<TLRPC.Dialog> all,
                                       JsonArray folders,
                                       Map<Long, String> backendChatIds) {
        if (folders == null) {
            return;
        }
        final PlatformBridge bridge = PlatformBridge.get();
        // Backend chat id -> dialog id for every row, private ones included: a
        // folder may contain a contact, not only a group or channel.
        Map<String, Long> dialogByChatId = new HashMap<>();
        for (Map.Entry<Long, String> entry : backendChatIds.entrySet()) {
            dialogByChatId.put(entry.getValue(), entry.getKey());
        }
        ArrayList<MessagesController.DialogFilter> filters = controller.getDialogFilters();
        filters.clear();

        int index = 0;
        for (JsonElement element : folders) {
            if (!element.isJsonObject()) {
                continue;
            }
            JsonObject folder = element.getAsJsonObject();
            index++;
            MessagesController.DialogFilter filter = new MessagesController.DialogFilter();
            filter.id = index;
            filter.name = optString(folder, "name");
            filter.order = (int) optLong(folder, "position", index);

            ArrayList<TLRPC.Dialog> subset = new ArrayList<>();
            if (folder.has("chat_ids") && folder.get("chat_ids").isJsonArray()) {
                for (JsonElement idElement : folder.getAsJsonArray("chat_ids")) {
                    if (idElement.isJsonNull()) {
                        continue;
                    }
                    Long known = dialogByChatId.get(idElement.getAsString());
                    long dialogId = known != null ? known : -bridge.ids().toLong(idElement.getAsString());
                    for (int i = 0; i < all.size(); i++) {
                        if (all.get(i).id == dialogId) {
                            subset.add(all.get(i));
                            filter.alwaysShow.add(dialogId);
                            break;
                        }
                    }
                }
            }
            filter.dialogs = subset;
            filters.add(filter);
            controller.dialogsByFolder.put(index, subset);
        }
        FileLog.d("platform dialogs: " + filters.size() + " folder tabs");
    }

    /** The mapped id of the signed-in user, so previews can say "You:". */
    private static long ownUserId(int account) {
        try {
            TLRPC.User self = AccountInstance.getInstance(account).getUserConfig().getCurrentUser();
            return self == null ? 0 : self.id;
        } catch (Throwable t) {
            return 0;
        }
    }

    private static MessageObject buildPreview(int account, PlatformBridge bridge, JsonObject row,
                                              long dialogId, long ownId) {
        if (!row.has("last_message") || !row.get("last_message").isJsonObject()) {
            return null;
        }
        try {
            JsonObject last = row.getAsJsonObject("last_message");
            boolean out = false;
            if (ownId != 0 && last.has("sender_id") && !last.get("sender_id").isJsonNull()) {
                out = bridge.ids().toLong(last.get("sender_id").getAsString()) == ownId;
            }
            TLRPC.TL_message message = bridge.mapper().toMessage(last, dialogId, out);
            if (message == null) {
                return null;
            }
            MessageObject object = new MessageObject(account, message, false, false);
            object.generateLayout(null);
            return object;
        } catch (Throwable t) {
            FileLog.e("platform preview: " + t);
            return null;
        }
    }

    public static void onRealtimeEvent(int account, String type, JsonObject data) {
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

    /** The backend chat id behind a dialog, or {@code null} when it is unknown. */
    public static String chatUuidFor(long dialogId) {
        return CHAT_IDS.get(dialogId);
    }

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
}
