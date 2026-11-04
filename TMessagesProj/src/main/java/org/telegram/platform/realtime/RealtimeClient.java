package org.telegram.platform.realtime;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import org.telegram.platform.PlatformConfig;
import org.telegram.platform.auth.TokenStore;

import java.util.Collections;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Keeps a WebSocket open to the backend and turns server events into callbacks.
 *
 * <p>Behaviour that matters for the UI:
 * <ul>
 *   <li>reconnect with exponential backoff (1s, 2s, 4s ... capped at 60s) and jitter</li>
 *   <li>a fresh access token is read on every attempt, so token rotation is transparent</li>
 *   <li>an application-level ping keeps NAT mappings alive on mobile networks</li>
 *   <li>connection state is exposed so the dialog list can show "connecting…"</li>
 * </ul>
 */
public final class RealtimeClient {

    public interface EventListener {
        void onEvent(String type, JsonObject data);

        void onConnectionState(boolean connected);
    }

    private static final int PING_INTERVAL_SECONDS = 45;
    private static final long MAX_BACKOFF_MS = 60_000L;

    private final PlatformConfig config;
    private final TokenStore tokens;
    private final SocketTransport transport;
    private final ScheduledExecutorService scheduler =
            Executors.newSingleThreadScheduledExecutor(r -> {
                Thread thread = new Thread(r, "platform-realtime");
                thread.setDaemon(true);
                return thread;
            });

    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicInteger attempt = new AtomicInteger(0);
    private volatile EventListener listener;

    public RealtimeClient(PlatformConfig config, TokenStore tokens, SocketTransport transport) {
        this.config = config;
        this.tokens = tokens;
        this.transport = transport;
    }

    public void setListener(EventListener listener) {
        this.listener = listener;
    }

    public boolean isConnected() {
        return transport.isOpen();
    }

    public void connect() {
        if (running.compareAndSet(false, true)) {
            open();
            schedulePing();
        }
    }

    public void disconnect() {
        running.set(false);
        transport.close();
    }

    // ---------------------------------------------------------------- internals

    private void open() {
        String access = tokens.accessToken();
        if (access == null) {
            running.set(false);
            return;
        }
        String url = config.websocketUrl + "?token=" + access;
        transport.connect(url, Collections.emptyMap(), new SocketTransport.Listener() {
            @Override
            public void onOpen() {
                attempt.set(0);
                notifyConnection(true);
            }

            @Override
            public void onText(String message) {
                dispatch(message);
            }

            @Override
            public void onClosed(int code, String reason) {
                notifyConnection(false);
                if (code == 4401 || code == 4403) {
                    // Auth failure: do not hammer the server while signed out.
                    running.set(false);
                    return;
                }
                scheduleReconnect();
            }

            @Override
            public void onFailure(Throwable cause) {
                notifyConnection(false);
                scheduleReconnect();
            }
        });
    }

    private void dispatch(String raw) {
        try {
            JsonObject envelope = JsonParser.parseString(raw).getAsJsonObject();
            String type = envelope.has("type") ? envelope.get("type").getAsString() : "unknown";
            JsonObject data = envelope.has("data") && envelope.get("data").isJsonObject()
                    ? envelope.getAsJsonObject("data") : new JsonObject();
            EventListener target = listener;
            if (target != null) {
                target.onEvent(type, data);
            }
        } catch (RuntimeException ignored) {
            // Malformed frame: ignore rather than tear down the socket.
        }
    }

    private void scheduleReconnect() {
        if (!running.get()) {
            return;
        }
        int tries = attempt.incrementAndGet();
        long backoff = Math.min(MAX_BACKOFF_MS, (1L << Math.min(tries, 6)) * 500L);
        long jitter = (long) (Math.random() * 400);
        scheduler.schedule(() -> {
            if (running.get()) {
                open();
            }
        }, backoff + jitter, TimeUnit.MILLISECONDS);
    }

    private void schedulePing() {
        scheduler.scheduleWithFixedDelay(() -> {
            if (transport.isOpen()) {
                JsonObject ping = new JsonObject();
                ping.addProperty("type", "ping");
                transport.send(ping.toString());
            }
        }, PING_INTERVAL_SECONDS, PING_INTERVAL_SECONDS, TimeUnit.SECONDS);
    }

    private void notifyConnection(boolean connected) {
        EventListener target = listener;
        if (target != null) {
            target.onConnectionState(connected);
        }
    }

    // ------------------------------------------------------------ client verbs

    public void sendTyping(String chatId) {
        JsonObject data = new JsonObject();
        data.addProperty("chat_id", chatId);
        send("typing", data);
    }

    public void sendRead(String chatId, long seq) {
        JsonObject data = new JsonObject();
        data.addProperty("chat_id", chatId);
        data.addProperty("seq", seq);
        send("read", data);
    }

    private void send(String type, JsonObject data) {
        if (!transport.isOpen()) {
            return; // the REST equivalents (/typing, /read) remain available
        }
        JsonObject envelope = new JsonObject();
        envelope.addProperty("type", type);
        envelope.add("data", data);
        transport.send(envelope.toString());
    }

    public void shutdown() {
        disconnect();
        scheduler.shutdownNow();
    }
}
