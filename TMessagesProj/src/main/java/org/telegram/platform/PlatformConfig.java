/*
 * Proprietary messaging platform — Android transport layer.
 * This module replaces Telegram's MTProto stack for the custom backend.
 * It intentionally contains no reference to ConnectionsManager or tgnet.
 */
package org.telegram.platform;

/**
 * Immutable configuration for the platform client.
 *
 * <p>The base URL and WebSocket URL are read from {@code BuildVars} in the app so
 * that a build can point at staging or production without code changes.
 */
public final class PlatformConfig {

    public final String httpBaseUrl;
    public final String websocketUrl;
    public final int connectTimeoutMs;
    public final int readTimeoutMs;
    public final int maxUploadBytes;

    private PlatformConfig(Builder builder) {
        this.httpBaseUrl = stripTrailingSlash(builder.httpBaseUrl);
        this.websocketUrl = builder.websocketUrl;
        this.connectTimeoutMs = builder.connectTimeoutMs;
        this.readTimeoutMs = builder.readTimeoutMs;
        this.maxUploadBytes = builder.maxUploadBytes;
    }

    private static String stripTrailingSlash(String value) {
        if (value == null) {
            throw new IllegalArgumentException("httpBaseUrl is required");
        }
        String trimmed = value.trim();
        while (trimmed.endsWith("/")) {
            trimmed = trimmed.substring(0, trimmed.length() - 1);
        }
        return trimmed;
    }

    /** Builds the absolute URL for an API path such as {@code /api/v1/chats}. */
    public String url(String path) {
        return httpBaseUrl + path;
    }

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private String httpBaseUrl = "http://10.0.2.2:8000";
        private String websocketUrl = "ws://10.0.2.2:8000/api/v1/ws";
        private int connectTimeoutMs = 15_000;
        private int readTimeoutMs = 30_000;
        private int maxUploadBytes = 200 * 1024 * 1024;

        public Builder httpBaseUrl(String value) {
            this.httpBaseUrl = value;
            return this;
        }

        public Builder websocketUrl(String value) {
            this.websocketUrl = value;
            return this;
        }

        public Builder timeouts(int connectTimeoutMs, int readTimeoutMs) {
            this.connectTimeoutMs = connectTimeoutMs;
            this.readTimeoutMs = readTimeoutMs;
            return this;
        }

        public Builder maxUploadBytes(int value) {
            this.maxUploadBytes = value;
            return this;
        }

        public PlatformConfig build() {
            return new PlatformConfig(this);
        }
    }
}
