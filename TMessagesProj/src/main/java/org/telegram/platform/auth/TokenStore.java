package org.telegram.platform.auth;

/**
 * Persistence for the token pair. Implementations must store the refresh token
 * encrypted at rest: it is a bearer credential with a 30 day lifetime.
 */
public interface TokenStore {

    String accessToken();

    String refreshToken();

    void save(String accessToken, String refreshToken);

    /**
     * Password generated at registration. The login flow is OTP based, but the
     * backend also accepts a password; keeping the generated one lets the user
     * sign in from a second device without inventing a credential by hand.
     */
    void saveAccountPassword(String password);

    String accountPassword();

    void clear();

    /** Simple implementation used by tests and as a fallback when Keystore is unavailable. */
    final class InMemory implements TokenStore {
        private volatile String access;
        private volatile String refresh;
        private volatile String password;

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
        public void saveAccountPassword(String password) {
            this.password = password;
        }

        @Override
        public String accountPassword() {
            return password;
        }

        @Override
        public void clear() {
            this.access = null;
            this.refresh = null;
            this.password = null;
        }
    }
}
