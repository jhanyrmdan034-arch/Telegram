package org.telegram.platform.store;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Non-persistent {@link LocalStore} used by unit tests and as a last-resort fallback. */
public final class MemoryLocalStore implements LocalStore {

    private final List<String> chats = new ArrayList<>();
    private final Map<String, String> chatById = new LinkedHashMap<>();
    private final Map<String, List<String>> messages = new LinkedHashMap<>();
    private final Map<String, Outbound> outbox = new LinkedHashMap<>();
    private final Map<String, String> state = new LinkedHashMap<>();

    @Override
    public synchronized void saveChats(List<String> chatJson) {
        chats.clear();
        chats.addAll(chatJson);
    }

    @Override
    public synchronized List<String> loadChats() {
        return new ArrayList<>(chats);
    }

    @Override
    public synchronized void saveChat(String chatJson) {
        String id = extractId(chatJson);
        if (id != null) {
            chatById.put(id, chatJson);
        }
    }

    @Override
    public synchronized String loadChat(String chatId) {
        return chatById.get(chatId);
    }

    @Override
    public synchronized void saveMessages(String chatId, List<String> messageJson) {
        List<String> existing = messages.get(chatId);
        if (existing == null) {
            existing = new ArrayList<>();
            messages.put(chatId, existing);
        }
        existing.addAll(0, messageJson);
    }

    @Override
    public synchronized List<String> loadMessages(String chatId, int limit) {
        List<String> existing = messages.get(chatId);
        if (existing == null) {
            return new ArrayList<>();
        }
        return new ArrayList<>(existing.subList(0, Math.min(limit, existing.size())));
    }

    @Override
    public synchronized void markMessageDeleted(String chatId, String messageId) {
        // Tests assert on chat state rather than rendering, so removal is enough.
        List<String> existing = messages.get(chatId);
        if (existing != null) {
            existing.removeIf(json -> json.contains("\"id\":\"" + messageId + "\""));
        }
    }

    @Override
    public synchronized void enqueueOutbound(String clientMessageId, String chatId,
                                             String payloadJson) {
        outbox.put(clientMessageId, new Outbound(clientMessageId, chatId, payloadJson, 0, null));
    }

    @Override
    public synchronized List<Outbound> pendingOutbound() {
        return new ArrayList<>(outbox.values());
    }

    @Override
    public synchronized void removeOutbound(String clientMessageId) {
        outbox.remove(clientMessageId);
    }

    @Override
    public synchronized void bumpOutboundAttempts(String clientMessageId, String lastError) {
        Outbound current = outbox.get(clientMessageId);
        if (current != null) {
            outbox.put(clientMessageId, new Outbound(current.clientMessageId, current.chatId,
                    current.payloadJson, current.attempts + 1, lastError));
        }
    }

    @Override
    public synchronized void putState(String key, String value) {
        state.put(key, value);
    }

    @Override
    public synchronized String getState(String key) {
        return state.get(key);
    }

    private static String extractId(String json) {
        int index = json.indexOf("\"id\":");
        if (index < 0) {
            return null;
        }
        int start = json.indexOf('"', index + 5);
        if (start < 0) {
            return null;
        }
        int end = json.indexOf('"', start + 1);
        return end < 0 ? null : json.substring(start + 1, end);
    }
}
