package org.telegram.platform.tl;

import com.google.gson.JsonObject;

import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.ApplicationLoader;
import org.telegram.messenger.BuildVars;
import org.telegram.messenger.FileLog;
import org.telegram.messenger.NotificationCenter;
import org.telegram.messenger.UserConfig;
import org.telegram.tgnet.TLRPC;
import org.telegram.platform.PlatformBridge;
import org.telegram.platform.PlatformException;
import org.telegram.platform.repo.Callback;
import org.telegram.platform.repo.MediaRepository;

import java.io.File;
import java.util.ArrayList;
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
    /** 0..1 per local path. Telegram's own progress channel carries ImageLocation
     *  arguments and would throw on our path key, so progress stays here until a
     *  view binds to it. */
    private static final Map<String, Float> PROGRESS = new ConcurrentHashMap<>();

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
                PROGRESS.put(key, total <= 0 ? 0f : Math.min(1f, sent / (float) total));
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
                PROGRESS.remove(key);
                FileLog.e("platform media upload: " + error);
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

    /** 0..1 upload progress for a local path, or -1 when nothing is in flight. */
    public static float uploadProgress(String path) {
        Float value = PROGRESS.get(path);
        return value == null ? -1f : value;
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
        return cacheFile(fileId, null);
    }

    public static File cacheFile(String fileId, String name) {
        File dir = new File(ApplicationLoader.applicationContext.getCacheDir(), "platform_media");
        if (!dir.exists()) {
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
        }
        String suffix = "";
        if (name != null) {
            int dot = name.lastIndexOf('.');
            if (dot > 0 && dot > name.length() - 6) {
                suffix = name.substring(dot);
            }
        }
        return new File(dir, fileId + suffix);
    }

    // --------------------------------------------------------------------------
    // Turning a backend media row into what the existing bubble expects
    // --------------------------------------------------------------------------

    /**
     * Fills {@code message.media} from the {@code file} object of a backend row.
     *
     * <p>Documents use the ordinary document card (name, size, mime, extension come
     * from {@code TL_document} attributes, no network needed). Photos carry a
     * {@code TL_photoSize} with the real dimensions so the bubble can size itself
     * before the bytes are on disk, and the local copy is exposed through
     * {@code attachPath}, which is how Telegram addresses a file it already has.
     */
    public static void applyMedia(TLRPC.TL_message message, JsonObject row, long dialogId) {
        if (message == null || row == null || !row.has("file") || !row.get("file").isJsonObject()) {
            return;
        }
        JsonObject file = row.getAsJsonObject("file");
        String fileId = file.has("id") ? file.get("id").getAsString() : null;
        if (fileId == null) {
            return;
        }
        String kind = file.has("kind") ? file.get("kind").getAsString() : "other";
        String mime = file.has("mime_type") && !file.get("mime_type").isJsonNull()
                ? file.get("mime_type").getAsString() : "application/octet-stream";
        String name = file.has("name") && !file.get("name").isJsonNull()
                ? file.get("name").getAsString() : fileId;
        long size = file.has("size") && !file.get("size").isJsonNull() ? file.get("size").getAsLong() : 0;
        int width = file.has("width") && !file.get("width").isJsonNull() ? file.get("width").getAsInt() : 0;
        int height = file.has("height") && !file.get("height").isJsonNull() ? file.get("height").getAsInt() : 0;
        String type = row.has("type") ? row.get("type").getAsString() : "text";

        final File cached = cacheFile(fileId, name);
        boolean asPhoto = "photo".equals(type) || "sticker".equals(type)
                || ("image/".length() > 0 && mime.startsWith("image/") && !"document".equals(type));

        if (asPhoto) {
            TLRPC.TL_messageMediaPhoto media = new TLRPC.TL_messageMediaPhoto();
            media.photo = buildPhoto(fileId, size, width, height);
            message.media = media;
        } else {
            TLRPC.TL_messageMediaDocument media = new TLRPC.TL_messageMediaDocument();
            media.document = buildDocument(fileId, name, mime, size, width, height, kind);
            message.media = media;
        }
        message.flags |= TLRPC.MESSAGE_FLAG_HAS_MEDIA;
        if (cached.exists() && cached.length() > 0) {
            message.attachPath = cached.getAbsolutePath();
        } else if ("photo".equals(type) || "video".equals(type) || "document".equals(type)) {
            downloadFor(fileId, name);
        }
    }

    private static TLRPC.TL_photo buildPhoto(String fileId, long size, int width, int height) {
        TLRPC.TL_photo photo = new TLRPC.TL_photo();
        photo.id = idOf(fileId);
        photo.dc_id = 0;
        photo.sizes = new ArrayList<>();
        TLRPC.TL_photoSize size_ = new TLRPC.TL_photoSize();
        size_.type = "m";
        size_.w = width;
        size_.h = height;
        size_.size = (int) Math.min(size, Integer.MAX_VALUE);
        size_.location = new TLRPC.TL_fileLocationToBeDeprecated();
        size_.location.volume_id = idOf(fileId);
        size_.location.local_id = 0;
        size_.location.dc_id = 0;
        photo.sizes.add(size_);
        return photo;
    }

    private static TLRPC.TL_document buildDocument(String fileId, String name, String mime, long size,
                                                   int width, int height, String kind) {
        TLRPC.TL_document document = new TLRPC.TL_document();
        document.id = idOf(fileId);
        document.access_hash = 0;
        document.dc_id = 0;
        document.mime_type = mime;
        document.size = size;
        document.file_name = name;
        document.attributes = new ArrayList<>();
        TLRPC.TL_documentAttributeFilename filename = new TLRPC.TL_documentAttributeFilename();
        filename.file_name = name;
        document.attributes.add(filename);
        if (width > 0 && height > 0) {
            TLRPC.TL_documentAttributeImageSize imageSize = new TLRPC.TL_documentAttributeImageSize();
            imageSize.w = width;
            imageSize.h = height;
            document.attributes.add(imageSize);
        }
        if ("video".equals(kind) || mime.startsWith("video/")) {
            TLRPC.TL_documentAttributeVideo video = new TLRPC.TL_documentAttributeVideo();
            video.w = width;
            video.h = height;
            document.attributes.add(video);
        }
        return document;
    }

    private static long idOf(String fileId) {
        // Stable, collision-light id for TL objects that only need an identity.
        return Math.abs(fileId.hashCode()) & 0xffffffffL;
    }

    private static void downloadFor(final String fileId, final String name) {
        download(fileId, null, new Callback<File>() {
            @Override
            public void onSuccess(File file) {
                // The rows that already exist pick the path up on their next bind.
                AndroidUtilities.runOnUIThread(() -> NotificationCenter
                        .getInstance(UserConfig.selectedAccount)
                        .postNotificationName(NotificationCenter.updateInterfaces,
                                org.telegram.messenger.MessagesController.UPDATE_MASK_MESSAGE_TEXT));
            }

            @Override
            public void onError(PlatformException error) {
                FileLog.e("platform media download: " + error);
            }
        });
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
