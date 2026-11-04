package org.telegram.platform.repo;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import org.telegram.platform.PlatformException;
import org.telegram.platform.net.PlatformApi;
import org.telegram.platform.store.LocalStore;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;

/**
 * Dialog list, chat folders and pinning — everything the main screen renders.
 * Reads come from the SQLite cache first (instant paint) and are then refreshed
 * from the API, which is the pattern the existing UI expects.
 */
public final class ChatRepository {

    private final PlatformApi api;
    private final LocalStore store;
    private final ExecutorService executor;

    public ChatRepository(PlatformApi api, LocalStore store, ExecutorService executor) {
        this.api = api;
        this.store = store;
        this.executor = executor;
    }

    /** Cached dialogs, newest/pinned first, available without any network. */
    public List<JsonObject> cachedChats() {
        List<JsonObject> result = new ArrayList<>();
        for (String json : store.loadChats()) {
            result.add(api.gson().fromJson(json, JsonObject.class));
        }
        return result;
    }

    public void refreshChats(final Callback<List<JsonObject>> callback) {
        executor.execute(() -> {
            try {
                JsonArray array = api.getArray(PlatformApi.API_PREFIX + "/chats", null);
                List<String> raw = new ArrayList<>();
                List<JsonObject> parsed = new ArrayList<>();
                for (JsonElement element : array) {
                    JsonObject chat = element.getAsJsonObject();
                    raw.add(chat.toString());
                    parsed.add(chat);
                }
                store.saveChats(raw);
                callback.onSuccess(parsed);
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    /** One page of dialogs; the UI loads more while it scrolls. */
    public com.google.gson.JsonArray dialogPage(int limit, int offset) throws PlatformException {
        Map<String, String> params = new HashMap<>();
        params.put("limit", String.valueOf(limit));
        params.put("offset", String.valueOf(offset));
        return api.getArray(PlatformApi.API_PREFIX + "/chats", params);
    }

    public void pinnedChats(final Callback<List<JsonObject>> callback) {
        executor.execute(() -> {
            try {
                callback.onSuccess(toList(api.getArray(PlatformApi.API_PREFIX + "/chats/pinned",
                        null)));
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    public void pin(final String chatId, final boolean pinned, final Callback<Void> callback) {
        executor.execute(() -> {
            try {
                JsonObject body = new JsonObject();
                body.addProperty("pinned", pinned);
                api.post(PlatformApi.API_PREFIX + "/chats/" + chatId + "/pin", body);
                callback.onSuccess(null);
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    /** Opens (or reuses) the private dialog with another user. */
    public void openPrivateChat(final String userId, final Callback<JsonObject> callback) {
        executor.execute(() -> {
            try {
                JsonObject body = new JsonObject();
                body.addProperty("type", "private");
                JsonArray members = new JsonArray();
                members.add(userId);
                body.add("member_ids", members);
                JsonObject chat = api.post(PlatformApi.API_PREFIX + "/chats", body);
                store.saveChat(chat.toString());
                callback.onSuccess(chat);
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    // ------------------------------------------------------------- folders tab

    /** Synchronous variants used by the dialog-list actions. */
    public void pinBlocking(String chatId, boolean pinned) throws PlatformException {
        com.google.gson.JsonObject body = new com.google.gson.JsonObject();
        body.addProperty("pinned", pinned);
        api.post(PlatformApi.API_PREFIX + "/chats/" + chatId + "/pin", body);
    }

    public void markReadBlocking(String chatId, int seq) throws PlatformException {
        api.post(PlatformApi.API_PREFIX + "/chats/" + chatId + "/read?seq=" + Math.max(0, seq),
                new com.google.gson.JsonObject());
    }

    public void archiveBlocking(String chatId, boolean archived) throws PlatformException {
        com.google.gson.JsonObject body = new com.google.gson.JsonObject();
        body.addProperty("archived", archived);
        api.post(PlatformApi.API_PREFIX + "/chats/" + chatId + "/archive", body);
    }

    public void deleteBlocking(String chatId) throws PlatformException {
        api.delete(PlatformApi.API_PREFIX + "/chats/" + chatId);
    }

    public void folders(final Callback<List<JsonObject>> callback) {
        executor.execute(() -> {
            try {
                callback.onSuccess(toList(api.getArray(PlatformApi.API_PREFIX + "/folders", null)));
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    public void createFolder(final String name, final int position,
                             final Callback<JsonObject> callback) {
        executor.execute(() -> {
            try {
                JsonObject body = new JsonObject();
                body.addProperty("name", name);
                body.addProperty("position", position);
                callback.onSuccess(api.post(PlatformApi.API_PREFIX + "/folders", body));
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    public void folderChats(final String folderId, final Callback<List<JsonObject>> callback) {
        executor.execute(() -> {
            try {
                callback.onSuccess(toList(api.getArray(
                        PlatformApi.API_PREFIX + "/folders/" + folderId + "/chats", null)));
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    public void setFolderChats(final String folderId, final List<String> chatIds,
                               final Callback<Void> callback) {
        executor.execute(() -> {
            try {
                JsonObject body = new JsonObject();
                JsonArray array = new JsonArray();
                for (String id : chatIds) {
                    array.add(id);
                }
                body.add("chat_ids", array);
                api.put(PlatformApi.API_PREFIX + "/folders/" + folderId + "/chats", body);
                callback.onSuccess(null);
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    public void deleteFolder(final String folderId, final Callback<Void> callback) {
        executor.execute(() -> {
            try {
                api.delete(PlatformApi.API_PREFIX + "/folders/" + folderId);
                callback.onSuccess(null);
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    private static List<JsonObject> toList(JsonArray array) {
        List<JsonObject> result = new ArrayList<>();
        for (JsonElement element : array) {
            result.add(element.getAsJsonObject());
        }
        return result;
    }

    /** Convenience for the search bar on the dialog list. */
    public void globalSearchUsers(final String query, final Callback<List<JsonObject>> callback) {
        executor.execute(() -> {
            try {
                Map<String, String> params = new HashMap<>();
                params.put("q", query);
                callback.onSuccess(toList(api.getArray(
                        PlatformApi.API_PREFIX + "/users/search", params)));
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }
}
