package org.telegram.platform.tl;

import org.telegram.messenger.FileLog;
import org.telegram.platform.PlatformBridge;
import org.telegram.platform.PlatformException;
import org.telegram.platform.repo.ChatRepository;

/**
 * Dialog-list actions (pin, read, archive, delete, open) routed to our backend
 * instead of MTProto. Every method resolves the mapped dialog id back to the
 * backend id first, so no request can be sent for a wrong peer.
 */
public final class PlatformDialogActions {

    public interface Done {
        void onDone(boolean ok);
    }

    private PlatformDialogActions() {
    }

    /**
     * Resolves a dialog id to the backend chat id. Rows installed by the sync know
     * their chat id directly, which also covers private dialogs where the dialog id
     * is the peer's user id and would otherwise map to the wrong object.
     */
    private static String backendId(long dialogId) {
        String known = PlatformDialogsSync.chatUuidFor(dialogId);
        if (known != null) {
            return known;
        }
        long id = dialogId < 0 ? -dialogId : dialogId;
        return PlatformBridge.get().ids().toUuid(id);
    }

    public static void setPinned(long dialogId, boolean pinned, final Done done) {
        final ChatRepository chats = PlatformBridge.get().chats();
        PlatformBridge.get().executor().execute(() -> {
            boolean ok = false;
            try {
                String chatId = backendId(dialogId);
                if (chatId != null) {
                    chats.pinBlocking(chatId, pinned);
                    ok = true;
                }
            } catch (PlatformException e) {
                FileLog.e("pin: " + e);
            } finally {
                finish(ok, done);
            }
        });
    }

    public static void markRead(long dialogId, int seq, final Done done) {
        final ChatRepository chats = PlatformBridge.get().chats();
        PlatformBridge.get().executor().execute(() -> {
            boolean ok = false;
            try {
                String chatId = backendId(dialogId);
                if (chatId != null) {
                    chats.markReadBlocking(chatId, seq);
                    ok = true;
                }
            } catch (PlatformException e) {
                FileLog.e("markRead: " + e);
            } finally {
                finish(ok, done);
            }
        });
    }

    public static void setArchived(long dialogId, boolean archived, final Done done) {
        final ChatRepository chats = PlatformBridge.get().chats();
        PlatformBridge.get().executor().execute(() -> {
            boolean ok = false;
            try {
                String chatId = backendId(dialogId);
                if (chatId != null) {
                    chats.archiveBlocking(chatId, archived);
                    ok = true;
                }
            } catch (PlatformException e) {
                FileLog.e("archive: " + e);
            } finally {
                finish(ok, done);
            }
        });
    }

    /** Real deletion is only offered where the backend allows it (owned groups). */
    public static void delete(long dialogId, final Done done) {
        final ChatRepository chats = PlatformBridge.get().chats();
        PlatformBridge.get().executor().execute(() -> {
            boolean ok = false;
            try {
                String chatId = backendId(dialogId);
                if (chatId != null) {
                    chats.deleteBlocking(chatId);
                    ok = true;
                }
            } catch (PlatformException e) {
                FileLog.e("delete: " + e);
            } finally {
                finish(ok, done);
            }
        });
    }

    private static void finish(boolean ok, Done done) {
        PlatformDialogsSync.refresh(0);
        if (done != null) {
            org.telegram.messenger.AndroidUtilities.runOnUIThread(() -> done.onDone(ok));
        }
    }
}
