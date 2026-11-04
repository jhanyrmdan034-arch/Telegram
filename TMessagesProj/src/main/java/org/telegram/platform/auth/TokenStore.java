package org.telegram.platform.auth;

/**
 * Persistence for the token pair. Implementations must store the refresh token
 * encrypted at rest: it is a bearer credential with a 30 day lifetime.
 */
public interface TokenStore {

    String accessToken();

    String refreshToken();

    void save(String accessToken, String refreshToken);

    void clear();

    /** Simple implementation used by tests and as a fallback when Keystore is unavailable. */
    final class InMemory implements TokenStore {
        private volatile String access;
        private volatile String refresh;

        @Override
        public String accessToken() {
            return access;
        }

        @Override
        public String refreshToken() {
            return refresh;
        }

        @Override
        public void save(String accessToken, String refreshToken) {
            this.access = accessToken;
            this.refresh = refreshToken;
        }

        @Override
        public void clear() {
            this.access = null;
            this.refresh = null;
        }
    }
}
