package org.telegram.platform.tl;

import android.os.Build;

import com.google.gson.JsonObject;

import org.telegram.messenger.FileLog;
import org.telegram.messenger.Utilities;
import org.telegram.platform.PlatformBridge;
import org.telegram.platform.PlatformException;
import org.telegram.platform.auth.PhoneIdentity;
import org.telegram.tgnet.ConnectionsManager;
import org.telegram.tgnet.TLObject;
import org.telegram.tgnet.TLRPC;

import java.util.Locale;
import java.util.UUID;

/**
 * Translates the authentication TL requests that {@code LoginActivity} already
 * sends into calls on our own backend, and synthesises the TL responses the
 * existing screens expect.
 *
 * <p>This is the whole phase 2 integration: the login screens, their layout,
 * animations and validation are untouched. Only the transport underneath them
 * changes, because {@code ConnectionsManager} asks this class first:
 *
 * <pre>
 *   LoginActivity ── TL_auth_sendCode ──┐
 *                  TL_auth_signIn        ├─► PlatformAuthInterceptor ──► REST + OTP
 *                  TL_auth_signUp        │        │
 *                  TL_auth_resendCode ───┘        └─► TL_auth_sentCode / TL_auth_authorization
 * </pre>
 *
 * <p>Phone numbers stay the user-facing identity; {@link PhoneIdentity} maps them
 * to the username the backend stores. No mock data is produced anywhere: every
 * response below is built from a real HTTP answer.
 */
public final class PlatformAuthInterceptor {

    private static final String TAG = "PlatformAuth";

    private PlatformAuthInterceptor() {
    }

    /** True when the request was taken over; the MTProto transport must then be skipped. */
    public static boolean intercept(TLObject request, ConnectionsManager.RequestDelegate onComplete) {
        if (request == null || onComplete == null || !PlatformBridge.isReady()) {
            return false;
        }
        if (!isAuthRequest(request)) {
            return false;
        }
        final PlatformBridge bridge = PlatformBridge.get();
        bridge.executor().execute(() -> {
            TLObject response = null;
            TLRPC.TL_error error = null;
            try {
                response = handle(bridge, request);
            } catch (PlatformException e) {
                error = toTlError(e);
            } catch (Throwable t) {
                FileLog.e(t);
                error = new TLRPC.TL_error(400, "INTERNAL_ERROR");
            }
            final TLObject finalResponse = response;
            final TLRPC.TL_error finalError = error;
            // ConnectionsManager delivers real responses on the stage queue; keep the
            // same contract so LoginActivity's existing callbacks behave identically.
            Utilities.stageQueue.postRunnable(() -> {
                if (finalResponse != null || finalError != null) {
                    onComplete.run(finalResponse, finalError);
                }
            });
        });
        return true;
    }

    private static boolean isAuthRequest(TLObject request) {
        return request instanceof TLRPC.TL_auth_sendCode
                || request instanceof TLRPC.TL_auth_resendCode
                || request instanceof TLRPC.TL_auth_signIn
                || request instanceof TLRPC.TL_auth_signUp;
    }

    // ------------------------------------------------------------------ dispatch

    private static TLObject handle(PlatformBridge bridge, TLObject request) throws PlatformException {
        if (request instanceof TLRPC.TL_auth_sendCode) {
            return sendCode(bridge, ((TLRPC.TL_auth_sendCode) request).phone_number);
        }
        if (request instanceof TLRPC.TL_auth_resendCode) {
            return sendCode(bridge, ((TLRPC.TL_auth_resendCode) request).phone_number);
        }
        if (request instanceof TLRPC.TL_auth_signIn) {
            TLRPC.TL_auth_signIn signIn = (TLRPC.TL_auth_signIn) request;
            return signIn(bridge, signIn.phone_number, signIn.phone_code);
        }
        if (request instanceof TLRPC.TL_auth_signUp) {
            TLRPC.TL_auth_signUp signUp = (TLRPC.TL_auth_signUp) request;
            return signUp(bridge, signUp.phone_number, signUp.first_name, signUp.last_name);
        }
        throw new PlatformException(PlatformException.Kind.UNKNOWN, 0, "Unsupported request", null);
    }

    // ------------------------------------------------------------------- sendCode

    private static TLObject sendCode(PlatformBridge bridge, String phone) throws PlatformException {
        String username = PhoneIdentity.usernameFor(phone);
        if (username == null) {
            throw new PlatformException(PlatformException.Kind.VALIDATION, 422,
                    "Invalid phone number", "PHONE_NUMBER_INVALID");
        }
        try {
            JsonObject challenge = bridge.auth().requestOtp(username, "login");
            return sentCode(phone, challenge);
        } catch (PlatformException e) {
            if (e.kind() == PlatformException.Kind.NOT_FOUND) {
                // The account does not exist yet: the UI shows its own sign-up page.
                TLRPC.TL_auth_sentCodeSuccess success = new TLRPC.TL_auth_sentCodeSuccess();
                TLRPC.TL_auth_authorizationSignUpRequired required =
                        new TLRPC.TL_auth_authorizationSignUpRequired();
                success.authorization = required;
                return success;
            }
            throw e;
        }
    }

    private static TLRPC.TL_auth_sentCode sentCode(String phone, JsonObject challenge)
            throws PlatformException {
        TLRPC.TL_auth_sentCode sentCode = new TLRPC.TL_auth_sentCode();
        TLRPC.TL_auth_sentCodeTypeSms type = new TLRPC.TL_auth_sentCodeTypeSms();
        type.length = codeLength(challenge);
        sentCode.type = type;
        sentCode.phone_code_hash = PhoneIdentity.codeHash(phone);
        sentCode.next_type = new TLRPC.TL_auth_codeTypeSms();
        sentCode.timeout = challenge != null && challenge.has("expires_in")
                ? challenge.get("expires_in").getAsInt() : 300;
        return sentCode;
    }

    private static int codeLength(JsonObject challenge) {
        // Backend codes are six digits (see app/routers/auth.py).
        return 6;
    }

    // --------------------------------------------------------------------- signIn

    private static TLObject signIn(PlatformBridge bridge, String phone, String code)
            throws PlatformException {
        String username = PhoneIdentity.usernameFor(phone);
        if (username == null) {
            throw new PlatformException(PlatformException.Kind.VALIDATION, 422,
                    "Invalid phone number", "PHONE_NUMBER_INVALID");
        }
        if (code == null || code.trim().isEmpty()) {
            throw new PlatformException(PlatformException.Kind.VALIDATION, 422,
                    "Empty code", "PHONE_CODE_EMPTY");
        }
        JsonObject user = bridge.auth().verifyOtp(username, code.trim(), deviceName());
        return authorization(bridge, user, phone);
    }

    // --------------------------------------------------------------------- signUp

    private static TLObject signUp(PlatformBridge bridge, String phone, String firstName,
                                   String lastName) throws PlatformException {
        String username = PhoneIdentity.usernameFor(phone);
        if (username == null) {
            throw new PlatformException(PlatformException.Kind.VALIDATION, 422,
                    "Invalid phone number", "PHONE_NUMBER_INVALID");
        }
        if (firstName == null || firstName.trim().isEmpty()) {
            throw new PlatformException(PlatformException.Kind.VALIDATION, 422,
                    "Invalid first name", "FIRSTNAME_INVALID");
        }
        String displayName = (firstName.trim() + " " + (lastName == null ? "" : lastName.trim())).trim();
        // A random, device-generated password: the user never types it, but it lets the
        // account also be opened with a password from another device if needed.
        String password = UUID.randomUUID().toString().replace("-", "")
                + UUID.randomUUID().toString().replace("-", "");
        try {
            JsonObject user = bridge.auth().register(username, password, displayName, deviceName());
            bridge.tokens().saveAccountPassword(password);
            return authorization(bridge, user, phone);
        } catch (PlatformException e) {
            if (e.kind() == PlatformException.Kind.CONFLICT) {
                throw new PlatformException(PlatformException.Kind.CONFLICT, 409,
                        "Account already exists", "PHONE_NUMBER_OCCUPIED");
            }
            throw e;
        }
    }

    // ------------------------------------------------------------------ responses

    private static TLRPC.TL_auth_authorization authorization(PlatformBridge bridge, JsonObject user,
                                                             String phone) throws PlatformException {
        if (user == null || !user.has("id")) {
            throw new PlatformException(PlatformException.Kind.SERVER, 0,
                    "Malformed user payload", "INTERNAL_ERROR");
        }
        TLRPC.TL_auth_authorization result = new TLRPC.TL_auth_authorization();
        result.user = toTlUser(bridge, user, phone);
        result.setup_password_required = false;
        result.otherwise_relogin_days = 180;
        result.tmp_sessions = 0;

        // The session is live from this moment on: open the realtime channel.
        bridge.realtime().connect();
        return result;
    }

    private static TLRPC.TL_user toTlUser(PlatformBridge bridge, JsonObject user, String phone) {
        TLRPC.TL_user tlUser = new TLRPC.TL_user();
        tlUser.id = bridge.ids().toLong(string(user, "id"));
        String display = string(user, "display_name");
        if (display == null) {
            display = string(user, "username");
        }
        if (display != null) {
            int space = display.indexOf(' ');
            if (space > 0) {
                tlUser.first_name = display.substring(0, space);
                tlUser.last_name = display.substring(space + 1);
            } else {
                tlUser.first_name = display;
                tlUser.last_name = "";
            }
        } else {
            tlUser.first_name = "";
            tlUser.last_name = "";
        }
        tlUser.username = string(user, "username");
        tlUser.phone = PhoneIdentity.digits(phone);
        TLRPC.TL_userStatusRecently status = new TLRPC.TL_userStatusRecently();
        tlUser.status = status;
        return tlUser;
    }

    private static String string(JsonObject json, String field) {
        if (json == null || !json.has(field) || json.get(field).isJsonNull()) {
            return null;
        }
        return json.get(field).getAsString();
    }

    // ---------------------------------------------------------------- error mapping

    /**
     * Turns the transport error into the exact TL error texts the login screens
     * already understand, so their existing alerts and page transitions are reused.
     */
    static TLRPC.TL_error toTlError(PlatformException e) {
        switch (e.kind()) {
            case OFFLINE:
            case NETWORK:
                return new TLRPC.TL_error(-1, "NETWORK_ERROR");
            case VALIDATION:
                return new TLRPC.TL_error(400, e.serverDetail() == null ? "PHONE_NUMBER_INVALID"
                        : e.serverDetail());
            case CONFLICT:
                return new TLRPC.TL_error(400, e.serverDetail() == null ? "PHONE_NUMBER_OCCUPIED"
                        : e.serverDetail());
            case NOT_FOUND:
                return new TLRPC.TL_error(400, "PHONE_NUMBER_UNOCCUPIED");
            case FORBIDDEN:
                return new TLRPC.TL_error(403, "PHONE_NUMBER_BANNED");
            case UNAUTHORIZED:
                return new TLRPC.TL_error(400, "PHONE_CODE_INVALID");
            case RATE_LIMITED: {
                long seconds = e.retryAfterSeconds() > 0 ? e.retryAfterSeconds() : 60L;
                return new TLRPC.TL_error(420, "FLOOD_WAIT_" + seconds);
            }
            case SERVER:
            default:
                return new TLRPC.TL_error(500, "INTERNAL_ERROR");
        }
    }

    private static String deviceName() {
        try {
            String model = Build.MODEL == null ? "Android" : Build.MODEL;
            String manufacturer = Build.MANUFACTURER == null ? "" : Build.MANUFACTURER;
            String name = (manufacturer + " " + model).trim();
            if (name.length() > 40) {
                name = name.substring(0, 40);
            }
            return name + " " + Build.VERSION.RELEASE;
        } catch (Throwable t) {
            return String.format(Locale.US, "Android %d", Build.VERSION.SDK_INT);
        }
    }
}
