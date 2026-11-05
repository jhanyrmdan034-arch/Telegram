package org.telegram.platform.tl;

import com.google.gson.JsonObject;

import org.telegram.messenger.FileLog;
import org.telegram.messenger.MessageObject;
import org.telegram.messenger.SendMessagesHelper;
import org.telegram.platform.PlatformBridge;
import org.telegram.platform.PlatformException;
import org.telegram.platform.net.PlatformApi;
import org.telegram.platform.repo.Callback;
import org.telegram.platform.repo.MediaRepository;

import java.io.File;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Local-first media sending: the bubble exists before the upload finishes.
 *
 * <p>Telegram already does this for text and its own uploads: a message is created
 * with {@code MESSAGE_SEND_STATE_SENDING}, put into the chat, and later flipped to
 * SENT (or to SEND_ERROR, which is what makes the existing "tap to retry" work).
 * This class drives the same states for our own file store:
 *
 * <ol>
 *   <li>the chat shows the bubble and the local preview immediately,</li>
 *   <li>bytes stream to our server with progress and a cancel flag,</li>
 *   <li>the message row is created on the backend,</li>
 *   <li>the bubble is confirmed with the backend id (a duplicate frame is dropped
 *       by the id set in {@link PlatformChatSync}),</li>
 *   <li>failure leaves the bubble in SEND_ERROR so the existing retry gesture
 *       re-runs the upload instead of contacting Telegram.</li>
 * </ol>
 */
public final class PlatformLocalSend {

    private static final Map<Integer, AtomicBoolean> CANCELLED = new ConcurrentHashMap<>();
    private static final Map<Integer, Pending> PENDING = new ConcurrentHashMap<>();

    private PlatformLocalSend() {
    }

    private static final class Pending {
        final String path;
        final String kind;
        final String caption;
        final long replyTo;

        Pending(String path, String kind, String caption, long replyTo) {
            this.path = path;
            this.kind = kind;
            this.caption = caption;
            this.replyTo = replyTo;
        }
    }

    public static boolean isEnabled() {
        return org.telegram.messenger.BuildVars.LOCAL_FIRST_MEDIA && PlatformMediaSync.isEnabled();
    }

    /** Uploads the file of an already visible local message. */
    public static void start(final SendMessagesHelper helper, final MessageObject object,
                             final String path, final String kind, final String caption,
                             final long replyTo) {
        if (!isEnabled() || object == null) {
            return;
        }
        final int localId = object.getId();
        final File file = new File(path);
        if (!file.exists()) {
            helper.platformFailLocalMedia(object);
            return;
        }
        final String chatId = PlatformDialogsSync.chatUuidFor(object.getDialogId());
        if (chatId == null) {
            helper.platformFailLocalMedia(object);
            return;
        }
        final AtomicBoolean cancel = new AtomicBoolean(false);
        CANCELLED.put(localId, cancel);
        PENDING.put(localId, new Pending(path, kind, caption, replyTo));
        PlatformBridge.get().media().upload(file, kind, PlatformMediaSync.guessMime(file),
                new MediaRepository.Progress() {
                    @Override
                    public void onProgress(long sent, long total) {
                        // The bubble keeps its own "sending" state; the numbers are
                        // available to any view that asks via uploadProgress().
                        PlatformMediaSync.noteProgress(path, sent, total);
                    }
                }, MediaRepository.cancelFlag(cancel), new Callback<JsonObject>() {
                    @Override
                    public void onSuccess(JsonObject uploaded) {
                        CANCELLED.remove(localId);
                        PENDING.remove(localId);
                        createMessage(helper, object, chatId, uploaded, kind, caption, replyTo);
                    }

                    @Override
                    public void onError(PlatformException error) {
                        CANCELLED.remove(localId);
                        FileLog.e("platform local media: " + error);
                        helper.platformFailLocalMedia(object);
                    }
                });
    }

    private static void createMessage(SendMessagesHelper helper, MessageObject object, String chatId,
                                      JsonObject uploaded, String kind, String caption,
                                      long replyTo) {
        try {
            JsonObject body = new JsonObject();
            body.addProperty("type", kind);
            body.addProperty("text", caption == null ? "" : caption);
            body.addProperty("file_id", uploaded.get("id").getAsString());
            body.addProperty("client_message_id", "local-" + object.getId());
            JsonObject row = PlatformBridge.get().api().post(
                    PlatformApi.API_PREFIX + "/chats/" + chatId + "/messages", body);
            helper.platformConfirmLocalMedia(object,
                    PlatformBridge.get().ids().toInt(row.get("id").getAsString()));
        } catch (PlatformException error) {
            FileLog.e("platform local media message: " + error);
            helper.platformFailLocalMedia(object);
        }
    }

    /** Called by the existing cancel gesture. */
    public static void cancel(MessageObject object) {
        if (object == null) {
            return;
        }
        AtomicBoolean flag = CANCELLED.remove(object.getId());
        PENDING.remove(object.getId());
        if (flag != null) {
            flag.set(true);
        }
    }

    /** Called from the retry gesture: re-uploads the same local file. */
    public static boolean retry(SendMessagesHelper helper, MessageObject object) {
        if (!isEnabled() || object == null) {
            return false;
        }
        Pending pending = PENDING.get(object.getId());
        if (pending == null) {
            return false;
        }
        start(helper, object, pending.path, pending.kind, pending.caption, pending.replyTo);
        return true;
    }
}
