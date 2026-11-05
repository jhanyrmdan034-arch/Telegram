#!/bin/sh
# Nightly backup: PostgreSQL dump + uploaded files, optional encryption, retention.
# Runs inside the `backup` container; can also be executed on a host with pg_dump.
set -eu

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_DIR=${BACKUP_DIR:-/backups}
RETENTION=${BACKUP_RETENTION_DAYS:-14}
STORAGE_DIR=${STORAGE_DIR:-/var/lib/messenger/files}

mkdir -p "$BACKUP_DIR"

DUMP="$BACKUP_DIR/db-$STAMP.dump"
echo "[backup] dumping database to $DUMP"
pg_dump --format=custom --compress=9 --no-owner --no-privileges -f "$DUMP"

if [ -d "$STORAGE_DIR" ]; then
  echo "[backup] archiving uploaded files"
  tar -C "$(dirname "$STORAGE_DIR")" -czf "$BACKUP_DIR/files-$STAMP.tar.gz" "$(basename "$STORAGE_DIR")"
fi

if [ -n "${BACKUP_ENCRYPTION_KEY:-}" ]; then
  echo "[backup] encrypting artefacts"
  for artefact in "$DUMP" "$BACKUP_DIR/files-$STAMP.tar.gz"; do
    [ -f "$artefact" ] || continue
    openssl enc -aes-256-cbc -pbkdf2 -salt -pass "pass:$BACKUP_ENCRYPTION_KEY" \
      -in "$artefact" -out "$artefact.enc"
    rm -f "$artefact"
  done
fi

echo "[backup] pruning artefacts older than $RETENTION days"
find "$BACKUP_DIR" -type f -mtime "+$RETENTION" -delete

echo "[backup] complete: $(ls -1 "$BACKUP_DIR" | tail -5 | tr '\n' ' ')"
