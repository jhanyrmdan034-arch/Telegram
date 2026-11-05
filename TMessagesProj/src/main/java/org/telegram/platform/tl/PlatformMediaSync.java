package org.telegram.platform.tl;

import com.google.gson.JsonObject;

import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.ApplicationLoader;
import org.telegram.messenger.BuildVars;
import org.telegram.messenger.FileLog;
import org.telegram.messenger.NotificationCenter;
import org.telegram.platform.PlatformBridge;
import org.telegram.platform.PlatformException;
import org.telegram.platform.repo.Callback;
import org.telegram.platform.repo.MediaRepository;

import java.io.File;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Sending and fetching media through our own file store.
 *
 * <p>Uploads stream from the picked file, report progress, can be cancelled and can
 * be retried (the server de-duplicates identical bytes). Downloads land in a cache
 * directory keyed by file id, so the same picture is fetched once.
 */
public final class PlatformMediaSync {

    private static final Map<String, AtomicBoolean> CANCELLED = new ConcurrentHashMap<>();

    private PlatformMediaSync() {
    }

    public static boolean isEnabled() {
        return BuildVars.MEDIA_FROM_PLATFORM && PlatformBridge.isReady();
    }

    /** Uploads the file, then creates the message that carries it. */
    public static void sendMedia(final long dialogId, final File file, final String kind,
                                 final String caption, final String replyToMessageId) {
        if (!isEnabled() || file == null || !file.exists()) {
            return;
        }
        final String chatId = PlatformDialogsSync.chatUuidFor(dialogId);
        if (chatId == null) {
            FileLog.e("platform media: no backend chat for " + dialogId);
            return;
        }
        final String key = file.getAbsolutePath();
        final AtomicBoolean cancel = new AtomicBoolean(false);
        CANCELLED.put(key, cancel);
        PlatformBridge.get().media().upload(file, kind, guessMime(file), new MediaRepository.Progress() {
            @Override
            public void onProgress(long sent, long total) {
                // Same notification the built-in uploader uses, so a bound progress
                // view keeps working without any UI change.
                AndroidUtilities.runOnUIThread(() -> NotificationCenter.getGlobalInstance()
                        .postNotificationName(NotificationCenter.fileUploadProgressChanged,
                                key, sent, total, false));
            }
        }, MediaRepository.cancelFlag(cancel), new Callback<JsonObject>() {
            @Override
            public void onSuccess(JsonObject uploaded) {
                CANCELLED.remove(key);
                createMediaMessage(chatId, uploaded, kind, caption, replyToMessageId);
            }

            @Override
            public void onError(PlatformException error) {
                CANCELLED.remove(key);
                FileLog.e("platform media upload: " + error);
                AndroidUtilities.runOnUIThread(() -> NotificationCenter.getGlobalInstance()
                        .postNotificationName(NotificationCenter.fileUploadProgressChanged,
                                key, -1L, -1L, false));
            }
        });
    }

    private static void createMediaMessage(String chatId, JsonObject uploaded, String kind,
                                           String caption, String replyToMessageId) {
        try {
            JsonObject body = new JsonObject();
            body.addProperty("type", kind);
            body.addProperty("text", caption == null ? "" : caption);
            body.addProperty("file_id", uploaded.get("id").getAsString());
            if (replyToMessageId != null) {
                body.addProperty("reply_to_id", replyToMessageId);
            }
            PlatformBridge.get().api().post(
                    org.telegram.platform.net.PlatformApi.API_PREFIX + "/chats/" + chatId + "/messages",
                    body);
        } catch (PlatformException e) {
            FileLog.e("platform media message: " + e);
        }
    }

    /** Cancels an upload that is in flight for this path. */
    public static void cancelUpload(String path) {
        AtomicBoolean flag = CANCELLED.get(path);
        if (flag != null) {
            flag.set(true);
        }
    }

    /** Cached local copy of a file, downloading it if needed. */
    public static File cacheFile(String fileId) {
        File dir = new File(ApplicationLoader.applicationContext.getCacheDir(), "platform_media");
        if (!dir.exists()) {
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
        }
        return new File(dir, fileId + ".bin");
    }

    public static void download(final String fileId, final MediaRepository.Progress progress,
                                final Callback<File> callback) {
        if (!isEnabled()) {
            return;
        }
        PlatformBridge.get().media().download(fileId, cacheFile(fileId), progress, null, callback);
    }

    /** Picks the mime type the picker gave us, falling back to the extension. */
    static String guessMime(File file) {
        String name = file.getName().toLowerCase();
        if (name.endsWith(".jpg") || name.endsWith(".jpeg")) {
            return "image/jpeg";
        }
        if (name.endsWith(".png")) {
            return "image/png";
        }
        if (name.endsWith(".webp")) {
            return "image/webp";
        }
        if (name.endsWith(".gif")) {
            return "image/gif";
        }
        if (name.endsWith(".mp4")) {
            return "video/mp4";
        }
        if (name.endsWith(".mov")) {
            return "video/quicktime";
        }
        if (name.endsWith(".webm")) {
            return "video/webm";
        }
        if (name.endsWith(".pdf")) {
            return "application/pdf";
        }
        if (name.endsWith(".ogg") || name.endsWith(".opus")) {
            return "audio/ogg";
        }
        if (name.endsWith(".mp3")) {
            return "audio/mpeg";
        }
        return "application/octet-stream";
    }
}
