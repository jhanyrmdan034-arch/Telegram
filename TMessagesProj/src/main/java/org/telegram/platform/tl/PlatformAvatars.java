package org.telegram.platform.tl;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;

import com.google.gson.JsonObject;

import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.ApplicationLoader;
import org.telegram.messenger.FileLog;
import org.telegram.messenger.ImageReceiver;
import org.telegram.messenger.NotificationCenter;
import org.telegram.platform.PlatformBridge;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Profile pictures served by our own backend.
 *
 * <p>Telegram renders {@code user.photo}/{@code chat.photo} through its CDN file
 * locations, which do not exist for our server. Instead the avatar bytes are
 * downloaded once, cached on disk, and handed to the same {@link ImageReceiver}
 * the row already uses — so layout, size, shape and the online badge stay exactly
 * as before.
 *
 * <p>Cache: one file per avatar file id plus a sidecar holding the ETag, so an
 * unchanged picture costs a 304 instead of a download.
 */
public final class PlatformAvatars {

    private static final Map<Long, String> FILE_ID_BY_DIALOG = new ConcurrentHashMap<>();
    private static final Map<String, Bitmap> BITMAPS = new ConcurrentHashMap<>();
    private static final Set<String> IN_FLIGHT = ConcurrentHashMap.newKeySet();

    private PlatformAvatars() {
    }

    /** Called by the dialog sync for every row, so a cell can resolve its picture. */
    public static void remember(long dialogId, String avatarFileId) {
        if (avatarFileId == null || avatarFileId.isEmpty()) {
            FILE_ID_BY_DIALOG.remove(dialogId);
        } else {
            FILE_ID_BY_DIALOG.put(dialogId, avatarFileId);
        }
    }

    public static void rememberFromRow(long dialogId, JsonObject row) {
        String fileId = null;
        if (row != null && row.has("avatar_file_id") && !row.get("avatar_file_id").isJsonNull()) {
            fileId = row.get("avatar_file_id").getAsString();
        }
        if (fileId == null && row != null && row.has("peer") && row.get("peer").isJsonObject()) {
            JsonObject peer = row.getAsJsonObject("peer");
            if (peer.has("avatar_file_id") && !peer.get("avatar_file_id").isJsonNull()) {
                fileId = peer.get("avatar_file_id").getAsString();
            }
        }
        remember(dialogId, fileId);
    }

    /** Applies the cached picture to a row avatar, or starts the download. UI thread. */
    public static void apply(ImageReceiver receiver, long dialogId) {
        if (receiver == null || !PlatformDialogsSync.isEnabled()) {
            return;
        }
        final String fileId = FILE_ID_BY_DIALOG.get(dialogId);
        if (fileId == null) {
            return;
        }
        Bitmap cached = BITMAPS.get(fileId);
        if (cached == null) {
            File file = fileFor(fileId);
            if (file.exists()) {
                cached = BitmapFactory.decodeFile(file.getAbsolutePath());
                if (cached != null) {
                    BITMAPS.put(fileId, cached);
                }
            }
        }
        if (cached != null) {
            receiver.setImageBitmap(cached);
        } else {
            ensure(fileId);
        }
    }

    /** Downloads an avatar once; refreshes the list when the bitmap is ready. */
    public static void ensure(final String fileId) {
        if (fileId == null || BITMAPS.containsKey(fileId) || !IN_FLIGHT.add(fileId)) {
            return;
        }
        final PlatformBridge bridge = PlatformBridge.get();
        bridge.executor().execute(() -> {
            try {
                File file = fileFor(fileId);
                File etagFile = new File(file.getAbsolutePath() + ".etag");
                if (download(bridge, fileId, file, etagFile)) {
                    Bitmap bitmap = BitmapFactory.decodeFile(file.getAbsolutePath());
                    if (bitmap != null) {
                        BITMAPS.put(fileId, bitmap);
                        AndroidUtilities.runOnUIThread(() -> NotificationCenter.getGlobalInstance()
                                .postNotificationName(NotificationCenter.dialogsNeedReload));
                    }
                }
            } catch (Throwable t) {
                FileLog.e(t);
            } finally {
                IN_FLIGHT.remove(fileId);
            }
        });
    }

    private static boolean download(PlatformBridge bridge, String fileId, File file, File etagFile)
            throws Exception {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(bridge.config().url("/api/v1/files/" + fileId + "/content"));
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(bridge.config().connectTimeoutMs);
            connection.setReadTimeout(bridge.config().readTimeoutMs);
            String token = bridge.tokens().accessToken();
            if (token != null) {
                connection.setRequestProperty("Authorization", "Bearer " + token);
            }

            if (file.exists()) {
                String etag = readSidecar(etagFile);
                if (etag != null) {
                    connection.setRequestProperty("If-None-Match", etag);
                }
            }

            int status = connection.getResponseCode();
            if (status == HttpURLConnection.HTTP_NOT_MODIFIED) {
                return false;
            }
            if (status != HttpURLConnection.HTTP_OK) {
                FileLog.e("avatar " + fileId + " -> HTTP " + status);
                return false;
            }

            File temp = new File(file.getAbsolutePath() + ".part");
            try (InputStream in = connection.getInputStream();
                 OutputStream out = new FileOutputStream(temp)) {
                byte[] buffer = new byte[16 * 1024];
                int read;
                while ((read = in.read(buffer)) > 0) {
                    out.write(buffer, 0, read);
                }
            }
            if (!temp.renameTo(file)) {
                FileLog.e("avatar " + fileId + ": cannot move downloaded file");
                return false;
            }
            String etag = connection.getHeaderField("ETag");
            if (etag != null) {
                writeSidecar(etagFile, etag);
            }
            return true;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private static File fileFor(String fileId) {
        File dir = new File(ApplicationLoader.applicationContext.getCacheDir(), "platform_avatars");
        if (!dir.exists()) {
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
        }
        return new File(dir, fileId.replace('/', '_') + ".img");
    }

    private static String readSidecar(File file) {
        try {
            if (!file.exists()) {
                return null;
            }
            byte[] data = new byte[(int) file.length()];
            try (InputStream in = new java.io.FileInputStream(file)) {
                int read = in.read(data);
                return read > 0 ? new String(data, 0, read, "UTF-8") : null;
            }
        } catch (Exception e) {
            return null;
        }
    }

    private static void writeSidecar(File file, String value) {
        try (OutputStream out = new FileOutputStream(file)) {
            out.write(value.getBytes("UTF-8"));
        } catch (Exception e) {
            FileLog.e(e);
        }
    }
}
