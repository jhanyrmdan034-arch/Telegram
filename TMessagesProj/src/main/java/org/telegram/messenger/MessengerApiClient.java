package org.telegram.messenger;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.text.TextUtils;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.telegram.tgnet.TLObject;
import org.telegram.tgnet.TLRPC;
import org.telegram.tgnet.Vector;
import org.telegram.tgnet.tl.TL_account;
import org.telegram.tgnet.tl.TL_update;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.Key;
import java.security.KeyStore;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * REST/SSE bridge for the existing Telegram data controllers. It deliberately
 * leaves Activities and their layouts untouched. All application data routes
 * through the versioned messenger API when MESSENGER_API_ENABLED is true.
 */
public final class MessengerApiClient {

    public interface RequestCallback {
        void onComplete(TLObject response, TLRPC.TL_error error);
    }

    private static final String SESSION_PREFS = "messenger_api_session_store";
    private static final String SESSION_PREFIX = "encrypted_bearer_";
    private static final String EVENT_CURSOR_PREFIX = "last_account_event_";
    private static final String KEY_ALIAS = "messenger_api_session_aes_v1";
    private static final int CONNECT_TIMEOUT_MS = 10_000;
    private static final int READ_TIMEOUT_MS = 20_000;
    private static final int MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
    private static final int MAX_ACTIVE_STREAMS = 32;
    private static final int SSE_READ_TIMEOUT_MS = 45_000;

    private static final Map<Integer, MessengerApiClient> INSTANCES = new ConcurrentHashMap<>();
    private static final ExecutorService REQUEST_EXECUTOR = Executors.newCachedThreadPool(threadFactory("Messenger API"));
    private static final ExecutorService SSE_EXECUTOR = Executors.newCachedThreadPool(threadFactory("Messenger SSE"));

    private final int currentAccount;
    private final Context appContext;
    private final SharedPreferences sessionPreferences;
    private final ConcurrentHashMap<Long, Long> conversationIdsByPeer = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<Long, AtomicInteger> lastSeenMessageIds = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<Long, SseSubscription> subscriptions = new ConcurrentHashMap<>();
    private final AtomicLong lastAccountEventId;
    private volatile SseSubscription accountSubscription;
    private volatile long authenticatedUserId;

    private MessengerApiClient(int account) {
        currentAccount = account;
        appContext = ApplicationLoader.applicationContext.getApplicationContext();
        sessionPreferences = appContext.getSharedPreferences(SESSION_PREFS, Context.MODE_PRIVATE);
        lastAccountEventId = new AtomicLong(Math.max(0, sessionPreferences.getLong(eventCursorKey(), 0)));
    }

    public static synchronized MessengerApiClient getInstance(int account) {
        MessengerApiClient client = INSTANCES.get(account);
        if (client == null) {
            client = new MessengerApiClient(account);
            INSTANCES.put(account, client);
        }
        return client;
    }

    public static boolean isEnabled() {
        return BuildVars.MESSENGER_API_ENABLED;
    }

    public Future<?> executeRequest(TLObject request, RequestCallback callback) {
        return REQUEST_EXECUTOR.submit(() -> {
            TLObject response = null;
            TLRPC.TL_error error = null;
            try {
                response = dispatch(request);
            } catch (ApiException e) {
                error = new TLRPC.TL_error(e.code, e.message);
            } catch (Throwable e) {
                FileLog.e(e);
                error = new TLRPC.TL_error(500, "API_REQUEST_FAILED");
            }
            if (!Thread.currentThread().isInterrupted() && callback != null) {
                callback.onComplete(response, error);
            } else if (response != null) {
                response.freeResources();
            }
        });
    }

    public void onAuthenticated(long userId) {
        authenticatedUserId = userId;
    }

    /** Revoke the API session asynchronously and remove its encrypted local copy. */
    public void endSession() {
        final String token = getSessionToken();
        stopSubscriptions();
        authenticatedUserId = 0;
        conversationIdsByPeer.clear();
        lastSeenMessageIds.clear();
        lastAccountEventId.set(0);
        sessionPreferences.edit().remove(sessionKey()).remove(eventCursorKey()).apply();
        if (!TextUtils.isEmpty(token)) {
            REQUEST_EXECUTOR.execute(() -> {
                try {
                    performJsonRequest("DELETE", "/api/v1/auth/session", null, token);
                } catch (ApiException ignored) {
                    // The local session is removed even when the server is unreachable.
                }
            });
        }
    }

    private TLObject dispatch(TLObject request) throws Exception {
        if (request instanceof TLRPC.TL_auth_sendCode) {
            return login(((TLRPC.TL_auth_sendCode) request).phone_number);
        }
        if (request instanceof TLRPC.TL_auth_logOut) {
            endSession();
            return new TLRPC.TL_auth_loggedOut();
        }
        if (request instanceof TLRPC.TL_messages_getDialogs) {
            return getDialogs((TLRPC.TL_messages_getDialogs) request);
        }
        if (request instanceof TLRPC.TL_messages_getHistory) {
            return getHistory((TLRPC.TL_messages_getHistory) request);
        }
        if (request instanceof TLRPC.TL_messages_getPeerDialogs) {
            return getPeerDialogs((TLRPC.TL_messages_getPeerDialogs) request);
        }
        if (request instanceof TLRPC.TL_messages_sendMessage) {
            return sendMessage((TLRPC.TL_messages_sendMessage) request);
        }
        if (request instanceof TLRPC.TL_contacts_search) {
            return searchUsers((TLRPC.TL_contacts_search) request);
        }
        if (request instanceof TLRPC.TL_users_getFullUser) {
            return getFullUser((TLRPC.TL_users_getFullUser) request);
        }
        if (request instanceof TLRPC.TL_users_getUsers) {
            return getUsers((TLRPC.TL_users_getUsers) request);
        }
        if (request instanceof TL_account.updateProfile) {
            return updateProfile((TL_account.updateProfile) request);
        }
        if (request instanceof TL_account.updateUsername) {
            return updateUsername((TL_account.updateUsername) request);
        }
        if (request instanceof TL_account.getNotifySettings) {
            TLRPC.TL_peerNotifySettings settings = new TLRPC.TL_peerNotifySettings();
            settings.flags = 1;
            settings.show_previews = true;
            return settings;
        }
        if (request instanceof TL_account.getReactionsNotifySettings) {
            TL_account.TL_reactionsNotifySettings settings = new TL_account.TL_reactionsNotifySettings();
            settings.sound = new TLRPC.TL_notificationSoundDefault();
            settings.show_previews = true;
            return settings;
        }
        if (request instanceof TLRPC.TL_contacts_getContacts) {
            return new TLRPC.TL_contacts_contacts();
        }
        if (request instanceof TLRPC.TL_messages_getDialogUnreadMarks) {
            return new Vector<TLRPC.DialogPeer>(TLRPC.DialogPeer::TLdeserialize);
        }
        if (request instanceof TLRPC.TL_updates_getState) {
            return getUpdatesState();
        }
        throw new ApiException(501, "API_OPERATION_NOT_SUPPORTED");
    }

    private TLRPC.TL_auth_sentCodeSuccess login(String phone) throws ApiException {
        JSONObject body = new JSONObject();
        try {
            body.put("phone", phone == null ? "" : phone);
        } catch (JSONException e) {
            throw new ApiException(400, "PHONE_NUMBER_INVALID");
        }
        JSONObject result = performJsonRequest("POST", "/api/v1/auth/phone", body, null);
        String token = result.optString("accessToken", "");
        if (token.length() < 32) {
            throw new ApiException(502, "INVALID_AUTH_RESPONSE");
        }
        storeSessionToken(token);
        JSONObject profile = result.optJSONObject("user");
        if (profile == null) {
            throw new ApiException(502, "INVALID_AUTH_RESPONSE");
        }
        long userId = profile.optLong("id", 0);
        if (userId <= 0) {
            throw new ApiException(502, "INVALID_AUTH_RESPONSE");
        }
        authenticatedUserId = userId;

        TLRPC.TL_auth_authorization authorization = new TLRPC.TL_auth_authorization();
        authorization.user = toUser(profile, true);
        TLRPC.TL_auth_sentCodeSuccess success = new TLRPC.TL_auth_sentCodeSuccess();
        success.authorization = authorization;
        return success;
    }

    private TLRPC.messages_Dialogs getDialogs(TLRPC.TL_messages_getDialogs request) throws ApiException {
        JSONObject response = performJsonRequest("GET", "/api/v1/conversations", null, null);
        JSONArray conversations = response.optJSONArray("conversations");
        TLRPC.TL_messages_dialogsSlice result = new TLRPC.TL_messages_dialogsSlice();
        result.count = conversations == null ? 0 : conversations.length();
        if (conversations == null) {
            initializeAccountEventCursor(0);
            subscribeAccountEvents();
            return result;
        }

        long latestMessageId = 0;
        Set<Long> includedUsers = new HashSet<>();
        for (int i = 0; i < conversations.length(); i++) {
            JSONObject conversation = conversations.optJSONObject(i);
            if (conversation == null) continue;
            JSONObject peerJson = conversation.optJSONObject("peer");
            if (peerJson == null) continue;
            long peerId = peerJson.optLong("id", 0);
            long conversationId = conversation.optLong("id", 0);
            if (peerId <= 0 || conversationId <= 0) continue;
            conversationIdsByPeer.put(peerId, conversationId);

            TLRPC.TL_user peer = toUser(peerJson, false);
            addUser(result.users, includedUsers, peer);
            JSONObject lastMessageJson = conversation.optJSONObject("lastMessage");
            TLRPC.TL_dialog dialog = new TLRPC.TL_dialog();
            dialog.id = peerId;
            dialog.peer = toPeer(peerId);
            dialog.folder_id = 0;
            dialog.notify_settings = new TLRPC.TL_peerNotifySettings();
            if (lastMessageJson != null) {
                TLRPC.TL_message lastMessage = toMessage(lastMessageJson, peerId);
                dialog.top_message = lastMessage.id;
                dialog.last_message_date = lastMessage.date;
                dialog.read_inbox_max_id = lastMessage.id;
                dialog.read_outbox_max_id = lastMessage.id;
                result.messages.add(lastMessage);
                latestMessageId = Math.max(latestMessageId, lastMessage.id);
                JSONObject sender = lastMessageJson.optJSONObject("sender");
                if (sender != null) {
                    long senderId = sender.optLong("id", 0);
                    TLRPC.User senderUser = senderId == currentUserId() ? currentUser() : toUser(sender, false);
                    addUser(result.users, includedUsers, senderUser);
                }
                observeMessageId(conversationId, lastMessage.id);
            }
            result.dialogs.add(dialog);
        }
        TLRPC.User self = currentUser();
        addUser(result.users, includedUsers, self);
        initializeAccountEventCursor(latestMessageId);
        subscribeAccountEvents();
        return result;
    }

    private TLRPC.messages_Messages getHistory(TLRPC.TL_messages_getHistory request) throws ApiException {
        long peerId = inputPeerUserId(request.peer);
        if (peerId <= 0 || peerId == currentUserId()) {
            return new TLRPC.TL_messages_messagesSlice();
        }
        long conversationId = createOrGetConversation(peerId);
        conversationIdsByPeer.put(peerId, conversationId);

        int limit = request.limit <= 0 ? 50 : Math.min(request.limit, 100);
        String path = "/api/v1/conversations/" + conversationId + "/messages?limit=" + limit;
        int beforeId = request.offset_id != 0 ? request.offset_id : request.max_id;
        if (beforeId > 0) {
            path += "&before=" + beforeId;
        }
        JSONObject response = performJsonRequest("GET", path, null, null);
        JSONArray messagesJson = response.optJSONArray("messages");
        TLRPC.TL_messages_messagesSlice result = new TLRPC.TL_messages_messagesSlice();
        result.count = messagesJson == null ? 0 : messagesJson.length();
        Set<Long> includedUsers = new HashSet<>();
        addUser(result.users, includedUsers, peerFromApi(peerId));
        addUser(result.users, includedUsers, currentUser());
        int newestId = 0;
        if (messagesJson != null) {
            // The existing Telegram history loader expects newest-first responses.
            for (int i = messagesJson.length() - 1; i >= 0; i--) {
                JSONObject messageJson = messagesJson.optJSONObject(i);
                if (messageJson == null) continue;
                TLRPC.TL_message message = toMessage(messageJson, peerId);
                result.messages.add(message);
                newestId = Math.max(newestId, message.id);
                JSONObject sender = messageJson.optJSONObject("sender");
                if (sender != null) {
                    long senderId = sender.optLong("id", 0);
                    addUser(result.users, includedUsers, senderId == currentUserId() ? currentUser() : toUser(sender, false));
                }
            }
        }
        if (newestId > 0) observeMessageId(conversationId, newestId);
        subscribe(conversationId, peerId, lastSeen(conversationId));
        return result;
    }

    private TLRPC.TL_messages_peerDialogs getPeerDialogs(TLRPC.TL_messages_getPeerDialogs request) throws ApiException {
        TLRPC.TL_messages_peerDialogs result = new TLRPC.TL_messages_peerDialogs();
        Set<Long> includedUsers = new HashSet<>();
        for (TLRPC.InputDialogPeer inputDialogPeer : request.peers) {
            if (!(inputDialogPeer instanceof TLRPC.TL_inputDialogPeer)) continue;
            TLRPC.InputPeer inputPeer = ((TLRPC.TL_inputDialogPeer) inputDialogPeer).peer;
            if (inputPeer == null) continue;
            long peerId = inputPeerUserId(inputPeer);
            if (peerId <= 0 || peerId == currentUserId()) continue;
            long conversationId = createOrGetConversation(peerId);
            conversationIdsByPeer.put(peerId, conversationId);
            JSONObject messagesResponse = performJsonRequest("GET", "/api/v1/conversations/" + conversationId + "/messages?limit=1", null, null);
            JSONArray messagesJson = messagesResponse.optJSONArray("messages");

            TLRPC.TL_dialog dialog = new TLRPC.TL_dialog();
            dialog.id = peerId;
            dialog.peer = toPeer(peerId);
            dialog.notify_settings = new TLRPC.TL_peerNotifySettings();
            dialog.read_inbox_max_id = lastSeen(conversationId);
            dialog.read_outbox_max_id = lastSeen(conversationId);
            addUser(result.users, includedUsers, peerFromApi(peerId));
            addUser(result.users, includedUsers, currentUser());
            if (messagesJson != null && messagesJson.length() > 0) {
                JSONObject lastJson = messagesJson.optJSONObject(messagesJson.length() - 1);
                if (lastJson != null) {
                    TLRPC.TL_message lastMessage = toMessage(lastJson, peerId);
                    dialog.top_message = lastMessage.id;
                    dialog.last_message_date = lastMessage.date;
                    dialog.read_inbox_max_id = lastMessage.id;
                    dialog.read_outbox_max_id = lastMessage.id;
                    result.messages.add(lastMessage);
                    observeMessageId(conversationId, lastMessage.id);
                    JSONObject sender = lastJson.optJSONObject("sender");
                    if (sender != null) {
                        long senderId = sender.optLong("id", 0);
                        addUser(result.users, includedUsers, senderId == currentUserId() ? currentUser() : toUser(sender, false));
                    }
                }
            }
            result.dialogs.add(dialog);
            subscribe(conversationId, peerId, lastSeen(conversationId));
        }
        result.state = updatesStateValue();
        return result;
    }

    private TLRPC.TL_updateShortSentMessage sendMessage(TLRPC.TL_messages_sendMessage request) throws ApiException {
        if (request == null || request.peer == null || TextUtils.isEmpty(request.message)) {
            throw new ApiException(400, "MESSAGE_EMPTY");
        }
        if (request.reply_to != null || request.schedule_date != 0 || request.schedule_repeat_period != 0 || request.rich_message != null || request.ephemeralReceiverBotId != 0) {
            throw new ApiException(501, "TEXT_REPLIES_AND_SCHEDULING_NOT_SUPPORTED");
        }
        long peerId = inputPeerUserId(request.peer);
        if (peerId <= 0 || peerId == currentUserId()) {
            throw new ApiException(400, "USER_ID_INVALID");
        }
        long conversationId = createOrGetConversation(peerId);
        conversationIdsByPeer.put(peerId, conversationId);
        JSONObject body = new JSONObject();
        try {
            body.put("text", request.message);
        } catch (JSONException e) {
            throw new ApiException(400, "MESSAGE_INVALID");
        }
        JSONObject response = performJsonRequest("POST", "/api/v1/conversations/" + conversationId + "/messages", body, null);
        JSONObject message = response.optJSONObject("message");
        if (message == null) throw new ApiException(502, "INVALID_MESSAGE_RESPONSE");
        int id = safeInt(message.optLong("id", 0), "MESSAGE_ID_INVALID");
        int date = safeInt(message.optLong("createdAt", 0), "MESSAGE_DATE_INVALID");
        observeMessageId(conversationId, id);
        subscribe(conversationId, peerId, lastSeen(conversationId));

        TLRPC.TL_updateShortSentMessage sent = new TLRPC.TL_updateShortSentMessage();
        sent.flags = 1 << 1;
        sent.out = true;
        sent.id = id;
        // This bridge does not use Telegram's global PTS sequence. The app's
        // local update counter is advanced only by ordered SSE message events.
        sent.pts = -1;
        sent.pts_count = 0;
        sent.date = date;
        if (request.entities != null && !request.entities.isEmpty()) {
            sent.flags |= 1 << 7;
            sent.entities.addAll(request.entities);
        }
        return sent;
    }

    private TLRPC.TL_contacts_found searchUsers(TLRPC.TL_contacts_search request) throws ApiException {
        TLRPC.TL_contacts_found result = new TLRPC.TL_contacts_found();
        if (request == null || TextUtils.isEmpty(request.q)) return result;
        try {
            String query = URLEncoder.encode(request.q, "UTF-8");
            int limit = request.limit <= 0 ? 20 : Math.min(request.limit, 50);
            JSONObject response = performJsonRequest("GET", "/api/v1/users/search?q=" + query + "&limit=" + limit, null, null);
            JSONArray users = response.optJSONArray("users");
            if (users == null) return result;
            for (int i = 0; i < users.length(); i++) {
                JSONObject userJson = users.optJSONObject(i);
                if (userJson == null) continue;
                TLRPC.TL_user user = toUser(userJson, false);
                result.users.add(user);
                result.results.add(toPeer(user.id));
            }
            return result;
        } catch (java.io.UnsupportedEncodingException e) {
            throw new ApiException(400, "SEARCH_QUERY_INVALID");
        }
    }

    private TLRPC.TL_users_userFull getFullUser(TLRPC.TL_users_getFullUser request) throws ApiException {
        long userId = inputUserId(request == null ? null : request.id);
        if (userId <= 0) throw new ApiException(400, "USER_ID_INVALID");
        JSONObject profile = getProfile(userId);
        TLRPC.TL_users_userFull result = new TLRPC.TL_users_userFull();
        TLRPC.TL_userFull full = new TLRPC.TL_userFull();
        full.id = userId;
        full.about = optString(profile, "bio", "");
        full.flags = TextUtils.isEmpty(full.about) ? 0 : 1 << 1;
        full.settings = new TLRPC.TL_peerSettings();
        full.notify_settings = new TLRPC.TL_peerNotifySettings();
        result.full_user = full;
        result.users.add(toUser(profile, userId == currentUserId()));
        return result;
    }

    private Vector<TLRPC.User> getUsers(TLRPC.TL_users_getUsers request) throws ApiException {
        Vector<TLRPC.User> users = new Vector<TLRPC.User>(TLRPC.User::TLdeserialize);
        if (request == null) return users;
        for (TLRPC.InputUser inputUser : request.id) {
            long userId = inputUserId(inputUser);
            if (userId <= 0) continue;
            JSONObject profile = getProfile(userId);
            users.objects.add(toUser(profile, userId == currentUserId()));
        }
        return users;
    }

    private TLRPC.User updateProfile(TL_account.updateProfile request) throws ApiException {
        JSONObject body = new JSONObject();
        try {
            if ((request.flags & 1) != 0) body.put("firstName", request.first_name == null ? "" : request.first_name);
            if ((request.flags & 2) != 0) body.put("lastName", request.last_name == null ? "" : request.last_name);
            if ((request.flags & 4) != 0) body.put("bio", request.about == null ? "" : request.about);
        } catch (JSONException e) {
            throw new ApiException(400, "PROFILE_INVALID");
        }
        JSONObject response = performJsonRequest("PATCH", "/api/v1/me", body, null);
        JSONObject profile = response.optJSONObject("user");
        if (profile == null) throw new ApiException(502, "INVALID_PROFILE_RESPONSE");
        return toUser(profile, true);
    }

    private TLRPC.User updateUsername(TL_account.updateUsername request) throws ApiException {
        JSONObject body = new JSONObject();
        try {
            body.put("username", request.username == null ? "" : request.username);
        } catch (JSONException e) {
            throw new ApiException(400, "USERNAME_INVALID");
        }
        JSONObject response = performJsonRequest("PATCH", "/api/v1/me", body, null);
        JSONObject profile = response.optJSONObject("user");
        if (profile == null) throw new ApiException(502, "INVALID_PROFILE_RESPONSE");
        return toUser(profile, true);
    }

    private TLRPC.TL_updates_state getUpdatesState() {
        return updatesStateValue();
    }

    private TLRPC.TL_updates_state updatesStateValue() {
        TLRPC.TL_updates_state state = new TLRPC.TL_updates_state();
        try {
            MessagesStorage storage = MessagesStorage.getInstance(currentAccount);
            state.pts = storage.getLastPtsValue();
            state.qts = storage.getLastQtsValue();
            state.date = storage.getLastDateValue();
            state.seq = storage.getLastSeqValue();
        } catch (Throwable ignored) {
            state.date = (int) (System.currentTimeMillis() / 1000L);
        }
        return state;
    }

    private long createOrGetConversation(long peerId) throws ApiException {
        Long cached = conversationIdsByPeer.get(peerId);
        if (cached != null && cached > 0) return cached;
        JSONObject body = new JSONObject();
        try {
            body.put("peerUserId", peerId);
        } catch (JSONException e) {
            throw new ApiException(400, "USER_ID_INVALID");
        }
        JSONObject response = performJsonRequest("POST", "/api/v1/conversations/direct", body, null);
        JSONObject conversation = response.optJSONObject("conversation");
        long id = conversation == null ? 0 : conversation.optLong("id", 0);
        if (id <= 0) throw new ApiException(502, "INVALID_CONVERSATION_RESPONSE");
        conversationIdsByPeer.put(peerId, id);
        return id;
    }

    private JSONObject getProfile(long userId) throws ApiException {
        if (userId == currentUserId()) {
            JSONObject response = performJsonRequest("GET", "/api/v1/me", null, null);
            JSONObject user = response.optJSONObject("user");
            if (user == null) throw new ApiException(502, "INVALID_PROFILE_RESPONSE");
            return user;
        }
        JSONObject response = performJsonRequest("GET", "/api/v1/users/" + userId, null, null);
        JSONObject user = response.optJSONObject("user");
        if (user == null) throw new ApiException(502, "INVALID_PROFILE_RESPONSE");
        return user;
    }

    private TLRPC.TL_user peerFromApi(long userId) throws ApiException {
        return toUser(getProfile(userId), userId == currentUserId());
    }

    private TLRPC.TL_user toUser(JSONObject profile, boolean self) {
        TLRPC.TL_user user = new TLRPC.TL_user();
        user.id = profile.optLong("id", 0);
        user.access_hash = user.id == 0 ? 1 : user.id;
        user.first_name = optString(profile, "firstName", "");
        user.last_name = optString(profile, "lastName", "");
        user.username = optNullableString(profile, "username");
        user.phone = optNullableString(profile, "phone");
        user.self = self;
        user.contact = !self;
        user.mutual_contact = !self;
        user.flags = 1 | (1 << 1) | (1 << 2);
        if (!TextUtils.isEmpty(user.username)) user.flags |= 1 << 3;
        if (!TextUtils.isEmpty(user.phone)) user.flags |= 1 << 4;
        if (self) user.flags |= 1 << 10;
        else user.flags |= (1 << 11) | (1 << 12);
        return user;
    }

    private TLRPC.User currentUser() {
        try {
            TLRPC.User user = UserConfig.getInstance(currentAccount).getCurrentUser();
            if (user != null) return user;
        } catch (Throwable ignored) {
        }
        return null;
    }

    private long currentUserId() {
        long id = authenticatedUserId;
        if (id > 0) return id;
        try {
            id = UserConfig.getInstance(currentAccount).getClientUserId();
            if (id > 0) authenticatedUserId = id;
        } catch (Throwable ignored) {
        }
        return id;
    }

    private TLRPC.TL_message toMessage(JSONObject json, long peerId) throws ApiException {
        JSONObject sender = json.optJSONObject("sender");
        if (sender == null) throw new ApiException(502, "INVALID_MESSAGE_RESPONSE");
        long senderId = sender.optLong("id", 0);
        if (senderId <= 0) throw new ApiException(502, "INVALID_MESSAGE_RESPONSE");
        TLRPC.TL_message message = new TLRPC.TL_message();
        message.id = safeInt(json.optLong("id", 0), "MESSAGE_ID_INVALID");
        message.local_id = message.id;
        message.date = safeInt(json.optLong("createdAt", 0), "MESSAGE_DATE_INVALID");
        message.from_id = toPeer(senderId);
        message.peer_id = toPeer(peerId);
        message.dialog_id = peerId;
        message.message = optString(json, "text", "");
        message.out = senderId == currentUserId();
        message.unread = !message.out;
        message.media = new TLRPC.TL_messageMediaEmpty();
        message.flags = (1 << 8) | (1 << 9);
        if (message.out) message.flags |= 1 << 1;
        return message;
    }

    private TLRPC.TL_peerUser toPeer(long userId) {
        TLRPC.TL_peerUser peer = new TLRPC.TL_peerUser();
        peer.user_id = userId;
        return peer;
    }

    private long inputPeerUserId(TLRPC.InputPeer peer) {
        if (peer instanceof TLRPC.TL_inputPeerUser) return ((TLRPC.TL_inputPeerUser) peer).user_id;
        if (peer instanceof TLRPC.TL_inputPeerUserFromMessage) return ((TLRPC.TL_inputPeerUserFromMessage) peer).user_id;
        if (peer instanceof TLRPC.TL_inputPeerSelf) return currentUserId();
        return 0;
    }

    private long inputUserId(TLRPC.InputUser user) {
        if (user == null) return 0;
        if (user instanceof TLRPC.TL_inputUserSelf) return currentUserId();
        return user.user_id;
    }

    private void addUser(ArrayList<TLRPC.User> users, Set<Long> included, TLRPC.User user) {
        if (user != null && user.id > 0 && included.add(user.id)) users.add(user);
    }

    private int safeInt(long value, String error) throws ApiException {
        if (value <= 0 || value > Integer.MAX_VALUE) throw new ApiException(502, error);
        return (int) value;
    }

    private AtomicInteger lastSeenCounter(long conversationId) {
        AtomicInteger counter = lastSeenMessageIds.get(conversationId);
        if (counter == null) {
            AtomicInteger created = new AtomicInteger();
            AtomicInteger existing = lastSeenMessageIds.putIfAbsent(conversationId, created);
            counter = existing == null ? created : existing;
        }
        return counter;
    }

    private void observeMessageId(long conversationId, int messageId) {
        if (conversationId <= 0 || messageId <= 0) return;
        AtomicInteger counter = lastSeenCounter(conversationId);
        synchronized (counter) {
            if (messageId > counter.get()) counter.set(messageId);
        }
    }

    private int lastSeen(long conversationId) {
        AtomicInteger id = lastSeenMessageIds.get(conversationId);
        return id == null ? 0 : id.get();
    }

    private void initializeAccountEventCursor(long messageId) {
        if (messageId > 0 && lastAccountEventId.compareAndSet(0, messageId)) {
            sessionPreferences.edit().putLong(eventCursorKey(), messageId).apply();
        }
    }

    private void advanceAccountEventCursor(long messageId) {
        if (messageId <= 0) return;
        while (true) {
            long current = lastAccountEventId.get();
            if (messageId <= current) return;
            if (lastAccountEventId.compareAndSet(current, messageId)) {
                sessionPreferences.edit().putLong(eventCursorKey(), messageId).apply();
                return;
            }
        }
    }

    private synchronized void subscribeAccountEvents() {
        if (currentUserId() <= 0 || TextUtils.isEmpty(getSessionToken())) return;
        SseSubscription existing = accountSubscription;
        if (existing != null && !existing.stopped.get()) return;
        SseSubscription subscription = new SseSubscription(0, 0, true);
        accountSubscription = subscription;
        subscription.future = SSE_EXECUTOR.submit(() -> runEventStream(subscription));
    }

    private synchronized void subscribe(long conversationId, long peerId, int initialLastSeen) {
        if (conversationId <= 0 || peerId <= 0 || TextUtils.isEmpty(getSessionToken())) return;
        observeMessageId(conversationId, initialLastSeen);
        SseSubscription existing = subscriptions.get(conversationId);
        if (existing != null && !existing.stopped.get()) return;
        if (subscriptions.size() >= MAX_ACTIVE_STREAMS) return;
        SseSubscription subscription = new SseSubscription(conversationId, peerId);
        subscriptions.put(conversationId, subscription);
        subscription.future = SSE_EXECUTOR.submit(() -> runEventStream(subscription));
    }

    private void runEventStream(SseSubscription subscription) {
        long retryDelay = 1_000L;
        while (!subscription.stopped.get() && !Thread.currentThread().isInterrupted()) {
            HttpURLConnection connection = null;
            try {
                String streamPath = subscription.accountStream
                        ? "/api/v1/events"
                        : "/api/v1/conversations/" + subscription.conversationId + "/events";
                URL url = buildUrl(streamPath);
                enforceTransportSecurity(url);
                connection = (HttpURLConnection) url.openConnection();
                connection.setRequestMethod("GET");
                connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
                connection.setReadTimeout(SSE_READ_TIMEOUT_MS);
                connection.setInstanceFollowRedirects(false);
                connection.setRequestProperty("Accept", "text/event-stream");
                connection.setRequestProperty("Cache-Control", "no-cache");
                connection.setRequestProperty("Authorization", "Bearer " + getSessionToken());
                if (subscription.accountStream) {
                    connection.setRequestProperty("Last-Event-ID", String.valueOf(lastAccountEventId.get()));
                }
                subscription.connection = connection;
                int status = connection.getResponseCode();
                if (status != HttpURLConnection.HTTP_OK) {
                    throw new ApiException(status, status == 401 ? "SESSION_REVOKED" : "SSE_CONNECTION_FAILED");
                }
                // Reconcile conversation streams after every connection. Account-level
                // streams replay missed messages from their Last-Event-ID cursor.
                if (!subscription.accountStream) synchronizeConversation(subscription);
                retryDelay = 1_000L;
                try (BufferedReader reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
                    String event = "";
                    StringBuilder data = new StringBuilder();
                    String line;
                    while (!subscription.stopped.get() && (line = reader.readLine()) != null) {
                        if (line.startsWith("event:")) {
                            event = line.substring(6).trim();
                        } else if (line.startsWith("data:")) {
                            if (data.length() > 0) data.append('\n');
                            data.append(line.substring(5).trim());
                        } else if (line.isEmpty()) {
                            if ("message".equals(event) && data.length() > 0) {
                                try {
                                    JSONObject message = new JSONObject(data.toString());
                                    if (subscription.accountStream) {
                                        acceptAccountMessage(message);
                                    } else {
                                        acceptIncomingMessage(message, subscription);
                                    }
                                } catch (JSONException e) {
                                    FileLog.e(e);
                                }
                            }
                            event = "";
                            data.setLength(0);
                        }
                    }
                    if (!subscription.stopped.get()) throw new java.io.IOException("SSE stream ended");
                }
            } catch (Throwable error) {
                if (!subscription.stopped.get() && !(error instanceof InterruptedException)) {
                    if (BuildVars.LOGS_ENABLED && !(error instanceof ApiException && ((ApiException) error).code == 401)) {
                        FileLog.e(error);
                    }
                }
            } finally {
                subscription.connection = null;
                if (connection != null) connection.disconnect();
            }
            if (!subscription.stopped.get()) {
                try {
                    Thread.sleep(retryDelay);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
                retryDelay = Math.min(retryDelay * 2, 30_000L);
            }
        }
        if (subscriptions.get(subscription.conversationId) == subscription) {
            subscriptions.remove(subscription.conversationId);
        }
        if (subscription.accountStream && accountSubscription == subscription) {
            accountSubscription = null;
        }
    }

    private void synchronizeConversation(SseSubscription subscription) {
        try {
            JSONObject response = performJsonRequest("GET", "/api/v1/conversations/" + subscription.conversationId + "/messages?limit=100", null, null);
            JSONArray messages = response.optJSONArray("messages");
            if (messages == null) return;
            for (int i = 0; i < messages.length(); i++) {
                JSONObject message = messages.optJSONObject(i);
                if (message != null) acceptIncomingMessage(message, subscription);
            }
        } catch (ApiException error) {
            if (BuildVars.LOGS_ENABLED) FileLog.e("Messenger SSE resync failed: " + error.message);
        }
    }

    private void acceptAccountMessage(JSONObject messageJson) {
        long messageId = messageJson.optLong("id", 0);
        advanceAccountEventCursor(messageId);
        long conversationId = messageJson.optLong("conversationId", 0);
        JSONObject sender = messageJson.optJSONObject("sender");
        long senderId = sender == null ? 0 : sender.optLong("id", 0);
        if (conversationId <= 0 || senderId <= 0 || messageId <= 0 || senderId == currentUserId()) return;
        conversationIdsByPeer.put(senderId, conversationId);
        acceptIncomingMessage(messageJson, new SseSubscription(conversationId, senderId));
    }

    private void acceptIncomingMessage(JSONObject messageJson, SseSubscription subscription) {
        JSONObject sender = messageJson.optJSONObject("sender");
        if (sender == null) return;
        long senderId = sender.optLong("id", 0);
        long messageIdLong = messageJson.optLong("id", 0);
        if (senderId <= 0 || messageIdLong <= 0 || messageIdLong > Integer.MAX_VALUE) return;
        int messageId = (int) messageIdLong;
        AtomicInteger seen = lastSeenCounter(subscription.conversationId);
        while (true) {
            int current = seen.get();
            if (messageId <= current) return;
            if (seen.compareAndSet(current, messageId)) break;
        }
        // The send callback updates the local optimistic row; ignore the sender's SSE echo.
        if (senderId == currentUserId()) return;

        try {
            TLRPC.TL_message message = toMessage(messageJson, subscription.peerId);
            TL_update.TL_updateNewMessage update = new TL_update.TL_updateNewMessage();
            update.message = message;
            update.pts_count = 1;
            TLRPC.TL_updates updates = new TLRPC.TL_updates();
            updates.updates.add(update);
            updates.users.add(toUser(sender, false));
            TLRPC.User self = currentUser();
            if (self != null && self.id != senderId) updates.users.add(self);
            updates.date = message.date;
            updates.seq = 0;
            Utilities.stageQueue.postRunnable(() -> {
                if (subscription.stopped.get()) return;
                update.pts = MessagesStorage.getInstance(currentAccount).getLastPtsValue() + 1;
                MessagesController.getInstance(currentAccount).processUpdates(updates, false);
            });
        } catch (ApiException error) {
            if (BuildVars.LOGS_ENABLED) FileLog.e("Messenger SSE message was invalid: " + error.message);
        }
    }

    private void stopSubscriptions() {
        SseSubscription account = accountSubscription;
        accountSubscription = null;
        if (account != null) {
            account.stopped.set(true);
            HttpURLConnection connection = account.connection;
            if (connection != null) connection.disconnect();
            Future<?> future = account.future;
            if (future != null) future.cancel(true);
        }
        for (SseSubscription subscription : subscriptions.values()) {
            subscription.stopped.set(true);
            HttpURLConnection connection = subscription.connection;
            if (connection != null) connection.disconnect();
            Future<?> future = subscription.future;
            if (future != null) future.cancel(true);
        }
        subscriptions.clear();
    }

    private JSONObject performJsonRequest(String method, String path, JSONObject body, String tokenOverride) throws ApiException {
        HttpURLConnection connection = null;
        try {
            URL url = buildUrl(path);
            enforceTransportSecurity(url);
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestMethod(method);
            connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
            connection.setReadTimeout(READ_TIMEOUT_MS);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Cache-Control", "no-store");
            String token = tokenOverride == null ? getSessionToken() : tokenOverride;
            if (!TextUtils.isEmpty(token)) connection.setRequestProperty("Authorization", "Bearer " + token);
            if (body != null) {
                byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                connection.setFixedLengthStreamingMode(bytes.length);
                try (OutputStream output = connection.getOutputStream()) {
                    output.write(bytes);
                }
            }
            int status = connection.getResponseCode();
            InputStream input = status >= 200 && status < 300 ? connection.getInputStream() : connection.getErrorStream();
            byte[] bytes = input == null ? new byte[0] : readBounded(input, MAX_RESPONSE_BYTES);
            JSONObject response = bytes.length == 0 ? new JSONObject() : new JSONObject(new String(bytes, StandardCharsets.UTF_8));
            if (status < 200 || status >= 300) {
                JSONObject error = response.optJSONObject("error");
                String code = error == null ? "HTTP_" + status : error.optString("code", "HTTP_" + status);
                String message = mapErrorCode(code, status);
                throw new ApiException(status, message);
            }
            return response;
        } catch (ApiException e) {
            throw e;
        } catch (SocketTimeoutException e) {
            throw new ApiException(503, "NETWORK_TIMEOUT");
        } catch (JSONException e) {
            throw new ApiException(502, "INVALID_API_RESPONSE");
        } catch (java.io.IOException e) {
            throw new ApiException(503, "CONNECTION_FAILED");
        } catch (Exception e) {
            throw new ApiException(502, "API_REQUEST_FAILED");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private URL buildUrl(String path) throws ApiException {
        String base = BuildVars.MESSENGER_API_BASE_URL == null ? "" : BuildVars.MESSENGER_API_BASE_URL.trim();
        while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
        if (base.isEmpty()) throw new ApiException(503, "API_BASE_URL_NOT_CONFIGURED");
        try {
            return new URL(base + (path.startsWith("/") ? path : "/" + path));
        } catch (Exception e) {
            throw new ApiException(503, "API_BASE_URL_INVALID");
        }
    }

    private void enforceTransportSecurity(URL url) throws ApiException {
        if ("https".equalsIgnoreCase(url.getProtocol())) return;
        String host = url.getHost();
        boolean emulatorLoopback = BuildVars.DEBUG_VERSION && ("10.0.2.2".equalsIgnoreCase(host)
                || "127.0.0.1".equals(host) || "localhost".equalsIgnoreCase(host) || "::1".equals(host));
        if (!emulatorLoopback) throw new ApiException(400, "TLS_REQUIRED");
    }

    private byte[] readBounded(InputStream input, int maxBytes) throws java.io.IOException {
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int total = 0;
            int read;
            while ((read = stream.read(buffer)) != -1) {
                total += read;
                if (total > maxBytes) throw new java.io.IOException("API response too large");
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        }
    }

    private String mapErrorCode(String code, int status) {
        if ("INVALID_ID".equals(code)) return "USER_ID_INVALID";
        if ("INVALID_REQUEST".equals(code)) return "REQUEST_FAILED";
        if ("UNAUTHORIZED".equals(code)) return "SESSION_REVOKED";
        if ("USER_NOT_FOUND".equals(code)) return "USER_ID_INVALID";
        if ("USERNAME_TAKEN".equals(code)) return "USERNAME_INVALID";
        if ("CONVERSATION_NOT_FOUND".equals(code)) return "CHAT_ID_INVALID";
        if (status == 429) return "FLOOD_WAIT";
        if (status >= 500) return "SERVER_ERROR";
        return code == null || code.isEmpty() ? "REQUEST_FAILED" : code;
    }

    private String sessionKey() {
        return SESSION_PREFIX + currentAccount;
    }

    private String eventCursorKey() {
        return EVENT_CURSOR_PREFIX + currentAccount;
    }

    private synchronized String getSessionToken() {
        String encrypted = sessionPreferences.getString(sessionKey(), null);
        if (TextUtils.isEmpty(encrypted)) return null;
        try {
            byte[] payload = Base64.decode(encrypted, Base64.NO_WRAP);
            if (payload.length <= 12) throw new IllegalArgumentException("Encrypted session is truncated");
            byte[] iv = new byte[12];
            byte[] ciphertext = new byte[payload.length - iv.length];
            System.arraycopy(payload, 0, iv, 0, iv.length);
            System.arraycopy(payload, iv.length, ciphertext, 0, ciphertext.length);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, getOrCreateSessionKey(), new GCMParameterSpec(128, iv));
            return new String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8);
        } catch (Throwable e) {
            sessionPreferences.edit().remove(sessionKey()).apply();
            if (BuildVars.LOGS_ENABLED) FileLog.e("Unable to decrypt stored messenger session");
            return null;
        }
    }

    private synchronized void storeSessionToken(String token) throws ApiException {
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, getOrCreateSessionKey());
            byte[] iv = cipher.getIV();
            byte[] ciphertext = cipher.doFinal(token.getBytes(StandardCharsets.UTF_8));
            ByteBuffer payload = ByteBuffer.allocate(iv.length + ciphertext.length);
            payload.put(iv);
            payload.put(ciphertext);
            String encoded = Base64.encodeToString(payload.array(), Base64.NO_WRAP);
            sessionPreferences.edit().putString(sessionKey(), encoded).apply();
        } catch (Throwable e) {
            throw new ApiException(500, "SECURE_SESSION_STORAGE_FAILED");
        }
    }

    private static synchronized Key getOrCreateSessionKey() throws Exception {
        KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
        keyStore.load(null);
        if (keyStore.containsAlias(KEY_ALIAS)) {
            return keyStore.getKey(KEY_ALIAS, null);
        }
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build());
        SecretKey key = generator.generateKey();
        return key;
    }

    private String optString(JSONObject object, String key, String fallback) {
        if (object == null) return fallback;
        Object value = object.opt(key);
        return value == null || value == JSONObject.NULL ? fallback : String.valueOf(value);
    }

    private String optNullableString(JSONObject object, String key) {
        if (object == null) return null;
        Object value = object.opt(key);
        return value == null || value == JSONObject.NULL ? null : String.valueOf(value);
    }

    private static ThreadFactory threadFactory(String prefix) {
        AtomicInteger index = new AtomicInteger();
        return runnable -> {
            Thread thread = new Thread(runnable, prefix + "-" + index.incrementAndGet());
            thread.setDaemon(true);
            return thread;
        };
    }

    private static final class SseSubscription {
        final long conversationId;
        final long peerId;
        final boolean accountStream;
        final AtomicBoolean stopped = new AtomicBoolean();
        volatile HttpURLConnection connection;
        volatile Future<?> future;

        SseSubscription(long conversationId, long peerId) {
            this(conversationId, peerId, false);
        }

        SseSubscription(long conversationId, long peerId, boolean accountStream) {
            this.conversationId = conversationId;
            this.peerId = peerId;
            this.accountStream = accountStream;
        }
    }

    private static final class ApiException extends Exception {
        final int code;
        final String message;

        ApiException(int code, String message) {
            super(message);
            this.code = code;
            this.message = message;
        }
    }
}
