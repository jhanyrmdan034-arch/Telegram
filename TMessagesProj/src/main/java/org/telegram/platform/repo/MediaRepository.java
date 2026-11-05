package org.telegram.platform.repo;

import com.google.gson.JsonObject;

import org.telegram.platform.PlatformBridge;
import org.telegram.platform.PlatformException;
import org.telegram.platform.net.PlatformApi;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Uploads and downloads against our own file store.
 *
 * <p>Everything is streaming: progress is reported per chunk, a cancel flag aborts
 * the connection immediately, and a failed attempt can simply be repeated because
 * the server de-duplicates identical bytes by sha256.
 */
public final class MediaRepository {

    /** 8 MiB, the server's part size for multipart uploads. */
    private static final int PART_SIZE = 8 * 1024 * 1024;
    private static final int BUFFER = 32 * 1024;

    public interface Progress {
        void onProgress(long uploaded, long total);
    }

    public interface Cancel {
        boolean isCancelled();
    }

    private final PlatformApi api;
    private final ExecutorService executor;

    public MediaRepository(PlatformApi api, ExecutorService executor) {
        this.api = api;
        this.executor = executor;
    }

    // ------------------------------------------------------------------ upload

    /**
     * Uploads a file and returns the created file record (id, size, mime, sha256).
     * Files larger than one part are uploaded in bounded pieces.
     */
    public JsonObject uploadBlocking(File file, String kind, String mimeType,
                                     Progress progress, Cancel cancel) throws PlatformException {
        if (file == null || !file.exists() || file.length() == 0) {
            throw new PlatformException("File is missing or empty", 400, null);
        }
        if (file.length() > PART_SIZE) {
            return multipartUpload(file, kind, mimeType, progress, cancel);
        }
        return singleUpload(file, kind, mimeType, progress, cancel);
    }

    public void upload(final File file, final String kind, final String mimeType,
                       final Progress progress, final Cancel cancel,
                       final Callback<JsonObject> callback) {
        executor.execute(() -> {
            try {
                callback.onSuccess(uploadBlocking(file, kind, mimeType, progress, cancel));
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    private JsonObject singleUpload(File file, String kind, String mime, Progress progress,
                                    Cancel cancel) throws PlatformException {
        String boundary = "----platform" + System.nanoTime();
        HttpURLConnection connection = null;
        try {
            URL url = new URL(base() + PlatformApi.API_PREFIX + "/files");
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setRequestProperty("Authorization", "Bearer " + token());
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);

            long total = file.length() + 512;
            try (OutputStream out = connection.getOutputStream()) {
                writeField(out, boundary, "kind", kind);
                if (mime != null) {
                    writeField(out, boundary, "mime_type", mime);
                }
                out.write(("--" + boundary + "\r\n"
                        + "Content-Disposition: form-data; name=\"file\"; filename=\""
                        + file.getName().replace("\"", "") + "\"\r\n"
                        + "Content-Type: " + (mime == null ? "application/octet-stream" : mime)
                        + "\r\n\r\n").getBytes("UTF-8"));
                long sent = 0;
                try (InputStream in = new java.io.FileInputStream(file)) {
                    byte[] buffer = new byte[BUFFER];
                    int read;
                    while ((read = in.read(buffer)) > 0) {
                        if (cancel != null && cancel.isCancelled()) {
                            connection.disconnect();
                            throw new PlatformException("Upload cancelled", 0, null);
                        }
                        out.write(buffer, 0, read);
                        sent += read;
                        if (progress != null) {
                            progress.onProgress(sent, file.length());
                        }
                    }
                }
                out.write(("\r\n--" + boundary + "--\r\n").getBytes("UTF-8"));
                if (progress != null) {
                    progress.onProgress(total, total);
                }
            }
            return readJson(connection);
        } catch (IOException e) {
            throw new PlatformException("upload failed: " + e.getMessage(), 0, e);
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private JsonObject multipartUpload(File file, String kind, String mime, Progress progress,
                                       Cancel cancel) throws PlatformException {
        JsonObject init = new JsonObject();
        init.addProperty("name", file.getName());
        init.addProperty("kind", kind);
        init.addProperty("size", file.length());
        if (mime != null) {
            init.addProperty("mime_type", mime);
        }
        JsonObject session = api.post(PlatformApi.API_PREFIX + "/files/multipart/init", init);
        String uploadId = session.get("upload_id").getAsString();
        int partSize = session.get("part_size").getAsInt();

        try (InputStream in = new java.io.FileInputStream(file)) {
            byte[] buffer = new byte[partSize];
            int index = 0;
            long sentTotal = 0;
            while (true) {
                int read = readFully(in, buffer);
                if (read <= 0) {
                    break;
                }
                if (cancel != null && cancel.isCancelled()) {
                    throw new PlatformException("Upload cancelled", 0, null);
                }
                putPart(uploadId, index, buffer, read);
                sentTotal += read;
                if (progress != null) {
                    progress.onProgress(sentTotal, file.length());
                }
                index++;
                if (read < partSize) {
                    break;
                }
            }
        } catch (IOException e) {
            throw new PlatformException("upload failed: " + e.getMessage(), 0, e);
        }
        return api.post(PlatformApi.API_PREFIX + "/files/multipart/" + uploadId + "/complete",
                new JsonObject());
    }

    private void putPart(String uploadId, int index, byte[] data, int length) throws PlatformException {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(base() + PlatformApi.API_PREFIX + "/files/multipart/" + uploadId
                    + "/parts/" + index);
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestMethod("PUT");
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(length);
            connection.setRequestProperty("Authorization", "Bearer " + token());
            connection.setRequestProperty("Content-Type", "application/octet-stream");
            try (OutputStream out = connection.getOutputStream()) {
                out.write(data, 0, length);
            }
            int status = connection.getResponseCode();
            if (status / 100 != 2) {
                throw new PlatformException("part upload failed with HTTP " + status, status, null);
            }
        } catch (IOException e) {
            throw new PlatformException("part upload failed: " + e.getMessage(), 0, e);
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    // ---------------------------------------------------------------- download

    /** Streams a file to {@code target}; the cached copy is reused when complete. */
    public File downloadBlocking(String fileId, File target, Progress progress, Cancel cancel)
            throws PlatformException {
        if (target.exists() && target.length() > 0) {
            return target;
        }
        HttpURLConnection connection = null;
        try {
            URL url = new URL(base() + PlatformApi.API_PREFIX + "/files/" + fileId + "/content");
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestProperty("Authorization", "Bearer " + token());
            int status = connection.getResponseCode();
            if (status == 403) {
                throw new PlatformException("Access denied", 403, null);
            }
            if (status != HttpURLConnection.HTTP_OK) {
                throw new PlatformException("download failed with HTTP " + status, status, null);
            }
            long total = connection.getContentLengthLong();
            File temp = new File(target.getAbsolutePath() + ".part");
            try (InputStream in = connection.getInputStream();
                 OutputStream out = new FileOutputStream(temp)) {
                byte[] buffer = new byte[BUFFER];
                long received = 0;
                int read;
                while ((read = in.read(buffer)) > 0) {
                    if (cancel != null && cancel.isCancelled()) {
                        connection.disconnect();
                        temp.delete();
                        throw new PlatformException("Download cancelled", 0, null);
                    }
                    out.write(buffer, 0, read);
                    received += read;
                    if (progress != null) {
                        progress.onProgress(received, total);
                    }
                }
            }
            if (!temp.renameTo(target)) {
                throw new PlatformException("cannot move downloaded file", 0, null);
            }
            return target;
        } catch (IOException e) {
            throw new PlatformException("download failed: " + e.getMessage(), 0, e);
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    public void download(final String fileId, final File target, final Progress progress,
                         final Cancel cancel, final Callback<File> callback) {
        executor.execute(() -> {
            try {
                callback.onSuccess(downloadBlocking(fileId, target, progress, cancel));
            } catch (PlatformException e) {
                callback.onError(e);
            }
        });
    }

    // ----------------------------------------------------------------- helpers

    private String base() {
        return PlatformBridge.get().config().httpBaseUrl;
    }

    private String token() {
        return PlatformBridge.get().tokens().accessToken();
    }

    private static void writeField(OutputStream out, String boundary, String name, String value)
            throws IOException {
        out.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name
                + "\"\r\n\r\n" + URLEncoder.encode(value, "UTF-8") + "\r\n").getBytes("UTF-8"));
    }

    private static int readFully(InputStream in, byte[] buffer) throws IOException {
        int total = 0;
        while (total < buffer.length) {
            int read = in.read(buffer, total, buffer.length - total);
            if (read < 0) {
                break;
            }
            total += read;
        }
        return total;
    }

    private static JsonObject readJson(HttpURLConnection connection) throws IOException, PlatformException {
        int status = connection.getResponseCode();
        InputStream stream = status / 100 == 2 ? connection.getInputStream()
                : connection.getErrorStream();
        StringBuilder builder = new StringBuilder();
        if (stream != null) {
            try (InputStream in = stream) {
                byte[] buffer = new byte[4096];
                int read;
                while ((read = in.read(buffer)) > 0) {
                    builder.append(new String(buffer, 0, read, "UTF-8"));
                }
            }
        }
        if (status / 100 != 2) {
            throw new PlatformException("upload failed with HTTP " + status + ": " + builder,
                    status, null);
        }
        return com.google.gson.JsonParser.parseString(builder.toString()).getAsJsonObject();
    }

    /** Convenience for callers that only need a cancel flag. */
    public static Cancel cancelFlag(final AtomicBoolean flag) {
        return flag::get;
    }
}
