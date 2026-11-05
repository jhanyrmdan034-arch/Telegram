#!/usr/bin/env bash
# adb-install-diagnose.sh — extracts the REAL reason an APK refuses to install.
#
# Why: the Android package installer only shows "App not installed"; the actual
# PackageManager error code (INSTALL_FAILED_*) stays in logcat / pm output.
# This script collects that code plus everything needed to interpret it.
#
# Usage (PC with adb, phone connected over USB with USB debugging enabled):
#   ./tools/adb-install-diagnose.sh Telegram-13.1.1-armv7.apk
#
# Output: prints a summary and writes install-diagnose-report.txt next to the APK.

set -u
APK="${1:-}"
REPORT="install-diagnose-report.txt"

ok()   { printf '  [ok]   %s\n' "$*"; }
info() { printf '  [info] %s\n' "$*"; }
warn() { printf '  [warn] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; }

say() { printf '%s\n' "$*" | tee -a "$REPORT"; }

if [ -z "$APK" ] || [ ! -f "$APK" ]; then
  echo "usage: $0 <path-to.apk>" >&2
  exit 2
fi
command -v adb >/dev/null 2>&1 || { echo "adb not found: install Android platform-tools" >&2; exit 2; }

: > "$REPORT"
say "=== adb install diagnosis ==="
say "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
say "apk : $APK"
say "size: $(wc -c < "$APK") bytes"
if command -v sha256sum >/dev/null 2>&1; then
  say "sha256: $(sha256sum "$APK" | awk '{print $1}')"
else
  say "sha256: (sha256sum unavailable)"
fi

DEVICES=$(adb devices | awk 'NR>1 && $2=="device" {print $1}')
COUNT=$(printf '%s\n' "$DEVICES" | grep -c . || true)
if [ "$COUNT" -eq 0 ]; then
  warn "no device in 'device' state. Check: USB debugging on, RSA prompt accepted, 'adb devices'."
  exit 3
fi
if [ "$COUNT" -gt 1 ]; then
  warn "$COUNT devices attached; using the first. Set ANDROID_SERIAL to pick another."
fi

say ""
say "--- device ---"
for prop in ro.product.manufacturer ro.product.model ro.product.cpu.abi \
            ro.product.cpu.abilist ro.build.version.release ro.build.version.sdk \
            ro.build.version.security_patch; do
  say "$prop=$(adb shell getprop "$prop" | tr -d '\r')"
done
say "free bytes on /data: $(adb shell df -k /data 2>/dev/null | awk 'NR==2 {print $4*1024}' | tr -d '\r')"

ABILIST=$(adb shell getprop ro.product.cpu.abilist | tr -d '\r')
say ""
say "--- 32-bit ARM support on this device ---"
case "$ABILIST" in
  *armeabi-v7a*) ok "abilist contains armeabi-v7a -> the 32-bit APK can run here" ;;
  *)             fail "abilist=$ABILIST has NO armeabi-v7a -> a 32-bit-only APK can never install on this phone" ;;
esac

say ""
say "--- packages that could block the install ---"
FORK=$(adb shell pm list packages 2>/dev/null | tr -d '\r' | grep -iE "telegram|fork" || true)
if [ -z "$FORK" ]; then
  ok "no telegram/fork package installed"
else
  printf '%s\n' "$FORK" | sed 's/^package://' | while read -r p; do
    say "installed: $p"
    adb shell dumpsys package "$p" 2>/dev/null | tr -d '\r' \
      | grep -E "versionCode=|versionName=|firstInstallTime=|lastUpdateTime=|signatures=|installerPackageName=" \
      | head -8 | sed 's/^/    /' | tee -a "$REPORT" >/dev/null
    adb shell dumpsys package "$p" 2>/dev/null | tr -d '\r' \
      | grep -E "versionCode=|firstInstallTime=|signatures=" | head -4 | sed 's/^/    /'
  done
  warn "if a different build of the same package is installed with another signing key,"
  warn "the install fails with INSTALL_FAILED_UPDATE_INCOMPATIBLE. Remove it first:"
  warn "  adb uninstall org.telegram.messenger.fork"
fi

say ""
say "--- adb install ---"
adb logcat -c 2>/dev/null || true
INSTALL_OUT=$(adb install -r -d "$APK" 2>&1)
INSTALL_RC=$?
printf '%s\n' "$INSTALL_OUT" | sed 's/^/  /'
say "exit_code=$INSTALL_RC"
say "output=$INSTALL_OUT"

if [ "$INSTALL_RC" -ne 0 ]; then
  say ""
  say "--- session install (gives the raw PackageManager string) ---"
  SIZE=$(wc -c < "$APK")
  CREATE=$(adb shell pm install-create -r -t -S "$SIZE" 2>&1 | tr -d '\r')
  say "install-create: $CREATE"
  SID=$(printf '%s' "$CREATE" | grep -oE '\[[0-9]+\]' | tr -d '[]')
  if [ -n "$SID" ]; then
    adb push "$APK" /data/local/tmp/diagnose.apk >/dev/null 2>&1
    WRITE=$(adb shell pm install-write -S "$SIZE" "$SID" diagnose.apk /data/local/tmp/diagnose.apk 2>&1 | tr -d '\r')
    say "install-write : $WRITE"
    COMMIT=$(adb shell pm install-commit "$SID" 2>&1 | tr -d '\r')
    say "install-commit: $COMMIT"
    adb shell rm -f /data/local/tmp/diagnose.apk >/dev/null 2>&1
  else
    warn "no session id returned; continuing with logcat only"
  fi
fi

say ""
say "--- PackageManager logcat ---"
LOGS=$(adb logcat -d 2>/dev/null | grep -iE "PackageManager|PackageInstaller|installd|PackageParser" | tail -40)
printf '%s\n' "$LOGS" | sed 's/^/  /'
say "logcat_lines=$(printf '%s\n' "$LOGS" | grep -c . || true)"

say ""
say "--- verdict ---"
ALL=$(printf '%s\n%s\n' "$INSTALL_OUT" "$LOGS")
MATCHED=0
check() {
  if printf '%s' "$ALL" | grep -qiE "$2"; then
    fail "$1"
    say "VERDICT: $1"
    MATCHED=1
  fi
}
check "INSTALL_FAILED_UPDATE_INCOMPATIBLE — another build of this package, different signing key: uninstall it first" "UPDATE_INCOMPATIBLE"
check "INSTALL_FAILED_NO_MATCHING_ABIS — this phone cannot run 32-bit ARM code (use an arm64 build)" "NO_MATCHING_ABIS"
check "INSTALL_PARSE_FAILED_NO_CERTIFICATES — APK carries no valid signature" "NO_CERTIFICATES|not signed"
check "INSTALL_PARSE_FAILED_NOT_APK — the file is not a valid APK (truncated / wrong file / still a .zip)" "NOT_APK|Invalid file|does not contain AndroidManifest"
check "INSTALL_FAILED_INVALID_APK — the archive is malformed (often a truncated download)" "INVALID_APK"
check "INSTALL_FAILED_INSUFFICIENT_STORAGE — free space on the phone" "INSUFFICIENT_STORAGE"
check "INSTALL_FAILED_TEST_ONLY — the build is marked testOnly" "TEST_ONLY"
check "INSTALL_FAILED_VERIFICATION_FAILURE — Play Protect / package verifier blocked it" "VERIFICATION_FAILURE"
check "INSTALL_FAILED_VERSION_DOWNGRADE — a newer version of the package is installed" "VERSION_DOWNGRADE"
if [ "$MATCHED" -eq 0 ]; then
  if [ "$INSTALL_RC" -eq 0 ]; then
    ok "install succeeded (no failure code found)"
    say "VERDICT: install succeeded"
  else
    warn "no known INSTALL_FAILED_* token found — send this report; the raw lines above carry the answer"
    say "VERDICT: unknown (raw output above)"
  fi
fi

say ""
say "report written to: $REPORT"
