#!/bin/sh
# Restore a database dump (and optionally uploaded files) produced by backup.sh.
#
#   ./restore.sh /backups/db-20260101T030000Z.dump [/backups/files-....tar.gz]
#
# DESTRUCTIVE: the target database must be empty or explicitly allowed to be replaced.
set -eu

DUMP=${1:?usage: restore.sh <db-dump> [files-archive]}
FILES_ARCHIVE=${2:-}
BACKUP_ENCRYPTION_KEY=${BACKUP_ENCRYPTION_KEY:-}

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

prepare() {
  artefact=$1
  case "$artefact" in
    *.enc)
      [ -n "$BACKUP_ENCRYPTION_KEY" ] || { echo "encrypted artefact needs BACKUP_ENCRYPTION_KEY"; exit 1; }
      out="$workdir/$(basename "${artefact%.enc}")"
      openssl enc -d -aes-256-cbc -pbkdf2 -pass "pass:$BACKUP_ENCRYPTION_KEY" -in "$artefact" -out "$out"
      echo "$out"
      ;;
    *) echo "$artefact" ;;
  esac
}

DB_DUMP=$(prepare "$DUMP")
echo "[restore] restoring database from $DB_DUMP"
pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction -d "$PGDATABASE" "$DB_DUMP"

if [ -n "$FILES_ARCHIVE" ]; then
  FILES_TAR=$(prepare "$FILES_ARCHIVE")
  target=${STORAGE_DIR:-/var/lib/messenger}
  echo "[restore] restoring files into $target"
  tar -C "$target" -xzf "$FILES_TAR"
fi

echo "[restore] done — restart the api service so caches are rebuilt"
