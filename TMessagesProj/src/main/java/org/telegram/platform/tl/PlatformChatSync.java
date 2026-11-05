package org.telegram.platform.tl;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import org.telegram.messenger.AccountInstance;
import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.BuildVars;
import org.telegram.messenger.FileLog;
import org.telegram.messenger.MessageObject;
import org.telegram.messenger.NotificationCenter;
import org.telegram.platform.PlatformBridge;
import org.telegram.platform.PlatformException;
import org.telegram.platform.mapper.DtoMapper;
import org.telegram.platform.repo.Callback;
import org.telegram.platform.repo.MessageRepository;
import org.telegram.tgnet.TLRPC;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Chat screen data from our own backend.
 *
 * <p>{@code ChatActivity} does not read a database directly: it waits for
 * {@code NotificationCenter.messagesDidLoad} and builds its bubbles from the
 * {@link MessageObject} list inside it. This class produces exactly that
 * notification from the REST API, so every existing layout, animation and menu
 * keeps working with our data.
 */
public final class PlatformChatSync {

    /** Dialog whose history is currently on screen. */
    private static volatile long activeDialogId;

    /** Highest seq already delivered per dialog, for paging and catch-up. */
    private static final Map<Long, Integer> LAST_SEQ = new HashMap<>();
    /** Message ids already handed to the UI, so a reconnect cannot duplicate rows. */
    private static final Map<Long, Set<Integer>> KNOWN_IDS = new HashMap<>();

    private PlatformChatSync() {
    }

    public static boolean isEnabled() {
        return BuildVars.CHAT_FROM_PLATFORM && PlatformBridge.isReady();
    }

    public static void setActiveDialog(long dialogId) {
        activeDialogId = dialogId;
    }

    public static long getActiveDialog() {
        return activeDialogId;
    }

    // ---------------------------------------------------------------- history

    /** Replacement for {@code messages.getHistory}: one page, newest first page last. */
    public static void loadHistory(long dialogId, int count, int classGuid, int loadIndex) {
        if (!isEnabled()) {
            return;
        }
        final String chatId = chatUuidFor(dialogId);
        if (chatId == null) {
            FileLog.e("platform chat: no backend chat for dialog " + dialogId);
            postEmpty(dialogId, classGuid, loadIndex);
            return;
        }
        setActiveDialog(dialogId);
        final MessageRepository messages = PlatformBridge.get().messages();
        final Long before = beforeSeq(dialogId);
        messages.loadHistory(chatId, before, Math.max(1, count), new Callback<JsonObject>() {
            @Override
            public void onSuccess(JsonObject page) {
                post(dialogId, parse(page), classGuid, loadIndex, false);
            }

            @Override
            public void onError(PlatformException error) {
                FileLog.e("platform chat history: " + error);
                postEmpty(dialogId, classGuid, loadIndex);
            }
        });
    }

    /** Fills the gap after a reconnect: everything newer than the last seq we saw. */
    public static void syncSinceLastSeen(long dialogId, int classGuid, int loadIndex) {
        if (!isEnabled()) {
            return;
        }
        final String chatId = chatUuidFor(dialogId);
        final Integer lastSeq = LAST_SEQ.get(dialogId);
        if (chatId == null || lastSeq == null) {
            return;
        }
        PlatformBridge.get().messages().loadHistoryAfter(chatId, lastSeq, 200,
                new Callback<JsonObject>() {
                    @Override
                    public void onSuccess(JsonObject page) {
                        post(dialogId, parse(page), classGuid, loadIndex, true);
                    }

                    @Override
                    public void onError(PlatformException error) {
                        FileLog.e("platform chat sync: " + error);
                    }
                });
    }

    private static Long beforeSeq(long dialogId) {
        Integer last = LAST_SEQ.get(dialogId);
        return last == null ? null : (long) last;
    }

    // ------------------------------------------------------------------- send

    public static void sendText(long dialogId, String text, MessageObject replyTo) {
        if (!isEnabled() || text == null || text.isEmpty()) {
            return;
        }
        final String chatId = chatUuidFor(dialogId);
        if (chatId == null) {
            FileLog.e("platform chat: cannot send, no backend chat for " + dialogId);
            return;
        }
        String replyId = null;
        if (replyTo != null && replyTo.messageOwner != null) {
            replyId = PlatformBridge.get().ids().toUuid(replyTo.getId());
        }
        final MessageObject reply = replyTo;
        PlatformBridge.get().messages().send(chatId, text, replyId, new Callback<String>() {
            @Override
            public void onSuccess(String messageId) {
                // message.new also arrives over the socket; the id set keeps it single.
                FileLog.d("platform chat: sent " + messageId);
            }

            @Override
            public void onError(PlatformException error) {
                FileLog.e("platform chat send: " + error);
            }
        });
    }

    public static void deleteMessages(long dialogId, ArrayList<Integer> ids, boolean forAll) {
        if (!isEnabled() || ids == null) {
            return;
        }
        final String chatId = chatUuidFor(dialogId);
        if (chatId == null) {
            return;
        }
        for (final Integer id : ids) {
            String messageId = PlatformBridge.get().ids().toUuid(id);
            if (messageId == null) {
                continue;
            }
            PlatformBridge.get().messages().delete(chatId, messageId, forAll, new Callback<Void>() {
                @Override
                public void onSuccess(Void value) {
                    Set<Integer> known = KNOWN_IDS.get(dialogId);
                    if (known != null) {
                        known.remove(id);
                    }
                }

                @Override
                public void onError(PlatformException error) {
                    FileLog.e("platform delete: " + error);
                }
            });
        }
    }

    public static void editMessage(long dialogId, int messageId, String text) {
        if (!isEnabled()) {
            return;
        }
        final String chatId = chatUuidFor(dialogId);
        final String backendId = PlatformBridge.get().ids().toUuid(messageId);
        if (chatId == null || backendId == null) {
            return;
        }
        PlatformBridge.get().messages().edit(chatId, backendId, text, new Callback<JsonObject>() {
            @Override
            public void onSuccess(JsonObject value) {
                FileLog.d("platform edit ok");
            }

            @Override
            public void onError(PlatformException error) {
                FileLog.e("platform edit: " + error);
            }
        });
    }

    /**
     * In-chat search. The results are delivered through the same notifications the
     * MTProto search posts, so the search bar and its highlights are unchanged.
     */
    public static void searchInChat(long dialogId, String query, int guid, int direction,
                                    boolean jumpToMessage) {
        if (!isEnabled() || query == null || query.trim().isEmpty()) {
            return;
        }
        final String chatId = chatUuidFor(dialogId);
        if (chatId == null) {
            return;
        }
        final int finalGuid = guid;
        final boolean jump = jumpToMessage;
        NotificationCenter.getGlobalInstance()
                .postNotificationName(NotificationCenter.chatSearchResultsLoading, finalGuid);
        final int pageLimit = 50;
        PlatformBridge.get().messages().search(chatId, query.trim(),
                new Callback<List<JsonObject>>() {
                    @Override
                    public void onSuccess(List<JsonObject> rows) {
                        final List<JsonObject> results = rows;
                        AndroidUtilities.runOnUIThread(() -> {
                            int index = 0;
                            // getMask(): bit 1 = more results ahead, bit 2 = something
                            // to navigate back to. The old code always sent 0, which told
                            // the bar there was nothing to jump to.
                            boolean more = results.size() >= pageLimit;
                            for (JsonObject row : results) {
                                String id = optString(row, "id");
                                if (id == null) {
                                    continue;
                                }
                                int messageId = PlatformBridge.get().ids().toInt(id);
                                index++;
                                int mask = (more ? 1 : 0) | (index > 0 ? 2 : 0);
                                NotificationCenter.getGlobalInstance().postNotificationName(
                                        NotificationCenter.chatSearchResultsAvailable, finalGuid,
                                        messageId, mask, dialogId, index, results.size(), jump);
                            }
                            if (results.isEmpty()) {
                                NotificationCenter.getGlobalInstance().postNotificationName(
                                        NotificationCenter.chatSearchResultsAvailable, finalGuid,
                                        0, 0, dialogId, 0, 0, jump);
                            }
                        });
                    }

                    @Override
                    public void onError(PlatformException error) {
                        FileLog.e("platform search: " + error);
                    }
                });
    }

    /** Incoming typing: feeds the same structure the MTProto updates fill. */
    public static void onTypingJson(JsonObject data) {
        if (!isEnabled() || data == null) {
            return;
        }
        String user = data.has("user_id") && !data.get("user_id").isJsonNull()
                ? data.get("user_id").getAsString() : null;
        String chat = data.has("chat_id") && !data.get("chat_id").isJsonNull()
                ? data.get("chat_id").getAsString() : null;
        if (user == null || chat == null) {
            return;
        }
        final long dialogId = PlatformBridge.get().ids().toLong(user);
        final long chatDialog = -PlatformBridge.get().ids().toLong(chat);
        AccountInstance.getInstance(0).getMessagesController()
                .platformSetTyping(activeDialogId != 0 ? activeDialogId : chatDialog,
                        dialogId, true);
    }

    public static void sendTyping(long dialogId) {
        final String chatId = chatUuidFor(dialogId);
        if (isEnabled() && chatId != null) {
            PlatformBridge.get().messages().sendTyping(chatId);
        }
    }

    // --------------------------------------------------------------- realtime

    public static void onRealtimeEvent(String type, JsonObject data) {
        if (!isEnabled() || type == null || activeDialogId == 0) {
            return;
        }
        String chatId = chatUuidFor(activeDialogId);
        if (chatId == null || data == null) {
            return;
        }
        String eventChat = data.has("chat_id") && !data.get("chat_id").isJsonNull()
                ? data.get("chat_id").getAsString() : null;
        if (!chatId.equals(eventChat)) {
            return;
        }
        if ("typing".equals(type)) {
            onTypingJson(data);
            return;
        }
        switch (type) {
            case "message.new":
            case "message.edited":
            case "message.updated":
            case "message.deleted":
                // Re-read the tail instead of patching bubbles by hand: the server
                // stays the single source of truth and ids keep the list unique.
                syncSinceLastSeen(activeDialogId, 0, 0);
                break;
            default:
                break;
        }
    }

    // ---------------------------------------------------------------- helpers

    private static String chatUuidFor(long dialogId) {
        String known = PlatformDialogsSync.chatUuidFor(dialogId);
        if (known != null) {
            return known;
        }
        long id = dialogId < 0 ? -dialogId : dialogId;
        return PlatformBridge.get().ids().toUuid(id);
    }

    private static List<JsonObject> parse(JsonObject page) {
        List<JsonObject> rows = new ArrayList<>();
        if (page == null || !page.has("items") || !page.get("items").isJsonArray()) {
            return rows;
        }
        JsonArray items = page.getAsJsonArray("items");
        for (JsonElement element : items) {
            if (element.isJsonObject()) {
                rows.add(element.getAsJsonObject());
            }
        }
        return rows;
    }

    private static void post(long dialogId, List<JsonObject> rows, int classGuid, int loadIndex,
                             boolean onlyNew) {
        final PlatformBridge bridge = PlatformBridge.get();
        final DtoMapper mapper = bridge.mapper();
        final int account = 0;
        final ArrayList<MessageObject> objects = new ArrayList<>(rows.size());
        final Set<Integer> known = KNOWN_IDS.get(dialogId) != null
                ? KNOWN_IDS.get(dialogId) : new HashSet<>();
        int lastSeq = LAST_SEQ.get(dialogId) != null ? LAST_SEQ.get(dialogId) : 0;
        int lastMessageId = 0;

        for (JsonObject row : rows) {
            try {
                final int id = bridge.ids().toInt(optString(row, "id"));
                final int seq = (int) optLong(row, "seq", 0);
                if (onlyNew && known.contains(id)) {
                    continue;
                }
                long senderMapped = 0;
                String sender = optString(row, "sender_id");
                if (sender != null) {
                    senderMapped = bridge.ids().toLong(sender);
                }
                boolean out = isOwn(account, senderMapped);
                TLRPC.TL_message message = mapper.toMessage(row, dialogId, out);
                if (message == null) {
                    continue;
                }
                message.id = id;
                MessageObject object = new MessageObject(account, message, false, false);
                objects.add(object);
                known.add(id);
                lastSeq = Math.max(lastSeq, seq);
                lastMessageId = Math.max(lastMessageId, id);
            } catch (Throwable t) {
                FileLog.e("platform chat message: " + t);
            }
        }
        KNOWN_IDS.put(dialogId, known);
        LAST_SEQ.put(dialogId, lastSeq);

        if (objects.isEmpty()) {
            return;
        }
        Collections.sort(objects, (a, b) -> Integer.compare(a.getId(), b.getId()));

        final ArrayList<MessageObject> deliver = objects;
        final int finalLastId = lastMessageId;
        AndroidUtilities.runOnUIThread(() -> NotificationCenter.getGlobalInstance()
                .postNotificationName(NotificationCenter.messagesDidLoad, dialogId, deliver.size(),
                        deliver, false, 0, finalLastId, 0, 0, 0, true, classGuid, loadIndex,
                        0, 0, 0));
    }

    private static void postEmpty(long dialogId, int classGuid, int loadIndex) {
        AndroidUtilities.runOnUIThread(() -> NotificationCenter.getGlobalInstance()
                .postNotificationName(NotificationCenter.messagesDidLoad, dialogId, 0,
                        new ArrayList<MessageObject>(), true, 0, 0, 0, 0, 0, true, classGuid,
                        loadIndex, 0, 0, 0));
    }

    private static boolean isOwn(int account, long mappedSenderId) {
        if (mappedSenderId == 0) {
            return false;
        }
        try {
            TLRPC.User self = AccountInstance.getInstance(account).getUserConfig().getCurrentUser();
            return self != null && self.id == mappedSenderId;
        } catch (Throwable t) {
            return false;
        }
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
