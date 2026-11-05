#!/usr/bin/env python3
"""Phase 0 — static dependency analysis of the Telegram Android client.

Parses the Java sources (no build required) and produces a machine-readable
inventory plus a human-readable report:

  * every screen class (Activity / Fragment / BottomSheet) in org.telegram.ui
  * the TL types each screen touches
  * the singleton controllers each screen depends on
  * the number of direct ConnectionsManager.sendRequest call sites
  * the mapping of those TL types onto the proprietary REST/WebSocket API

Usage:
    python tools/android_dependency_map.py --repo ../..  --out ../docs/android
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Curated mapping: TL object/method -> proprietary API surface.
# Every entry states which endpoint replaces that dependency.
# --------------------------------------------------------------------------- #
TL_ENDPOINTS: dict[str, list[str]] = {
    # ---- auth & sessions
    "TL_auth_sendCode": ["POST /auth/register", "POST /auth/otp/request"],
    "TL_auth_signIn": ["POST /auth/login", "POST /auth/otp/verify"],
    "TL_auth_signUp": ["POST /auth/register"],
    "TL_auth_logOut": ["POST /auth/logout"],
    "TL_auth_exportAuthorization": ["POST /auth/refresh"],
    "TL_account_getAuthorizations": ["GET /auth/sessions"],
    "TL_account_resetAuthorization": ["DELETE /auth/sessions/{id}"],
    "TL_account_registerDevice": ["PUT /devices/{id}"],
    "TL_account_updateStatus": ["GET /users/me"],
    # ---- profiles & contacts
    "TL_users_getFullUser": ["GET /users/{id}"],
    "TL_users_getUsers": ["GET /users/search", "GET /users/{id}"],
    "TL_account_updateProfile": ["PATCH /users/me"],
    "TL_account_updateUsername": ["PATCH /users/me"],
    "TL_photos_uploadProfilePhoto": ["POST /files", "PATCH /users/me"],
    "TL_contacts_getContacts": ["GET /contacts"],
    "TL_contacts_search": ["GET /users/search"],
    "TL_contacts_addContact": ["POST /contacts"],
    "TL_contacts_deleteContacts": ["DELETE /contacts/{id}"],
    "TL_contacts_block": ["POST /users/{id}/block"],
    "TL_contacts_unblock": ["DELETE /users/{id}/block"],
    "TL_contacts_getBlocked": ["GET /users/blocked"],
    # ---- dialogs & chat list
    "TL_messages_getDialogs": ["GET /chats"],
    "TL_messages_getPeerDialogs": ["GET /chats"],
    "TL_messages_getPinnedDialogs": ["GET /chats/pinned"],
    "TL_messages_toggleDialogPin": ["POST /chats/{id}/pin"],
    "TL_messages_getDialogUnreadMarks": ["GET /chats"],
    "TL_messages_getPeerSettings": ["GET /chats/{id}"],
    "TL_messages_getPeerStories": ["GET /chats/{id}"],
    # ---- messages
    "TL_messages_getHistory": ["GET /chats/{id}/messages"],
    "TL_messages_getMessages": ["GET /chats/{id}/messages/{message_id}"],
    "TL_messages_sendMessage": ["POST /chats/{id}/messages"],
    "TL_messages_sendMedia": ["POST /chats/{id}/messages"],
    "TL_messages_editMessage": ["PATCH /messages/{id}"],
    "TL_messages_deleteMessages": ["DELETE /messages/{id}"],
    "TL_messages_forwardMessages": ["POST /messages/{id}/forward"],
    "TL_messages_readHistory": ["POST /chats/{id}/read"],
    "TL_messages_setTyping": ["POST /chats/{id}/typing"],
    "TL_messages_sendReaction": ["PUT /messages/{id}/reaction"],
    "TL_messages_search": ["GET /chats/{id}/messages/search"],
    "TL_messages_getMessageEditData": ["GET /chats/{id}/messages/{message_id}"],
    # ---- groups & channels
    "TL_messages_createChat": ["POST /chats"],
    "TL_messages_addChatUser": ["POST /chats/{id}/members"],
    "TL_messages_deleteChatUser": ["DELETE /chats/{id}/members/{member_id}"],
    "TL_messages_editChatTitle": ["PATCH /chats/{id}"],
    "TL_messages_editChatPhoto": ["PATCH /chats/{id}"],
    "TL_messages_getFullChat": ["GET /chats/{id}"],
    "TL_messages_getChats": ["GET /chats/{id}"],
    "TL_channels_createChannel": ["POST /chats"],
    "TL_channels_editAdmin": ["PATCH /chats/{id}/members/{member_id}"],
    "TL_channels_editBanned": ["PATCH /chats/{id}/members/{member_id}"],
    "TL_channels_getParticipants": ["GET /chats/{id}/members"],
    "TL_channels_joinChannel": ["POST /chats/{id}/members"],
    "TL_channels_leaveChannel": ["POST /chats/{id}/leave"],
    "TL_channels_deleteChannel": ["DELETE /chats/{id}"],
    "TL_channels_getFullChannel": ["GET /chats/{id}"],
    "TL_channels_toggleSignatures": ["PATCH /chats/{id}"],
    # ---- media & files
    "TL_upload_saveFilePart": ["POST /files"],
    "TL_upload_getFile": ["GET /files/{id}/content"],
    "TL_messages_uploadMedia": ["POST /files"],
    "TL_messages_getDocumentByHash": ["GET /files/{id}"],
    "TL_photos_getUserPhotos": ["GET /users/{id}"],
    "TL_messages_searchGifs": ["GET /files/{id}"],
    # ---- stickers & emoji
    "TL_messages_getStickerSet": ["GET /stickers/{name}"],
    "TL_messages_getAllStickers": ["GET /stickers"],
    "TL_messages_getEmojiStickerSets": ["GET /stickers"],
    "TL_messages_installStickerSet": ["POST /stickers/{name}/install"],
    # ---- calls
    "TL_phone_requestCall": ["POST /chats/{id}/calls"],
    "TL_phone_acceptCall": ["POST /calls/{id}/status"],
    "TL_phone_discardCall": ["POST /calls/{id}/status"],
    "TL_phone_receivedCall": ["POST /calls/{id}/status"],
    "TL_phone_getCallConfig": ["GET /calls/ice"],
    "TL_phone_confirmCall": ["POST /calls/{id}/status"],
    # ---- notifications & support
    "TL_account_reportPeer": ["POST /reports"],
    "TL_help_getSupport": ["POST /tickets"],
    "TL_help_getConfig": ["GET /config"],
    "TL_help_getAppConfig": ["GET /config"],
    "TL_help_getCountriesList": ["GET /config"],
    "TL_help_getNearestDc": ["GET /config"],
    "TL_help_getTermsOfService": ["GET /config"],
    # ---- features with no proprietary backend yet (must be hidden or built)
    "TL_payments_getPaymentForm": ["HANGS: billing"],
    "TL_payments_sendPaymentForm": ["HANGS: billing"],
    "TL_payments_getStarsStatus": ["HANGS: stars"],
    "TL_payments_sendStarsForm": ["HANGS: stars"],
    "TL_premium_getStatus": ["HANGS: premium"],
    "TL_stories_sendStory": ["HANGS: stories"],
    "TL_stories_getAllStories": ["HANGS: stories"],
    "TL_bots_setBotCommands": ["HANGS: bots"],
    "TL_messages_getInlineBotResults": ["HANGS: bots"],
    "TL_wallet_*": ["HANGS: wallet"],
    "TL_messages_startBot": ["HANGS: bots"],
    "TL_account_getThemes": ["LOCAL"],
}

DOMAIN_RULES: list[tuple[str, re.Pattern]] = [
    ("auth", re.compile(r"^TL_(auth|account)_(sendCode|signIn|signUp|logOut|exportAuthorization|getAuthorizations|resetAuthorization|registerDevice|updateStatus)")),
    ("profile", re.compile(r"^TL_(users_getFullUser|account_updateProfile|account_updateUsername|photos_uploadProfilePhoto)")),
    ("contacts", re.compile(r"^TL_contacts_")),
    ("chats", re.compile(r"^TL_messages_(getDialogs|getPeerDialogs|getPinnedDialogs|toggleDialogPin|getPeerSettings|getDialogUnreadMarks)")),
    ("messages", re.compile(r"^TL_messages_(getHistory|getMessages|sendMessage|sendMedia|editMessage|deleteMessages|forwardMessages|readHistory|setTyping|search|getMessageEditData|sendReaction)")),
    ("groups", re.compile(r"^TL_(messages_(createChat|addChatUser|deleteChatUser|editChatTitle|editChatPhoto|getFullChat|getChats)|channels_)")),
    ("media", re.compile(r"^TL_(upload_|messages_uploadMedia|messages_getDocumentByHash|photos_getUserPhotos)")),
    ("stickers", re.compile(r"^TL_messages_(getStickerSet|getAllStickers|getEmojiStickerSets|installStickerSet)")),
    ("calls", re.compile(r"^TL_phone_")),
    ("config", re.compile(r"^TL_help_(getConfig|getAppConfig|getCountriesList|getInviteText|getNearestDc)$")),
    ("support", re.compile(r"^TL_(account_reportPeer|help_getSupport)$")),
    ("billing", re.compile(r"^TL_(payments_|premium_)")),
    ("stories", re.compile(r"^TL_stories_")),
    ("bots", re.compile(r"^TL_(bots_|messages_(getInlineBotResults|startBot))")),
    ("wallet", re.compile(r"^TL_wallet_")),
]

CONTROLLERS = [
    "MessagesController", "MessagesStorage", "SendMessagesHelper", "MediaController",
    "MediaDataController", "ContactsController", "NotificationsController",
    "LocationController", "DownloadController", "FileLoader", "FileRefController",
    "UserConfig", "AccountInstance", "ConnectionsManager", "ChatThemeController",
    "StoriesController", "MessagesCustomEmoji", "BillingController", "PremiumFeature",
    "SendMessagesHelper", "ChatAttachAlert", "ImageLoader", "LocaleController",
]

# Controllers every screen may reach through the shared account singleton. They are
# bridged once by the platform adapter, not per screen, so they are excluded from
# the per-screen closure to keep the map readable.
CORE_CONTROLLERS = {
    "MessagesController", "MessagesStorage", "ConnectionsManager", "UserConfig",
    "AccountInstance", "LocaleController", "NotificationsController", "MediaController",
    "DownloadController", "FileLoader", "ImageLoader",
}

MODEL_CLASSES = [
    "MessageObject", "TLRPC", "TLObject", "ChatObject", "UserObject", "DialogObject",
    "MessageCustomEmoji", "WebPage", "ImageLocation", "ChatTheme", "SavedMessagesController",
]

SCREEN_RE = re.compile(
    r"public\s+(?:final\s+|abstract\s+)?class\s+(?P<name>\w+)\s+extends\s+(?P<super>[\w.]+)"
)
TL_RE = re.compile(r"TLRPC\.(TL_\w+)")
CONN_RE = re.compile(r"ConnectionsManager\.getInstance\([^)]*\)\.sendRequest")


@dataclass
class Screen:
    name: str
    path: str
    superclass: str
    lines: int
    tl_types: set[str] = field(default_factory=set)
    effective_tl: set[str] = field(default_factory=set)
    controllers: set[str] = field(default_factory=set)
    models: set[str] = field(default_factory=set)
    send_request_count: int = 0

    @property
    def domains(self) -> set[str]:
        domains = set()
        for tl in self.effective_tl:
            for name, pattern in DOMAIN_RULES:
                if pattern.match(tl):
                    domains.add(name)
        return domains

    @property
    def endpoints(self) -> set[str]:
        out: set[str] = set()
        for tl in self.effective_tl:
            for endpoint in TL_ENDPOINTS.get(tl, []):
                out.add(endpoint)
        return out

    @property
    def blocked_by(self) -> set[str]:
        return {e.split(": ", 1)[1] for e in self.endpoints if e.startswith("HANGS: ")}


def index_classes(root: Path) -> dict[str, set[str]]:
    """Map every class name to the TL types its file references (depth-1 resolution)."""
    index: dict[str, set[str]] = {}
    base = root / "TMessagesProj/src/main/java/org/telegram"
    for path in base.rglob("*.java"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        index[path.stem] = set(TL_RE.findall(text))
    return index


def resolve(screen: Screen, index: dict[str, set[str]], depth: int = 2) -> set[str]:
    """Direct TL usage plus everything reachable through the controllers it calls."""
    seen = set(screen.tl_types)
    frontier = set(screen.controllers)
    for _ in range(depth):
        nxt: set[str] = set()
        for name in frontier:
            for tl in index.get(name, set()):
                if tl not in seen:
                    seen.add(tl)
        # Controllers also instantiate other controllers (SendMessagesHelper -> FileLoader ...)
        for name in list(frontier):
            for other, tl_set in index.items():
                if other in screen.controllers or other in frontier:
                    continue
        frontier = nxt
    return seen


def is_screen(superclass: str) -> bool:
    simple = superclass.split(".")[-1]
    return (
        simple.endswith("Activity")
        or simple.endswith("Fragment")
        or simple.endswith("BottomSheet")
        or simple in {"BaseFragment", "ActionBarLayout"}
    )


def analyse(root: Path) -> list[Screen]:
    ui_root = root / "TMessagesProj/src/main/java/org/telegram/ui"
    screens: list[Screen] = []
    for path in ui_root.rglob("*.java"):
        if "/Cells/" in str(path) or "/Adapters/" in str(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = SCREEN_RE.search(text)
        if not match or not is_screen(match.group("super")):
            continue
        screen = Screen(
            name=match.group("name"),
            path=str(path.relative_to(root)),
            superclass=match.group("super"),
            lines=text.count("\n") + 1,
        )
        screen.tl_types = set(TL_RE.findall(text))
        screen.controllers = {c for c in CONTROLLERS if c in text}
        screen.models = {m for m in MODEL_CLASSES if m in text}
        screen.send_request_count = len(CONN_RE.findall(text))
        screens.append(screen)
    index = index_classes(root)
    # Expand each screen through the *feature* controllers it uses (depth 1).
    # Core singletons are bridged once by the adapter layer, so they are excluded.
    for screen in screens:
        closure: set[str] = set(screen.tl_types)
        for controller in screen.controllers - CORE_CONTROLLERS:
            closure |= index.get(controller, set())
        screen.effective_tl = closure
    screens.sort(key=lambda s: s.name)
    return screens


def build_report(screens: list[Screen]) -> dict:
    domain_counter: Counter[str] = Counter()
    endpoint_counter: Counter[str] = Counter()
    blocked_counter: Counter[str] = Counter()
    for screen in screens:
        domain_counter.update(screen.domains)
        endpoint_counter.update(screen.endpoints)
        blocked_counter.update(screen.blocked_by)

    tl_counter: Counter[str] = Counter()
    for screen in screens:
        tl_counter.update(screen.effective_tl)

    return {
        "summary": {
            "screens": len(screens),
            "screens_with_network_calls": sum(1 for s in screens if s.send_request_count),
            "direct_send_request_sites": sum(s.send_request_count for s in screens),
            "distinct_tl_types_in_ui": len(tl_counter),
            "total_screen_lines": sum(s.lines for s in screens),
        },
        "domains": domain_counter.most_common(),
        "endpoints": endpoint_counter.most_common(),
        "blocked_features": blocked_counter.most_common(),
        "top_tl_types": tl_counter.most_common(40),
        "unmapped_tl_types": sorted(t for t in tl_counter if t not in TL_ENDPOINTS)[:60],
        "screens": [
            {
                "name": s.name,
                "path": s.path,
                "superclass": s.superclass,
                "lines": s.lines,
                "send_request_sites": s.send_request_count,
                "controllers": sorted(s.controllers),
                "domains": sorted(s.domains),
                "blocked_features": sorted(s.blocked_by),
                "endpoints": sorted(s.endpoints),
                "tl_types": sorted(s.effective_tl),
            }
            for s in screens
        ],
    }


def write_markdown(report: dict, out: Path) -> None:
    summary = report["summary"]
    lines = [
        "# Phase 0 — Android client dependency inventory (generated)",
        "",
        "> Generated by `platform/tools/android_dependency_map.py` directly from the Java sources.",
        "> Re-run it after any client change: `python tools/android_dependency_map.py`.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for key, value in summary.items():
        lines.append(f"| {key.replace('_', ' ')} | {value} |")

    lines += ["", "## Backend domains required by the UI", "", "| Domain | Screens |", "|---|---|"]
    for domain, count in report["domains"]:
        lines.append(f"| {domain} | {count} |")

    lines += ["", "## Features with no proprietary backend yet", "", "| Feature | Screens affected |", "|---|---|"]
    for feature, count in report["blocked_features"]:
        lines.append(f"| {feature} | {count} |")

    lines += ["", "## Most required API endpoints", "", "| Endpoint | Screens |", "|---|---|"]
    for endpoint, count in report["endpoints"][:40]:
        lines.append(f"| `{endpoint}` | {count} |")

    lines += ["", "## Screens (full inventory)", "",
              "| Screen | Superclass | Lines | sendRequest | Domains | Endpoints needed | Missing backend |",
              "|---|---|---:|---:|---|---|---|"]
    for screen in report["screens"]:
        endpoints = "<br>".join(f"`{e}`" for e in screen["endpoints"]) or "—"
        domains = ", ".join(screen["domains"]) or "—"
        blocked = ", ".join(screen["blocked_features"]) or "—"
        lines.append(
            f"| `{screen['name']}` | {screen['superclass']} | {screen['lines']} | "
            f"{screen['send_request_sites']} | {domains} | {endpoints} | {blocked} |"
        )

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="../../..", help="repository root")
    parser.add_argument("--out", default="../docs/android", help="output directory")
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    screens = analyse(root)
    report = build_report(screens)
    (out / "dependency-map.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, out / "phase0-inventory.md")

    s = report["summary"]
    print(f"screens={s['screens']} lines={s['total_screen_lines']} "
          f"sendRequest={s['direct_send_request_sites']} tlTypes={s['distinct_tl_types_in_ui']}")
    print("domains:", ", ".join(f"{d}={c}" for d, c in report["domains"]))
    print("blocked:", ", ".join(f"{d}={c}" for d, c in report["blocked_features"]))
    print(f"wrote {out/'phase0-inventory.md'} and dependency-map.json")


if __name__ == "__main__":
    main()
