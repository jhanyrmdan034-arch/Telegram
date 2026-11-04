package org.telegram.platform.realtime;

import java.util.Map;

/** WebSocket transport abstraction (production: nv-websocket-client, tests: fake). */
public interface SocketTransport {

    interface Listener {
        void onOpen();

        void onText(String message);

        void onClosed(int code, String reason);

        void onFailure(Throwable cause);
    }

    void connect(String url, Map<String, String> headers, Listener listener);

    boolean isOpen();

    void send(String text);

    void close();
}
