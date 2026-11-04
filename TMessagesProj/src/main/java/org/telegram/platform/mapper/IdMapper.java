package org.telegram.platform.mapper;

import org.telegram.platform.store.LocalStore;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * The UI layer addresses everything with numeric ids ({@code TLRPC.Chat.id} is a
 * long, {@code TLRPC.Message.id} is an int) while the backend uses UUIDs.
 *
 * <p>This class produces a stable numeric alias for a UUID and stores the
 * reverse mapping in the local database, so:
 * <ul>
 *   <li>the same UUID always maps to the same number, across restarts</li>
 *   <li>a hash collision is detected and resolved by probing, never silently merged</li>
 *   <li>{@link #toUuid(long)} can always answer "which server object is this row?"</li>
 * </ul>
 * All conversion lives here so the UI never has to know about UUIDs.
 */
public final class IdMapper {

    private static final String PREFIX_LONG = "idmap:l:";
    private static final String PREFIX_INT = "idmap:i:";

    private final LocalStore store;

    public IdMapper(LocalStore store) {
        this.store = store;
    }

    /** Chat / user id space: positive, 62 bits so negation stays free for peers. */
    public long toLong(String uuid) {
        return resolve(uuid, PREFIX_LONG, 0x3FFFFFFFFFFFFFFFL, 62);
    }

    /** Message id space: positive 31 bits to match {@code TLRPC.Message.id}. */
    public int toInt(String uuid) {
        return (int) resolve(uuid, PREFIX_INT, 0x7FFFFFFFL, 31);
    }

    /** Reverse lookup, or {@code null} when the alias was never handed out. */
    public String toUuid(long value) {
        return store.getState(PREFIX_LONG + value);
    }

    public String toUuid(int value) {
        return store.getState(PREFIX_INT + value);
    }

    private long resolve(String uuid, String prefix, long mask, int bits) {
        if (uuid == null) {
            return 0L;
        }
        for (int salt = 0; salt < 8; salt++) {
            long candidate = (hash(uuid + (salt == 0 ? "" : "#" + salt)) & mask);
            if (candidate == 0) {
                candidate = 1L; // 0 means "unset" in TLRPC
            }
            String key = prefix + candidate;
            String existing = store.getState(key);
            if (existing == null) {
                store.putState(key, uuid);
                return candidate;
            }
            if (existing.equals(uuid)) {
                return candidate;
            }
        }
        throw new IllegalStateException("Cannot allocate a numeric id for " + uuid);
    }

    private static long hash(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(value.getBytes(StandardCharsets.UTF_8));
            long result = 0L;
            for (int i = 0; i < 8; i++) {
                result = (result << 8) | (bytes[i] & 0xFF);
            }
            return result;
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }
}
