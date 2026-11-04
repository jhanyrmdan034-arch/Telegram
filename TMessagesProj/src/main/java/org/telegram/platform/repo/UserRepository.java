package org.telegram.platform.repo;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import org.telegram.platform.PlatformException;
import org.telegram.platform.net.PlatformApi;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;

/** Contacts, profiles, privacy, blocking and both kinds of search. */
public final class UserRepository {

    private final PlatformApi api;
    private final ExecutorService executor;

    public UserRepository(PlatformApi api, ExecutorService executor) {
        this.api = api;
        this.executor = executor;
    }

    public List<JsonObject> contactsBlocking() throws PlatformException {
        JsonArray array = api.getArray(PlatformApi.API_PREFIX + "/contacts", null);
        return toList(array);
    }

    public JsonObject profileBlocking(String userId) throws PlatformException {
        return api.getObject(PlatformApi.API_PREFIX + "/users/" + userId, null);
    }

    public List<JsonObject> searchUsersBlocking(String query) throws PlatformException {
        Map<String, String> params = new HashMap<>();
        params.put("q", query);
        return toList(api.getArray(PlatformApi.API_PREFIX + "/users/search", params));
    }

    public JsonObject globalSearchBlocking(String query) throws PlatformException {
        Map<String, String> params = new HashMap<>();
        params.put("q", query);
        return api.getObject(PlatformApi.API_PREFIX + "/search", params);
    }

    public JsonObject updateProfileBlocking(String displayName, String bio, String avatarFileId)
            throws PlatformException {
        JsonObject body = new JsonObject();
        if (displayName != null) {
            body.addProperty("display_name", displayName);
        }
        if (bio != null) {
            body.addProperty("bio", bio);
        }
        if (avatarFileId != null) {
            body.addProperty("avatar_file_id", avatarFileId);
        }
        return api.patch(PlatformApi.API_PREFIX + "/users/me", body);
    }

    public JsonObject updatePrivacyBlocking(String lastSeenPrivacy, String searchPrivacy)
            throws PlatformException {
        JsonObject body = new JsonObject();
        if (lastSeenPrivacy != null) {
            body.addProperty("last_seen_privacy", lastSeenPrivacy);
        }
        if (searchPrivacy != null) {
            body.addProperty("search_privacy", searchPrivacy);
        }
        return api.patch(PlatformApi.API_PREFIX + "/users/me/privacy", body);
    }

    public void addContactBlocking(String userId, String firstName, String lastName)
            throws PlatformException {
        JsonObject body = new JsonObject();
        body.addProperty("contact_id", userId);
        if (firstName != null) {
            body.addProperty("first_name", firstName);
        }
        if (lastName != null) {
            body.addProperty("last_name", lastName);
        }
        api.post(PlatformApi.API_PREFIX + "/contacts", body);
    }

    public void removeContactBlocking(String userId) throws PlatformException {
        api.delete(PlatformApi.API_PREFIX + "/contacts/" + userId);
    }

    public void blockBlocking(String userId) throws PlatformException {
        api.post(PlatformApi.API_PREFIX + "/users/" + userId + "/block", new JsonObject());
    }

    public void unblockBlocking(String userId) throws PlatformException {
        api.delete(PlatformApi.API_PREFIX + "/users/" + userId + "/block");
    }

    public void contacts(final Callback<List<JsonObject>> callback) {
        executor.execute(() -> {
            try {
                callback.onSuccess(contactsBlocking());
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    public void searchGlobal(final String query, final Callback<JsonObject> callback) {
        executor.execute(() -> {
            try {
                callback.onSuccess(globalSearchBlocking(query));
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    public void profile(final String userId, final Callback<JsonObject> callback) {
        executor.execute(() -> {
            try {
                callback.onSuccess(profileBlocking(userId));
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    private static List<JsonObject> toList(JsonArray array) {
        List<JsonObject> rows = new ArrayList<>();
        if (array == null) {
            return rows;
        }
        for (JsonElement element : array) {
            if (element.isJsonObject()) {
                rows.add(element.getAsJsonObject());
            }
        }
        return rows;
    }
}
