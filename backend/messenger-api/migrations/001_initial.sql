PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  phone_e164    TEXT NOT NULL UNIQUE,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL,
  disabled_at   INTEGER
);

CREATE TABLE IF NOT EXISTS user_profiles (
  user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  first_name    TEXT NOT NULL DEFAULT '',
  last_name     TEXT NOT NULL DEFAULT '',
  username      TEXT UNIQUE,
  bio           TEXT NOT NULL DEFAULT '',
  updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_sessions (
  id            TEXT PRIMARY KEY,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash    TEXT NOT NULL UNIQUE,
  created_at    INTEGER NOT NULL,
  expires_at    INTEGER NOT NULL,
  revoked_at    INTEGER
);

CREATE INDEX IF NOT EXISTS user_sessions_user_expiry_idx
  ON user_sessions(user_id, expires_at);

CREATE TABLE IF NOT EXISTS conversations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  kind          TEXT NOT NULL CHECK (kind IN ('direct')),
  direct_key    TEXT NOT NULL UNIQUE,
  created_at    INTEGER NOT NULL,
  last_message_id INTEGER
);

CREATE TABLE IF NOT EXISTS conversation_members (
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  joined_at       INTEGER NOT NULL,
  PRIMARY KEY (conversation_id, user_id)
);

CREATE INDEX IF NOT EXISTS conversation_members_user_idx
  ON conversation_members(user_id, conversation_id);

CREATE TABLE IF NOT EXISTS messages (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  sender_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  text            TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 4096),
  created_at      INTEGER NOT NULL,
  edited_at       INTEGER,
  deleted_at      INTEGER
);

CREATE INDEX IF NOT EXISTS messages_conversation_order_idx
  ON messages(conversation_id, id DESC);

CREATE TABLE IF NOT EXISTS message_receipts (
  message_id      INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  delivered_at    INTEGER,
  read_at         INTEGER,
  PRIMARY KEY (message_id, user_id)
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version         TEXT PRIMARY KEY,
  applied_at      INTEGER NOT NULL
);
