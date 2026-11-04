package org.telegram.platform.net;

import java.io.IOException;
import java.util.Collections;
import java.util.Map;

/**
 * Minimal HTTP abstraction so the API client can be exercised without Android
 * (and so a different transport, e.g. HTTP/2 with pinning, can be swapped in).
 */
public interface HttpTransport {

    Response execute(String method, String url, Map<String, String> headers, byte[] body,
                     int connectTimeoutMs, int readTimeoutMs) throws IOException;

    final class Response {
        public final int status;
        public final String body;
        public final String retryAfterHeader;

        public Response(int status, String body, String retryAfterHeader) {
            this.status = status;
            this.body = body;
            this.retryAfterHeader = retryAfterHeader;
        }

        public static Response empty(int status) {
            return new Response(status, "", null);
        }
    }

    static Map<String, String> noHeaders() {
        return Collections.emptyMap();
    }
}
