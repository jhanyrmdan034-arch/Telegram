package org.telegram.platform.realtime;

import com.neovisionaries.ws.client.WebSocket;
import com.neovisionaries.ws.client.WebSocketAdapter;
import com.neovisionaries.ws.client.WebSocketException;
import com.neovisionaries.ws.client.WebSocketFactory;
import com.neovisionaries.ws.client.WebSocketFrame;

import java.io.IOException;
import java.util.List;
import java.util.Map;

/**
 * WebSocket transport built on nv-websocket-client, which the app already ships
 * ({@code com.neovisionaries:nv-websocket-client:2.14}) — no new dependency.
 */
public final class NvWebSocketTransport implements SocketTransport {

    private static final int CONNECT_TIMEOUT_MS = 15_000;

    private volatile WebSocket socket;

    @Override
    public void connect(String url, Map<String, String> headers, final Listener listener) {
        try {
            WebSocketFactory factory = new WebSocketFactory().setConnectionTimeout(CONNECT_TIMEOUT_MS);
            WebSocket ws = factory.createSocket(url);
            if (headers != null) {
                for (Map.Entry<String, String> header : headers.entrySet()) {
                    ws.addHeader(header.getKey(), header.getValue());
                }
            }
            ws.addListener(new WebSocketAdapter() {
                @Override
                public void onConnected(WebSocket websocket, Map<String, List<String>> responseHeaders) {
                    listener.onOpen();
                }

                @Override
                public void onTextMessage(WebSocket websocket, String text) {
                    listener.onText(text);
                }

                @Override
                public void onDisconnected(WebSocket websocket, WebSocketFrame serverCloseFrame,
                                           WebSocketFrame clientCloseFrame, boolean closedByServer) {
                    int code = serverCloseFrame != null ? serverCloseFrame.getCloseCode() : 1006;
                    String reason = serverCloseFrame != null ? serverCloseFrame.getCloseReason() : "";
                    listener.onClosed(code, reason);
                }

                @Override
                public void onError(WebSocket websocket, WebSocketException cause) {
                    listener.onFailure(cause);
                }
            });
            socket = ws.connectAsynchronously();
        } catch (IOException e) {
            listener.onFailure(e);
        }
    }

    @Override
    public boolean isOpen() {
        WebSocket ws = socket;
        return ws != null && ws.isOpen();
    }

    @Override
    public void send(String text) {
        WebSocket ws = socket;
        if (ws != null && ws.isOpen()) {
            ws.sendText(text);
        }
    }

    @Override
    public void close() {
        WebSocket ws = socket;
        socket = null;
        if (ws != null) {
            ws.disconnect();
        }
    }
}
