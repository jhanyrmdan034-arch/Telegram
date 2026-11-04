#!/usr/bin/env python3
"""Checks the Android TL adapter against the real TL class definitions.

The interceptor writes fields on synthesized TL objects. A typo there compiles
against nothing (no SDK in this sandbox) and would only show up as a crash on a
device, so every field it assigns is verified against ``TLRPC.java`` itself:
the declared type, its inheritance chain, and the arity of the platform methods
it calls.

Exit code is non-zero when the adapter and the TL/API surface disagree.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TLRPC = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/TLRPC.java"
CLIENT = ROOT / "TMessagesProj/src/main/java/org/telegram/platform"

CLASS_DECL = re.compile(r"^\s{4}public static (?:abstract )?class (\w+)(?: extends ([\w.]+))?")
FIELD_DECL = re.compile(r"^\s{8}public (?!static |final )([\w<>\[\].]+) (\w+);")
LOCAL_DECL = re.compile(r"TLRPC\.(\w+) (\w+)\s*=\s*new TLRPC\.\w+\(\)")
PARAM_DECL = re.compile(r"\((?:[^()]|\([^()]*\))*\)")
ASSIGNMENT = re.compile(r"\b(\w+)\.(\w+)\s*=(?!=)")
COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def strip_comments(source: str) -> str:
    """Keeps line structure so javadoc examples are not mistaken for real calls."""
    return COMMENTS.sub(lambda m: "\n" * m.group(0).count("\n"), source)

# Fields inherited from TLObject (everything in TLRPC ultimately extends it).
TL_OBJECT_FIELDS = {"networkType", "readParams", "deserializeResponse"}


def load_tl_index() -> dict[str, dict]:
    """name -> {base, fields} for every class declared in TLRPC.java."""
    index: dict[str, dict] = {}
    current = None
    for line in TLRPC.read_text(encoding="utf-8").splitlines():
        decl = CLASS_DECL.match(line)
        if decl:
            current = decl.group(1)
            index[current] = {"base": decl.group(2), "fields": set()}
            continue
        if current and line.startswith("    }") and line.strip() == "}":
            current = None
            continue
        if current:
            field = FIELD_DECL.match(line)
            if field:
                index[current]["fields"].add(field.group(2))
    return index


def resolve_fields(index: dict[str, dict], name: str) -> set[str]:
    fields: set[str] = set()
    seen = 0
    while name and name in index and seen < 10:
        fields |= index[name]["fields"]
        name = index[name]["base"]
        seen += 1
    return fields | TL_OBJECT_FIELDS


def variables(source: str) -> dict[str, str]:
    """Local variable -> TL class, from `TLRPC.X var = new TLRPC.X()` declarations."""
    return {match.group(2): match.group(1) for match in LOCAL_DECL.finditer(source)}


def _arguments(source: str, start: int) -> tuple[int, int]:
    """Counts arguments of the call whose '(' ends at ``start``; returns (arity, end)."""
    depth, argc, seen_content, index = 0, 1, False, start
    while index < len(source):
        char = source[index]
        if char in "([{":
            depth += 1
            seen_content = True
        elif char in ")]}":
            if depth == 0:
                return (argc if seen_content else 0), index
            depth -= 1
        elif char == '"':
            # skip string literal
            index += 1
            while index < len(source) and source[index] != '"':
                if source[index] == "\\":
                    index += 1
                index += 1
            seen_content = True
        elif char == "," and depth == 0:
            argc += 1
        elif not char.isspace():
            seen_content = True
        index += 1
    return argc, index


def method_declarations(path: Path) -> dict[str, set[int]]:
    """public method name -> set of arities, from one of our own classes."""
    source = path.read_text(encoding="utf-8")
    result: dict[str, set[int]] = {}
    for match in re.finditer(r"public (?:static )?(?:final )?[\w<>\[\], .]+ (\w+)\(", source):
        arity, _ = _arguments(source, match.end())
        result.setdefault(match.group(1), set()).add(arity)
    return result


def main() -> int:
    index = load_tl_index()
    if not index:
        print("cannot parse TLRPC.java", file=sys.stderr)
        return 1

    problems: list[str] = []
    checked_fields = 0
    checked_calls = 0

    for path in sorted(CLIENT.rglob("*.java")):
        source = strip_comments(path.read_text(encoding="utf-8"))
        if "TLRPC." not in source:
            continue
        local_types = variables(source)
        for match in ASSIGNMENT.finditer(source):
            variable, field = match.group(1), match.group(2)
            tl_class = local_types.get(variable)
            if tl_class is None:
                continue
            checked_fields += 1
            if tl_class not in index:
                problems.append(f"{path.name}: unknown TL class {tl_class}")
                continue
            if field not in resolve_fields(index, tl_class):
                problems.append(
                    f"{path.name}: {tl_class}.{field} is not a field of that TL class")

    # Method calls made through the platform client classes.
    api: dict[str, dict[str, set[int]]] = {}
    for name, relative in {
        "auth": "auth/AuthManager.java",
        "bridge": "PlatformBridge.java",
        "realtime": "realtime/RealtimeClient.java",
        "ids": "mapper/IdMapper.java",
        "tokens": "auth/TokenStore.java",
    }.items():
        api[name] = method_declarations(CLIENT / relative)

    for path in sorted(CLIENT.rglob("*.java")):
        source = strip_comments(path.read_text(encoding="utf-8"))
        for receiver, methods in api.items():
            for call in re.finditer(rf"\b{receiver}\(\)\.(\w+)\(", source):
                method = call.group(1)
                if method not in methods:
                    problems.append(f"{path.name}: {receiver}().{method} is not declared")
                    continue
                argc, _ = _arguments(source, call.end())
                checked_calls += 1
                if argc not in methods[method]:
                    problems.append(
                        f"{path.name}: {receiver}().{method} called with {argc} args, "
                        f"declared arities {sorted(methods[method])}")

    print(f"TL classes indexed: {len(index)}")
    print(f"TL field assignments verified: {checked_fields}")
    print(f"platform method calls verified: {checked_calls}")
    if problems:
        print("\n== problems ==")
        for problem in sorted(set(problems)):
            print("  -", problem)
        return 1
    print("\nOK: every TL field and platform call the client uses exists with a matching signature")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
