package org.telegram.platform.auth;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import org.telegram.platform.PlatformException;
import org.telegram.platform.net.PlatformApi;

import java.util.Collections;

/**
 * Owns the authentication lifecycle against the proprietary backend:
 * password login, registration, OTP sign-in, session listing, logout and restore.
 *
 * <p>The existing LoginActivity can drive this class unchanged in appearance:
 * it collects a phone/username, requests a code, then verifies it.
 */
public final class AuthManager {

    public enum State { SIGNED_OUT, SIGNED_IN }

    public interface Listener {
        void onStateChanged(State state, JsonObject user);

        void onError(PlatformException error);
    }

    private static final String DEVICE_PLATFORM = "android";

    private final PlatformApi api;
    private final TokenStore tokens;
    private volatile Listener listener;
    private volatile JsonObject currentUser;
    private volatile State state = State.SIGNED_OUT;

    public AuthManager(PlatformApi api, TokenStore tokens) {
        this.api = api;
        this.tokens = tokens;
    }

    public void setListener(Listener listener) {
        this.listener = listener;
    }

    public State state() {
        return state;
    }

    public JsonObject currentUser() {
        return currentUser;
    }

    public boolean isSignedIn() {
        return state == State.SIGNED_IN && tokens.accessToken() != null;
    }

    // ------------------------------------------------------------------- flows

    /** Password sign-in (kept for the existing password field, if enabled). */
    public JsonObject login(String username, String password, String deviceName)
            throws PlatformException {
        JsonObject body = new JsonObject();
        body.addProperty("username", username);
        body.addProperty("password", password);
        body.addProperty("device_name", deviceName);
        body.addProperty("platform", DEVICE_PLATFORM);
        JsonObject pair = api.executePublic("POST", PlatformApi.API_PREFIX + "/auth/login", body)
                .getAsJsonObject();
        return applySession(pair);
    }

    /**
     * Creates the account. {@code registrationToken} is the grant returned by
     * {@link #requestOtp} for an unknown number: the backend refuses sign-up
     * without it, so an account cannot be created for a phone that never received
     * a code.
     */
    public JsonObject register(String username, String password, String displayName,
                               String deviceName, String registrationToken)
            throws PlatformException {
        JsonObject body = new JsonObject();
        body.addProperty("username", username);
        body.addProperty("password", password);
        body.addProperty("display_name", displayName);
        body.addProperty("device_name", deviceName);
        body.addProperty("platform", DEVICE_PLATFORM);
        if (registrationToken != null) {
            body.addProperty("registration_token", registrationToken);
        }
        JsonObject pair = api.executePublic("POST", PlatformApi.API_PREFIX + "/auth/register", body)
                .getAsJsonObject();
        return applySession(pair);
    }

    /** Step 1 of OTP sign-in: asks the backend to send a code. */
    public JsonObject requestOtp(String username, String purpose) throws PlatformException {
        JsonObject body = new JsonObject();
        body.addProperty("username", username);
        body.addProperty("purpose", purpose == null ? "login" : purpose);
        return api.executePublic("POST", PlatformApi.API_PREFIX + "/auth/otp/request", body)
                .getAsJsonObject();
    }

    /** Step 2 of OTP sign-in: exchanges the code for a token pair. */
    public JsonObject verifyOtp(String username, String code, String deviceName)
            throws PlatformException {
        JsonObject body = new JsonObject();
        body.addProperty("username", username);
        body.addProperty("code", code);
        body.addProperty("device_name", deviceName);
        body.addProperty("platform", DEVICE_PLATFORM);
        JsonObject pair = api.executePublic("POST", PlatformApi.API_PREFIX + "/auth/otp/verify", body)
                .getAsJsonObject();
        return applySession(pair);
    }

    /** Restores a session persisted from a previous app start. */
    public boolean restore() {
        if (tokens.accessToken() == null && tokens.refreshToken() == null) {
            return false;
        }
        try {
            currentUser = api.getObject(PlatformApi.API_PREFIX + "/users/me", null);
            setState(State.SIGNED_IN, currentUser);
            return true;
        } catch (PlatformException e) {
            if (e.requiresLogin()) {
                tokens.clear();
            }
            setState(State.SIGNED_OUT, null);
            return false;
        }
    }

    public JsonArray sessions() throws PlatformException {
        return api.getArray(PlatformApi.API_PREFIX + "/auth/sessions", null);
    }

    public void revokeSession(String sessionId) throws PlatformException {
        api.delete(PlatformApi.API_PREFIX + "/auth/sessions/" + sessionId);
    }

    public void logout() {
        try {
            api.executePublic("POST", PlatformApi.API_PREFIX + "/auth/logout", new JsonObject());
        } catch (PlatformException ignored) {
            // Logging out locally must succeed even when the network is down.
        } finally {
            tokens.clear();
            currentUser = null;
            setState(State.SIGNED_OUT, null);
        }
    }

    /** Presence heartbeat used by the dialog list ("online" / "last seen"). */
    public void touchPresence() {
        try {
            api.getObject(PlatformApi.API_PREFIX + "/users/me", Collections.emptyMap());
        } catch (PlatformException ignored) {
            // Presence is best effort.
        }
    }

    // ---------------------------------------------------------------- internal

    private JsonObject applySession(JsonObject pair) throws PlatformException {
        if (pair == null || !pair.has("access_token") || !pair.has("refresh_token")) {
            throw new PlatformException(PlatformException.Kind.SERVER, 0,
                    "Malformed authentication response", null);
        }
        tokens.save(pair.get("access_token").getAsString(), pair.get("refresh_token").getAsString());
        try {
            currentUser = api.getObject(PlatformApi.API_PREFIX + "/users/me", null);
        } catch (PlatformException e) {
            tokens.clear();
            throw e;
        }
        setState(State.SIGNED_IN, currentUser);
        return currentUser;
    }

    private void setState(State newState, JsonObject user) {
        this.state = newState;
        Listener target = listener;
        if (target != null) {
            target.onStateChanged(newState, user);
        }
    }

    public JsonElement parse(String json) {
        return api.gson().fromJson(json, JsonElement.class);
    }
}
