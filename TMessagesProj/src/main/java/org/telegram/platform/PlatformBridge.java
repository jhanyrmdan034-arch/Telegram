package org.telegram.platform;

import android.content.Context;

import org.telegram.platform.auth.AuthManager;
import org.telegram.platform.auth.KeystoreTokenStore;
import org.telegram.platform.auth.TokenStore;
import org.telegram.platform.mapper.DtoMapper;
import org.telegram.platform.mapper.IdMapper;
import org.telegram.platform.net.PlatformApi;
import org.telegram.platform.net.UrlConnectionTransport;
import org.telegram.platform.realtime.NvWebSocketTransport;
import org.telegram.platform.realtime.RealtimeClient;
import org.telegram.platform.repo.ChatRepository;
import org.telegram.platform.repo.MessageRepository;
import org.telegram.platform.store.LocalStore;
import org.telegram.platform.store.SqliteLocalStore;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Single entry point for the platform client. Everything the UI needs is reached
 * through this object, so the rest of the app has exactly one dependency:
 * {@code PlatformBridge.get()}.
 *
 * <p>Wiring in the existing app (see docs/android/phase1-android-client-fa.md):
 * <ol>
 *   <li>{@code ApplicationLoader.onCreate} → {@link #init(Context, PlatformConfig)}</li>
 *   <li>LoginActivity (welcome fragment) → {@code auth().requestOtp(...)} / {@code verifyOtp(...)}</li>
 *   <li>DialogsActivity → {@code chats().refreshChats(...)} + {@code realtime().connect()}</li>
 *   <li>ChatActivity → {@code messages().loadHistory(...)} / {@code send(...)}</li>
 * </ol>
 * Nothing in this class references ConnectionsManager, so MTProto stays untouched
 * and can coexist until every screen is switched over.
 */
public final class PlatformBridge {

    private static volatile PlatformBridge instance;

    private final PlatformConfig config;
    private final TokenStore tokenStore;
    private final PlatformApi api;
    private final AuthManager auth;
    private final RealtimeClient realtime;
    private final LocalStore localStore;
    private final IdMapper idMapper;
    private final DtoMapper mapper;
    private final ChatRepository chats;
    private final MessageRepository messages;
    private final org.telegram.platform.repo.UserRepository users;
    private final org.telegram.platform.repo.MediaRepository media;
    private final ExecutorService executor = Executors.newFixedThreadPool(3, r -> {
        Thread thread = new Thread(r, "platform-io");
        thread.setDaemon(true);
        return thread;
    });

    private PlatformBridge(Context context, PlatformConfig config) {
        this.config = config;
        this.tokenStore = new KeystoreTokenStore(context);
        this.api = new PlatformApi(config, new UrlConnectionTransport(), tokenStore);
        this.auth = new AuthManager(api, tokenStore);
        this.realtime = new RealtimeClient(config, tokenStore, new NvWebSocketTransport());
        this.localStore = new SqliteLocalStore(context);
        this.idMapper = new IdMapper(localStore);
        this.mapper = new DtoMapper(idMapper);
        this.chats = new ChatRepository(api, localStore, executor);
        this.messages = new MessageRepository(api, localStore, realtime, executor);
        this.users = new org.telegram.platform.repo.UserRepository(api, executor);
        this.media = new org.telegram.platform.repo.MediaRepository(api, executor);

        realtime.setListener(new RealtimeClient.EventListener() {
            @Override
            public void onEvent(String type, com.google.gson.JsonObject data) {
                messages.applyRealtimeEvent(type, data);
                // The dialog list refreshes itself on new messages, presence and
                // read receipts: no restart, no pull-to-refresh needed.
                org.telegram.platform.tl.PlatformDialogsSync.onRealtimeEvent(0, type, data);
                org.telegram.platform.tl.PlatformChatSync.onRealtimeEvent(type, data);
                if ("profile.updated".equals(type)) {
                    org.telegram.platform.tl.PlatformContactsSync.onProfileUpdated(data);
                }
            }

            @Override
            public void onConnectionState(boolean connected) {
                if (connected) {
                    // Anything typed while offline is delivered as soon as we are back.
                    messages.flushOutbox();
                    // Frames missed while the socket was down are re-read from the API.
                    org.telegram.platform.tl.PlatformDialogsSync.refresh(0);
                    org.telegram.platform.tl.PlatformChatSync.syncSinceLastSeen(
                            org.telegram.platform.tl.PlatformChatSync.getActiveDialog(), 0, 0);
                }
            }
        });
    }

    /** Must be called once from {@code ApplicationLoader}. Idempotent. */
    public static PlatformBridge init(Context context, PlatformConfig config) {
        PlatformBridge local = instance;
        if (local == null) {
            synchronized (PlatformBridge.class) {
                local = instance;
                if (local == null) {
                    local = new PlatformBridge(context.getApplicationContext(), config);
                    instance = local;
                }
            }
        }
        return local;
    }

    public static PlatformBridge get() {
        PlatformBridge local = instance;
        if (local == null) {
            throw new IllegalStateException("PlatformBridge.init() was not called");
        }
        return local;
    }

    public static boolean isReady() {
        return instance != null;
    }

    /** Called after a successful sign-in and on every cold start with a stored token. */
    public void startSession() {
        auth.restore();
        realtime.connect();
    }

    public void endSession() {
        realtime.disconnect();
        auth.logout();
        localStore.saveChats(java.util.Collections.<String>emptyList());
    }

    public PlatformConfig config() {
        return config;
    }

    public PlatformApi api() {
        return api;
    }

    public AuthManager auth() {
        return auth;
    }

    public RealtimeClient realtime() {
        return realtime;
    }

    public LocalStore store() {
        return localStore;
    }

    public TokenStore tokens() {
        return tokenStore;
    }

    public DtoMapper mapper() {
        return mapper;
    }

    public IdMapper ids() {
        return idMapper;
    }

    public ChatRepository chats() {
        return chats;
    }

    public org.telegram.platform.repo.MediaRepository media() {
        return media;
    }

    public org.telegram.platform.repo.UserRepository users() {
        return users;
    }

    public MessageRepository messages() {
        return messages;
    }

    public ExecutorService executor() {
        return executor;
    }
}
