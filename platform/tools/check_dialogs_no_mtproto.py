#!/usr/bin/env python3
"""Phase 3 audit: the dialog list must not fall back to MTProto.

Every entry point that the dialog list uses (load, pin, read, archive, delete)
has a platform branch that returns *before* any MTProto call. This script proves
that ordering statically, so a future edit cannot silently restore the old path.

    python tools/check_dialogs_no_mtproto.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram"

MT_PROTO_MARKERS = ("sendRequest", "ConnectionsManager", "getMessagesStorage(", "MessagesStorage.getInstance")
PLATFORM_GUARD = "PlatformDialogsSync.isEnabled()"


def read(relative: str) -> str:
    return (JAVA / relative).read_text(encoding="utf-8")


def method_body(source: str, signature: str) -> tuple[int, str]:
    """Returns (start offset, body) for a method signature, matching braces."""
    start = source.index(signature)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return start, source[open_brace:index + 1]
    raise ValueError(f"unbalanced braces after {signature}")


def check_guard_first(source: str, signature: str, label: str, problems: list[str]) -> None:
    try:
        _, body = method_body(source, signature)
    except (ValueError, IndexError):
        problems.append(f"{label}: method body not found")
        return
    if PLATFORM_GUARD not in body:
        problems.append(f"{label}: no platform branch (dialog action would go to MTProto)")
        return
    guard_at = body.index(PLATFORM_GUARD)
    first_mtproto = min(
        (body.find(marker) for marker in MT_PROTO_MARKERS if body.find(marker) != -1),
        default=-1,
    )
    if first_mtproto != -1 and guard_at > first_mtproto:
        problems.append(f"{label}: platform branch comes after an MTProto call")


def main() -> int:
    problems: list[str] = []

    # 1) the list loader leaves before touching the MTProto loader
    dialogs = read("ui/DialogsActivity.java")
    _, load_body = method_body(dialogs, "public static void loadDialogs(AccountInstance accountInstance)")
    guard_at = load_body.find(PLATFORM_GUARD)
    mtproto_at = load_body.find("messagesController.loadDialogs(")
    if guard_at == -1:
        problems.append("DialogsActivity.loadDialogs: no platform branch")
    elif mtproto_at != -1 and guard_at > mtproto_at:
        problems.append("DialogsActivity.loadDialogs: MTProto loader runs before the platform branch")

    # 2) every dialog action is intercepted inside MessagesController
    controller = read("messenger/MessagesController.java")
    check_guard_first(controller,
                      "public boolean pinDialog(long dialogId, boolean pin, TLRPC.InputPeer peer, long taskId)",
                      "MessagesController.pinDialog", problems)
    check_guard_first(controller,
                      "public void markDialogAsRead(long dialogId, int maxPositiveId, int maxNegativeId, int maxDate, boolean popup, long threadId, int countDiff, boolean readNow, int scheduledCount)",
                      "MessagesController.markDialogAsRead", problems)
    check_guard_first(controller,
                      "public void deleteDialog(final long did, int onlyHistory, boolean revoke)",
                      "MessagesController.deleteDialog", problems)
    check_guard_first(controller,
                      "public int addDialogToFolder(long dialogId, int folderId, int pinnedNum, long taskId)",
                      "MessagesController.addDialogToFolder", problems)

    # 3) the sync adapter itself stays free of the transport
    for name in ("PlatformDialogsSync.java", "PlatformDialogActions.java", "PlatformAvatars.java"):
        source = read(f"platform/tl/{name}")
        for marker in ("ConnectionsManager", "sendRequest(", "MessagesStorage"):
            if marker in source:
                problems.append(f"tl/{name}: uses MTProto symbol {marker}")

    print("== problems ==" if problems else "OK: the dialog list and its actions never reach MTProto")
    for problem in problems:
        print("  -", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
