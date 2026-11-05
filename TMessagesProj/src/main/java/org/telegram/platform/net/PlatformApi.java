package org.telegram.platform.net;

import com.google.gson.Gson;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import com.google.gson.JsonParser;

import org.telegram.platform.PlatformConfig;
import org.telegram.platform.PlatformException;
import org.telegram.platform.auth.TokenStore;

import java.io.IOException;
import java.io.UnsupportedEncodingException;
import java.net.URLEncoder;
import java.util.HashMap;
import java.util.Map;

/**
 * REST client for the proprietary backend.
 *
 * <p>Responsibilities:
 * <ul>
 *   <li>attach the bearer token and serialise JSON with the app's existing Gson</li>
 *   <li>refresh the access token exactly once when a request comes back 401,
 *       using a single-flight guard so N parallel calls produce one refresh</li>
 *   <li>translate HTTP failures into {@link PlatformException} with a stable kind</li>
 * </ul>
 *
 * <p>All methods are blocking; callers run them on a background executor
 * (see {@code org.telegram.platform.repo.*}).
 */
public final class PlatformApi {

    public static final String API_PREFIX = "/api/v1";

    private final PlatformConfig config;
    private final HttpTransport transport;
    private final TokenStore tokens;
    private final Gson gson = new Gson();
    private final Object refreshLock = new Object();

    public PlatformApi(PlatformConfig config, HttpTransport transport, TokenStore tokens) {
        this.config = config;
        this.transport = transport;
        this.tokens = tokens;
    }

    public PlatformConfig config() {
        return config;
    }

    public Gson gson() {
        return gson;
    }

    // ------------------------------------------------------------------ verbs

    public JsonObject getObject(String path, Map<String, String> query) throws PlatformException {
        JsonElement element = execute("GET", path, query, null, true);
        return element == null ? null : element.getAsJsonObject();
    }

    public com.google.gson.JsonArray getArray(String path, Map<String, String> query)
            throws PlatformException {
        JsonElement element = execute("GET", path, query, null, true);
        return element == null ? new com.google.gson.JsonArray() : element.getAsJsonArray();
    }

    public JsonObject post(String path, JsonObject body) throws PlatformException {
        return asObject(execute("POST", path, null, body, true));
    }

    public JsonObject patch(String path, JsonObject body) throws PlatformException {
        return asObject(execute("PATCH", path, null, body, true));
    }

    public JsonObject put(String path, JsonObject body) throws PlatformException {
        return asObject(execute("PUT", path, null, body, true));
    }

    public void delete(String path) throws PlatformException {
        execute("DELETE", path, null, null, true);
    }

    /** Raw request used by the auth flow, which must not trigger a refresh loop. */
    public JsonElement executePublic(String method, String path, JsonObject body)
            throws PlatformException {
        return execute(method, path, null, body, false);
    }

    private static JsonObject asObject(JsonElement element) {
        return element == null || element.isJsonNull() ? null : element.getAsJsonObject();
    }

    // --------------------------------------------------------------- internals

    private JsonElement execute(String method, String path, Map<String, String> query,
                                JsonObject body, boolean allowRefresh) throws PlatformException {
        String url = buildUrl(path, query);
        Map<String, String> headers = new HashMap<>();
        headers.put("Content-Type", "application/json; charset=utf-8");
        String access = tokens.accessToken();
        if (access != null) {
            headers.put("Authorization", "Bearer " + access);
        }
        byte[] payload = body == null ? null : gson.toJson(body).getBytes(java.nio.charset.StandardCharsets.UTF_8);

        HttpTransport.Response response;
        try {
            response = transport.execute(method, url, headers, payload,
                    config.connectTimeoutMs, config.readTimeoutMs);
        } catch (IOException e) {
            throw new PlatformException(PlatformException.Kind.NETWORK, 0,
                    "Cannot reach the server", e.getMessage());
        }

        if (response.status == 401 && allowRefresh && tokens.refreshToken() != null) {
            if (refreshTokens()) {
                return execute(method, path, query, body, false);
            }
            throw new PlatformException(PlatformException.Kind.UNAUTHORIZED, 401,
                    "Session expired", "Please sign in again");
        }

        if (response.status >= 400) {
            throw toException(response);
        }
        if (response.status == 204 || response.body == null || response.body.isEmpty()) {
            return null;
        }
        try {
            return JsonParser.parseString(response.body);
        } catch (JsonParseException e) {
            throw new PlatformException(PlatformException.Kind.SERVER, response.status,
                    "Malformed response", e.getMessage());
        }
    }

    /**
     * Rotates the refresh token. Only one thread performs the network call; the
     * rest observe the new token and continue.
     */
    private boolean refreshTokens() {
        String failingToken = tokens.refreshToken();
        synchronized (refreshLock) {
            String current = tokens.refreshToken();
            if (current == null) {
                return false;
            }
            if (!current.equals(failingToken)) {
                // Another thread already refreshed while we waited.
                return true;
            }
            JsonObject body = new JsonObject();
            body.addProperty("refresh_token", current);
            try {
                HttpTransport.Response response = transport.execute("POST",
                        config.url(API_PREFIX + "/auth/refresh"), singleHeader("Content-Type",
                                "application/json"), gson.toJson(body).getBytes(java.nio.charset.StandardCharsets.UTF_8),
                        config.connectTimeoutMs, config.readTimeoutMs);
                if (response.status != 200) {
                    tokens.clear();
                    return false;
                }
                JsonObject json = JsonParser.parseString(response.body).getAsJsonObject();
                tokens.save(json.get("access_token").getAsString(),
                        json.get("refresh_token").getAsString());
                return true;
            } catch (IOException | RuntimeException e) {
                return false;
            }
        }
    }

    private static Map<String, String> singleHeader(String key, String value) {
        Map<String, String> map = new HashMap<>();
        map.put(key, value);
        return map;
    }

    private PlatformException toException(HttpTransport.Response response) {
        String detail = null;
        try {
            JsonElement parsed = JsonParser.parseString(response.body);
            if (parsed.isJsonObject() && parsed.getAsJsonObject().has("detail")) {
                JsonElement value = parsed.getAsJsonObject().get("detail");
                detail = value.isJsonPrimitive() ? value.getAsString() : value.toString();
            }
        } catch (RuntimeException ignored) {
            // Non-JSON error body
        }
        long retryAfter = 0L;
        if (response.retryAfterHeader != null) {
            try {
                retryAfter = Long.parseLong(response.retryAfterHeader.trim());
            } catch (NumberFormatException ignored) {
                // header may contain a date instead of seconds
            }
        }
        return new PlatformException(PlatformException.kindForStatus(response.status),
                response.status, detail == null ? "Request failed" : detail, detail, retryAfter);
    }

    private String buildUrl(String path, Map<String, String> query) {
        StringBuilder builder = new StringBuilder(config.url(path));
        if (query != null && !query.isEmpty()) {
            boolean first = true;
            for (Map.Entry<String, String> entry : query.entrySet()) {
                if (entry.getValue() == null) {
                    continue;
                }
                builder.append(first ? '?' : '&');
                first = false;
                builder.append(encode(entry.getKey())).append('=').append(encode(entry.getValue()));
            }
        }
        return builder.toString();
    }

    private static String encode(String value) {
        try {
            return URLEncoder.encode(value, "UTF-8");
        } catch (UnsupportedEncodingException e) {
            return value;
        }
    }
}
