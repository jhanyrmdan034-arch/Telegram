package org.telegram.platform;

/**
 * Every failure of the platform client is expressed as one of these kinds so the
 * UI layer can react without string matching.
 */
public class PlatformException extends Exception {

    public enum Kind {
        /** No connectivity; the caller should keep the request in the outbox. */
        OFFLINE,
        /** Socket/timeout failure. Safe to retry with backoff. */
        NETWORK,
        /** 401 after refresh attempt: the session is gone, force re-login. */
        UNAUTHORIZED,
        /** 403: authenticated but not allowed (blocked account, not a member, ...). */
        FORBIDDEN,
        /** 404: referenced entity does not exist. */
        NOT_FOUND,
        /** 409: conflict such as a duplicate username. */
        CONFLICT,
        /** 422: validation problem; the message is user-presentable. */
        VALIDATION,
        /** 429: rate limited; retry after the advertised delay. */
        RATE_LIMITED,
        /** 5xx or malformed payload. */
        SERVER,
        /** Anything else (programming error). */
        UNKNOWN
    }

    private final Kind kind;
    private final int statusCode;
    private final String serverDetail;
    private final long retryAfterSeconds;

    public PlatformException(Kind kind, int statusCode, String message, String serverDetail) {
        this(kind, statusCode, message, serverDetail, 0L);
    }

    public PlatformException(Kind kind, int statusCode, String message, String serverDetail,
                             long retryAfterSeconds) {
        super(message);
        this.kind = kind;
        this.statusCode = statusCode;
        this.serverDetail = serverDetail;
        this.retryAfterSeconds = retryAfterSeconds;
    }

    /** Failure detected before a response existed (socket, timeout, cancel). */
    public PlatformException(String message, int statusCode, Throwable cause) {
        this(kindForStatus(statusCode), statusCode, message, null, 0L);
        if (cause != null) {
            initCause(cause);
        }
    }

    public Kind kind() {
        return kind;
    }

    public int statusCode() {
        return statusCode;
    }

    /** Message returned by the API (already safe to show to a person). */
    public String serverDetail() {
        return serverDetail;
    }

    public long retryAfterSeconds() {
        return retryAfterSeconds;
    }

    public boolean isRetryable() {
        return kind == Kind.NETWORK || kind == Kind.OFFLINE || kind == Kind.RATE_LIMITED
                || kind == Kind.SERVER;
    }

    public boolean requiresLogin() {
        return kind == Kind.UNAUTHORIZED;
    }

    public static Kind kindForStatus(int status) {
        switch (status) {
            case 401:
                return Kind.UNAUTHORIZED;
            case 403:
                return Kind.FORBIDDEN;
            case 404:
                return Kind.NOT_FOUND;
            case 409:
                return Kind.CONFLICT;
            case 422:
                return Kind.VALIDATION;
            case 429:
                return Kind.RATE_LIMITED;
            default:
                return status >= 500 ? Kind.SERVER : Kind.UNKNOWN;
        }
    }

    @Override
    public String toString() {
        return "PlatformException{" + kind + ", status=" + statusCode + ", message="
                + getMessage() + '}';
    }
}
