package org.telegram.platform.tl;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import org.telegram.messenger.AccountInstance;
import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.BuildVars;
import org.telegram.messenger.FileLog;
import org.telegram.messenger.MessagesController;
import org.telegram.messenger.NotificationCenter;
import org.telegram.platform.PlatformBridge;
import org.telegram.platform.PlatformException;
import org.telegram.platform.repo.Callback;
import org.telegram.platform.repo.UserRepository;
import org.telegram.tgnet.TLRPC;

import java.util.ArrayList;
import java.util.List;

/**
 * Contacts, profiles and global search from our backend.
 *
 * <p>Contacts are installed into the same structures the contacts screen reads
 * ({@code ContactsController.contactsDict} plus the sorted sections) and profiles
 * arrive as the same {@code NotificationCenter.userInfoDidLoad} the profile screen
 * already handles, so no screen is modified.
 */
public final class PlatformContactsSync {

    private PlatformContactsSync() {
    }

    public static boolean isEnabled() {
        return BuildVars.CONTACTS_FROM_PLATFORM && PlatformBridge.isReady();
    }

    // -------------------------------------------------------------- contacts

    public static void loadContacts(final int account) {
        if (!isEnabled()) {
            return;
        }
        PlatformBridge.get().users().contacts(new Callback<List<JsonObject>>() {
            @Override
            public void onSuccess(List<JsonObject> rows) {
                final ArrayList<TLRPC.User> users = new ArrayList<>(rows.size());
                for (JsonObject row : rows) {
                    try {
                        TLRPC.TL_user user = buildUser(row);
                        if (user != null) {
                            users.add(user);
                        }
                    } catch (Throwable t) {
                        FileLog.e("platform contact: " + t);
                    }
                }
                AndroidUtilities.runOnUIThread(() -> {
                    AccountInstance instance = AccountInstance.getInstance(account);
                    if (!users.isEmpty()) {
                        instance.getMessagesController().putUsers(users, false);
                    }
                    instance.getContactsController().platformSetContacts(users);
                    NotificationCenter.getGlobalInstance()
                            .postNotificationName(NotificationCenter.contactsDidLoad, true);
                });
            }

            @Override
            public void onError(PlatformException error) {
                FileLog.e("platform contacts: " + error);
            }
        });
    }

    private static TLRPC.TL_user buildUser(JsonObject row) {
        final PlatformBridge bridge = PlatformBridge.get();
        String id = optString(row, "contact_id");
        if (id == null) {
            id = optString(row, "id");
        }
        if (id == null) {
            return null;
        }
        TLRPC.TL_user user = new TLRPC.TL_user();
        user.id = bridge.ids().toLong(id);
        String display = optString(row, "display_name");
        String username = optString(row, "username");
        user.first_name = display != null ? display : (username == null ? "" : username);
        user.username = username;
        user.self = false;
        user.flags |= 1; // first_name set
        if (username != null) {
            user.flags |= 2; // username set
        }
        String avatar = optString(row, "avatar_file_id");
        if (avatar != null) {
            PlatformAvatars.remember(user.id, avatar);
        }
        return user;
    }

    // --------------------------------------------------------------- profile

    public static void loadProfile(final int account, final TLRPC.User user, final int classGuid) {
        if (!isEnabled() || user == null) {
            return;
        }
        final String userId = PlatformBridge.get().ids().toUuid(user.id);
        if (userId == null) {
            return;
        }
        PlatformBridge.get().users().profile(userId, new Callback<JsonObject>() {
            @Override
            public void onSuccess(JsonObject row) {
                final TLRPC.UserFull full = buildFull(user, row);
                AndroidUtilities.runOnUIThread(() -> NotificationCenter.getGlobalInstance()
                        .postNotificationName(NotificationCenter.userInfoDidLoad, user.id, full));
                PlatformAvatars.remember(user.id, optString(row, "avatar_file_id"));
            }

            @Override
            public void onError(PlatformException error) {
                FileLog.e("platform profile: " + error);
            }
        });
    }

    private static TLRPC.UserFull buildFull(TLRPC.User user, JsonObject row) {
        TLRPC.TL_userFull full = new TLRPC.TL_userFull();
        full.user = user;
        full.about = optString(row, "bio");
        full.flags |= 2; // about
        full.blocked = row.has("presence_visible") && !row.get("presence_visible").getAsBoolean()
                && false; // blocking is surfaced separately; never guessed here
        if (full.about != null) {
            full.flags |= 2;
        }
        return full;
    }

    // -------------------------------------------------------- global search

    /** Fills the search screen with users from our backend; chats come from the local list. */
    public static void searchGlobal(final String query, final Callback<List<TLRPC.User>> callback) {
        if (!isEnabled() || query == null || query.trim().isEmpty()) {
            callback.onSuccess(new ArrayList<TLRPC.User>());
            return;
        }
        PlatformBridge.get().users().searchGlobal(query.trim(), new Callback<JsonObject>() {
            @Override
            public void onSuccess(JsonObject result) {
                ArrayList<TLRPC.User> users = new ArrayList<>();
                if (result != null && result.has("users") && result.get("users").isJsonArray()) {
                    JsonArray array = result.getAsJsonArray("users");
                    for (JsonElement element : array) {
                        if (!element.isJsonObject()) {
                            continue;
                        }
                        JsonObject row = element.getAsJsonObject();
                        try {
                            users.add(PlatformBridge.get().mapper().toUser(row));
                        } catch (Throwable t) {
                            FileLog.e("platform search user: " + t);
                        }
                    }
                }
                if (!users.isEmpty()) {
                    AccountInstance.getInstance(0).getMessagesController().putUsers(users, false);
                }
                callback.onSuccess(users);
            }

            @Override
            public void onError(PlatformException error) {
                FileLog.e("platform search: " + error);
                callback.onSuccess(new ArrayList<TLRPC.User>());
            }
        });
    }

    /** Profile/avatar changes pushed over the socket. */
    public static void onProfileUpdated(JsonObject data) {
        if (!isEnabled() || data == null || !data.has("user_id")) {
            return;
        }
        final int account = 0;
        final String userId = optString(data, "user_id");
        final long mapped = PlatformBridge.get().ids().toLong(userId);
        String avatar = optString(data, "avatar_file_id");
        PlatformAvatars.remember(mapped, avatar);
        AndroidUtilities.runOnUIThread(() -> {
            TLRPC.User user = AccountInstance.getInstance(account).getMessagesController().getUser(mapped);
            if (user != null) {
                String display = optString(data, "display_name");
                if (display != null) {
                    user.first_name = display;
                }
                NotificationCenter.getGlobalInstance().postNotificationName(
                        NotificationCenter.updateInterfaces, MessagesController.UPDATE_MASK_NAME);
            }
            NotificationCenter.getGlobalInstance()
                    .postNotificationName(NotificationCenter.dialogsNeedReload);
        });
    }

    private static String optString(JsonObject json, String field) {
        if (json == null || !json.has(field) || json.get(field).isJsonNull()) {
            return null;
        }
        return json.get(field).getAsString();
    }
}
