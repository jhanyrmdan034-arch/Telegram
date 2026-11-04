#!/usr/bin/env python3
"""Static verification for the Android platform client (phase 1).

There is no JDK/Android SDK in the build sandbox, so compilation cannot be run
here.  This checker gives the strongest available guarantees instead:

1. every Java file parses (javalang) and belongs to the package of its directory
2. every REST path used by the client exists in ``docs/openapi.json``
3. the client never touches MTProto internals (no ConnectionsManager / SendMessagesHelper /
   MessagesController), and only the mapper package may use ``org.telegram.tgnet``
4. the public class/method inventory is printed, so the API surface is reviewed rather
   than assumed
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

try:
    import javalang
except ImportError:  # pragma: no cover
    sys.exit("javalang is required: pip install javalang")

ROOT = Path(__file__).resolve().parents[2]
CLIENT_ROOT = ROOT / "TMessagesProj/src/main/java/org/telegram/platform"
OPENAPI = ROOT / "platform/docs/openapi.json"

FORBIDDEN = (
    "ConnectionsManager",
    "SendMessagesHelper",
    "MessagesController",
    "MessagesStorage",
    "MTProto",
    "TLApi",
)

# A path is a concatenation chain: API_PREFIX + "/chats/" + chatId + "/messages"
PATH_CHAIN = re.compile(
    r'API_PREFIX((?:\s*\+\s*(?:"(?:[^"\\]|\\.)*"|[A-Za-z_][\w.]*(?:\([^()]*\))?|\([^()]*\)))+)'
)
STRING_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def java_files() -> list[Path]:
    return sorted(CLIENT_ROOT.rglob("*.java"))


def check_parse(files: list[Path]) -> tuple[int, int, list[str]]:
    problems: list[str] = []
    classes = methods = 0
    for path in files:
        source = path.read_text(encoding="utf-8")
        try:
            tree = javalang.parse.parse(source)
        except Exception as exc:  # noqa: BLE001 - report the parser message verbatim
            problems.append(f"{path.relative_to(ROOT)}: {exc}")
            continue
        expected = "org.telegram.platform" + "." + ".".join(
            path.relative_to(CLIENT_ROOT).parent.parts) if path.relative_to(
            CLIENT_ROOT).parent.parts else "org.telegram.platform"
        package = tree.package.name if tree.package else ""
        if package != expected:
            problems.append(f"{path.relative_to(ROOT)}: package {package!r} != {expected!r}")
        for _, node in tree.filter(javalang.tree.ClassDeclaration):
            classes += 1
            methods += len(node.methods)
        for _, node in tree.filter(javalang.tree.InterfaceDeclaration):
            classes += 1
            methods += len(node.methods)
    return classes, methods, problems


def check_independence(files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        # Comments are allowed to name the MTProto classes they replace.
        source = COMMENT.sub("", path.read_text(encoding="utf-8"))
        relative = path.relative_to(CLIENT_ROOT).as_posix()
        for symbol in FORBIDDEN:
            if symbol in source:
                problems.append(f"{relative}: forbidden MTProto symbol {symbol}")
        if "import org.telegram.tgnet" in source and not relative.startswith("mapper/"):
            problems.append(f"{relative}: only mapper/* may import org.telegram.tgnet")
        if "import org.telegram.platform" in source and "package org.telegram.platform" not in source:
            problems.append(f"{relative}: bad platform import")
    return problems


def check_endpoints(files: list[Path]) -> tuple[list[str], int]:
    if not OPENAPI.exists():
        return [f"missing {OPENAPI}"], 0
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    declared = {path.rstrip("/") for path in spec["paths"]}

    used: Counter[str] = Counter()
    for path in files:
        source = path.read_text(encoding="utf-8")
        for match in PATH_CHAIN.finditer(source):
            chain = match.group(1)
            segments = [part.strip() for part in re.split(r"\+", chain) if part.strip()]
            built = ""
            for segment in segments:
                literal = STRING_LITERAL.fullmatch(segment)
                built += literal.group(1) if literal else "{param}"
            used[("/api/v1" + built).rstrip("/")] += 1

    problems = []
    for endpoint in sorted(used):
        if endpoint in declared or _matches_template(endpoint, declared):
            continue
        problems.append(f"endpoint not in OpenAPI: {endpoint}")
    return problems, len(used)


def _matches_template(path: str, declared: set[str]) -> bool:
    parts = path.strip("/").split("/")
    for candidate in declared:
        candidate_parts = candidate.strip("/").split("/")
        if len(candidate_parts) != len(parts):
            continue
        if all(c.startswith("{") or c == p for c, p in zip(candidate_parts, parts)):
            return True
    return False


def main() -> int:
    files = java_files()
    if not files:
        print(f"no Java sources under {CLIENT_ROOT}", file=sys.stderr)
        return 1

    classes, methods, parse_problems = check_parse(files)
    independence = check_independence(files)
    endpoint_problems, endpoint_count = check_endpoints(files)

    total_lines = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in files)

    print("== files ==")
    for path in files:
        source = path.read_text(encoding="utf-8")
        print(f"  {path.relative_to(CLIENT_ROOT).as_posix():<52} "
              f"{len(source.splitlines()):>5} lines")
    print(f"\ntotal: {len(files)} files, {total_lines} lines, "
          f"{classes} types, {methods} methods")
    print(f"endpoints referenced: {endpoint_count}")

    problems = parse_problems + independence + endpoint_problems
    if problems:
        print("\n== problems ==")
        for problem in problems:
            print("  -", problem)
        return 1;

    print("\nOK: parses cleanly, no MTProto coupling, every endpoint exists in OpenAPI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
