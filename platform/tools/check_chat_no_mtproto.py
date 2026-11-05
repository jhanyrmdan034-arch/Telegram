#!/usr/bin/env python3
"""Phase 4 audit: every migrated chat operation must branch out before MTProto.

For each seam the platform branch has to appear inside the method body *before*
the first MTProto marker, so a later edit cannot silently restore Telegram's path.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram"

MT_PROTO = ("sendRequest", "ConnectionsManager", "getMessagesStorage(", "MessagesStorage.getInstance",
            "TLRPC.TL_messages_")
GUARD = ("PlatformChatSync.isEnabled()", "PlatformDialogsSync.isEnabled()",
         "PlatformContactsSync.isEnabled()", "PlatformMediaSync.isEnabled()")

SEAMS = [
    ("ui/DialogsActivity.java", "public static void loadDialogs(AccountInstance accountInstance)", "dialog list load"),
    ("messenger/MessagesController.java",
     "public void loadMessages(long dialogId, long mergeDialogId, boolean loadInfo, int count, int max_id, int offset_date, boolean fromCache, int midDate, int classGuid, int load_type, int last_message_id, int mode, long threadMessageId, int loadIndex, int first_unread, int unread_count, int last_date, boolean queryFromServer, int mentionsCount, boolean isTopic)",
     "history load"),
    ("messenger/SendMessagesHelper.java", "public void sendMessage(SendMessageParams sendMessageParams)", "send text"),
    ("messenger/SendMessagesHelper.java",
     "public int editMessage(MessageObject messageObject, String message, boolean searchLinks, final BaseFragment fragment, ArrayList<TLRPC.MessageEntity> entities, int scheduleDate, int scheduleRepeatPeriod)",
     "edit text"),
    ("messenger/MessagesController.java",
     "public void deleteMessages(ArrayList<Integer> messages, ArrayList<Long> randoms, TLRPC.EncryptedChat encryptedChat, long dialogId, boolean forAll, int mode, boolean cacheOnly, long taskId, TLObject taskRequest, int topicId, boolean movedToScheduled, int movedToScheduledMessageId)",
     "delete messages"),
    ("messenger/MessagesController.java",
     "public boolean sendTyping(long dialogId, long threadMsgId, int action, int classGuid)", "typing send"),
    ("messenger/MessagesController.java",
     "public void markDialogAsRead(long dialogId, int maxPositiveId, int maxNegativeId, int maxDate, boolean popup, long threadId, int countDiff, boolean readNow, int scheduledCount)",
     "read receipt"),
    ("messenger/MediaDataController.java",
     "public void searchMessagesInChat(String query, long dialogId, long mergeDialogId, int guid, int direction, long replyMessageId, boolean internal, TLRPC.User user, TLRPC.Chat chat, boolean jumpToMessage, ReactionsLayoutInBubble.VisibleReaction reaction)",
     "in-chat search"),
    ("messenger/MessagesController.java",
     "public boolean pinDialog(long dialogId, boolean pin, TLRPC.InputPeer peer, long taskId)", "pin"),
    ("messenger/MessagesController.java",
     "public int addDialogToFolder(long dialogId, int folderId, int pinnedNum, long taskId)", "archive"),
    ("messenger/ContactsController.java",
     "public void loadContacts(boolean fromCache, final long hash)", "contacts load"),
    ("messenger/MessagesController.java",
     "public void loadFullUser(final TLRPC.User user, int classGuid, boolean force)", "profile load"),
    ("ui/Adapters/SearchAdapter.java",
     "public void searchDialogs(final String query)", "global search"),
    ("messenger/SendMessagesHelper.java", "public void sendMessage(SendMessageParams sendMessageParams)",
     "send media (photo/document)"),
]


def body(source: str, signature: str) -> str:
    start = source.index(signature)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace:index + 1]
    raise ValueError("unbalanced braces")


def main() -> int:
    problems: list[str] = []
    cache: dict[str, str] = {}
    for relative, signature, label in SEAMS:
        source = cache.setdefault(relative, (JAVA / relative).read_text(encoding="utf-8"))
        try:
            method = body(source, signature)
        except (ValueError, IndexError):
            problems.append(f"{label}: method not found in {relative}")
            continue
        guard_at = min((method.find(g) for g in GUARD if method.find(g) != -1), default=-1)
        if guard_at == -1:
            problems.append(f"{label}: no platform branch")
            continue
        first_mtproto = min((method.find(m) for m in MT_PROTO if method.find(m) != -1), default=-1)
        if first_mtproto != -1 and guard_at > first_mtproto:
            problems.append(f"{label}: platform branch after an MTProto call")

    for name in ("PlatformChatSync.java", "PlatformDialogsSync.java", "PlatformDialogActions.java",
                 "PlatformAvatars.java", "PlatformContactsSync.java"):
        source = (JAVA / "platform/tl" / name).read_text(encoding="utf-8")
        for marker in ("ConnectionsManager", "sendRequest(", "MessagesStorage"):
            if marker in source:
                problems.append(f"tl/{name}: uses MTProto symbol {marker}")

    # MediaRepository is pure HTTP. PlatformMediaSync is allowed to construct TL
    # objects (a bubble needs them) but must never call into MTProto.
    banned = {
        "platform/repo/MediaRepository.java": ("ConnectionsManager", "sendRequest(", "MessagesStorage",
                                               "TLRPC.", "FileLoader"),
        "platform/tl/PlatformMediaSync.java": ("ConnectionsManager", "sendRequest(", "MessagesStorage",
                                               "FileLoader", "getFileLoader", "ImageLoader"),
    }
    for relative, markers in banned.items():
        source = (JAVA / relative).read_text(encoding="utf-8")
        for marker in markers:
            if marker in source:
                problems.append(f"{relative}: uses MTProto symbol {marker}")

    print("== problems ==" if problems else
          f"OK: {len(SEAMS)} chat/dialog entry points branch out before MTProto")
    for problem in problems:
        print("  -", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
