package org.telegram.platform.store;

import java.util.List;

/**
 * Offline cache + outbox. The concrete implementation is SQLite so the dialog
 * list and chat history survive process death and airplane mode.
 */
public interface LocalStore {

    /** Replaces the cached dialog list (ordered as returned by the server). */
    void saveChats(List<String> chatJson);

    List<String> loadChats();

    void saveChat(String chatJson);

    String loadChat(String chatId);

    /** Inserts or updates messages of one chat (server order preserved). */
    void saveMessages(String chatId, List<String> messageJson);

    List<String> loadMessages(String chatId, int limit);

    void markMessageDeleted(String chatId, String messageId);

    // ---- outbox: messages composed while offline or while a send failed

    void enqueueOutbound(String clientMessageId, String chatId, String payloadJson);

    List<Outbound> pendingOutbound();

    void removeOutbound(String clientMessageId);

    void bumpOutboundAttempts(String clientMessageId, String lastError);

    // ---- key/value (last synced seq per chat, feature flags, id map)

    void putState(String key, String value);

    String getState(String key);

    final class Outbound {
        public final String clientMessageId;
        public final String chatId;
        public final String payloadJson;
        public final int attempts;
        public final String lastError;

        public Outbound(String clientMessageId, String chatId, String payloadJson, int attempts,
                        String lastError) {
            this.clientMessageId = clientMessageId;
            this.chatId = chatId;
            this.payloadJson = payloadJson;
            this.attempts = attempts;
            this.lastError = lastError;
        }
    }
}
