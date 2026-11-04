package org.telegram.platform.repo;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import org.telegram.platform.PlatformException;
import org.telegram.platform.net.PlatformApi;
import org.telegram.platform.realtime.RealtimeClient;
import org.telegram.platform.store.LocalStore;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Chat history and sending, including the behaviours phase 4 requires:
 * optimistic send, offline outbox, automatic retry after reconnect, edit,
 * delete, forward, read receipts and typing indicators.
 */
public final class MessageRepository {

    public static final String STATE_PENDING = "pending";
    public static final String STATE_SENT = "sent";
    public static final String STATE_FAILED = "failed";

    private final PlatformApi api;
    private final LocalStore store;
    private final RealtimeClient realtime;
    private final ExecutorService executor;
    private final ScheduledExecutorService retryScheduler =
            Executors.newSingleThreadScheduledExecutor(r -> {
                Thread thread = new Thread(r, "platform-outbox");
                thread.setDaemon(true);
                return thread;
            });

    public MessageRepository(PlatformApi api, LocalStore store, RealtimeClient realtime,
                             ExecutorService executor) {
        this.api = api;
        this.store = store;
        this.realtime = realtime;
        this.executor = executor;
    }

    // ----------------------------------------------------------------- reading

    public List<JsonObject> cachedHistory(String chatId, int limit) {
        List<JsonObject> result = new ArrayList<>();
        for (String json : store.loadMessages(chatId, limit)) {
            result.add(api.gson().fromJson(json, JsonObject.class));
        }
        return result;
    }

    /**
     * Loads one page of history. {@code beforeSeq} pages backwards; the API returns
     * items in ascending order and exposes {@code next_before_seq} for old messages.
     */
    public void loadHistory(final String chatId, final Long beforeSeq, final int limit,
                            final Callback<JsonObject> callback) {
        executor.execute(() -> {
            try {
                Map<String, String> params = new HashMap<>();
                params.put("limit", String.valueOf(limit));
                if (beforeSeq != null) {
                    params.put("before_seq", String.valueOf(beforeSeq));
                }
                JsonObject page = api.getObject(
                        PlatformApi.API_PREFIX + "/chats/" + chatId + "/messages", params);
                List<String> raw = new ArrayList<>();
                if (page.has("items")) {
                    JsonArray items = page.getAsJsonArray("items");
                    for (JsonElement element : items) {
                        raw.add(element.getAsJsonObject().toString());
                    }
                }
                if (!raw.isEmpty()) {
                    store.saveMessages(chatId, raw);
                }
                callback.onSuccess(page);
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    public void search(final String chatId, final String query,
                       final Callback<List<JsonObject>> callback) {
        executor.execute(() -> {
            try {
                Map<String, String> params = new HashMap<>();
                params.put("q", query);
                JsonArray array = api.getArray(
                        PlatformApi.API_PREFIX + "/chats/" + chatId + "/messages/search", params);
                List<JsonObject> result = new ArrayList<>();
                for (JsonElement element : array) {
                    result.add(element.getAsJsonObject());
                }
                callback.onSuccess(result);
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    // --------------------------------------------------------------- sending

    /**
     * Sends a message. The call returns immediately with the local id so the bubble
     * appears at once; if the network rejects the request the payload goes to the
     * outbox and is retried automatically.
     */
    public void send(final String chatId, final String text, final String replyToMessageId,
                     final Callback<String> callback) {
        final String clientMessageId = UUID.randomUUID().toString();
        final JsonObject body = new JsonObject();
        body.addProperty("type", "text");
        body.addProperty("text", text);
        body.addProperty("client_message_id", clientMessageId);
        if (replyToMessageId != null) {
            JsonObject reply = new JsonObject();
            reply.addProperty("message_id", replyToMessageId);
            body.add("reply_to", reply);
        }
        executor.execute(() -> {
            try {
                JsonObject message = api.post(
                        PlatformApi.API_PREFIX + "/chats/" + chatId + "/messages", body);
                store.saveMessages(chatId, single(message.toString()));
                callback.onSuccess(clientMessageId);
            } catch (PlatformException e) {
                if (e.isRetryable()) {
                    store.enqueueOutbound(clientMessageId, chatId, body.toString());
                    scheduleOutboxFlush();
                    callback.onSuccess(clientMessageId); // still "pending" in the UI
                } else {
                    callback.onError(e);
                }
            }
        });
    }

    public void edit(final String chatId, final String messageId, final String text,
                     final Callback<JsonObject> callback) {
        executor.execute(() -> {
            try {
                JsonObject body = new JsonObject();
                body.addProperty("text", text);
                JsonObject updated = api.patch(
                        PlatformApi.API_PREFIX + "/chats/" + chatId + "/messages/" + messageId, body);
                store.saveMessages(chatId, single(updated.toString()));
                callback.onSuccess(updated);
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    public void delete(final String chatId, final String messageId, final boolean forEveryone,
                       final Callback<Void> callback) {
        executor.execute(() -> {
            try {
                api.delete(PlatformApi.API_PREFIX + "/chats/" + chatId + "/messages/" + messageId
                        + "?for_everyone=" + forEveryone);
                store.markMessageDeleted(chatId, messageId);
                callback.onSuccess(null);
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    public void forward(final String messageId, final List<String> chatIds,
                        final Callback<List<JsonObject>> callback) {
        executor.execute(() -> {
            try {
                JsonObject body = new JsonObject();
                JsonArray array = new JsonArray();
                for (String id : chatIds) {
                    array.add(id);
                }
                body.add("chat_ids", array);
                JsonElement response = api.post(
                        PlatformApi.API_PREFIX + "/messages/" + messageId + "/forward", body);
                List<JsonObject> created = new ArrayList<>();
                if (response != null && response.isJsonArray()) {
                    for (JsonElement element : response.getAsJsonArray()) {
                        created.add(element.getAsJsonObject());
                    }
                }
                callback.onSuccess(created);
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    /**
     * Sends everything queued while offline. Called on reconnect and by a timer,
     * so a message composed in airplane mode still arrives.
     */
    public void flushOutbox() {
        executor.execute(() -> {
            for (LocalStore.Outbound pending : store.pendingOutbound()) {
                JsonObject body = api.gson().fromJson(pending.payloadJson, JsonObject.class);
                try {
                    JsonObject sent = api.post(
                            PlatformApi.API_PREFIX + "/chats/" + pending.chatId + "/messages", body);
                    store.removeOutbound(pending.clientMessageId);
                    store.saveMessages(pending.chatId, single(sent.toString()));
                } catch (PlatformException e) {
                    if (e.isRetryable()) {
                        store.bumpOutboundAttempts(pending.clientMessageId, e.getMessage());
                    } else {
                        // Permanent rejection: drop it so the UI can show "failed".
                        store.removeOutbound(pending.clientMessageId);
                    }
                }
            }
        });
    }

    private void scheduleOutboxFlush() {
        retryScheduler.schedule(this::flushOutbox, 20, TimeUnit.SECONDS);
    }

    // ---------------------------------------------------------------- realtime

    /** Applies a pushed event to the cache before the UI is notified. */
    public void applyRealtimeEvent(String type, JsonObject data) {
        if ("message.new".equals(type) || "message.edited".equals(type)) {
            if (data.has("chat_id") && data.has("id")) {
                store.saveMessages(data.get("chat_id").getAsString(), single(data.toString()));
            }
        } else if ("message.deleted".equals(type)) {
            if (data.has("chat_id") && data.has("id")) {
                store.markMessageDeleted(data.get("chat_id").getAsString(),
                        data.get("id").getAsString());
            }
        }
    }

    public void sendTyping(String chatId) {
        realtime.sendTyping(chatId);
    }

    public void markRead(String chatId, long seq) {
        realtime.sendRead(chatId, seq);
    }

    public void shutdown() {
        retryScheduler.shutdownNow();
    }

    private static List<String> single(String json) {
        List<String> list = new ArrayList<>(1);
        list.add(json);
        return list;
    }
}
