"""SQLite storage for the custom backend: users, login codes, chats, messages."""
import json
import os
import sqlite3
import threading
import time


class Database:
    def __init__(self, path: str):
        self.path = path
        if os.path.exists(path):
            os.remove(path) if path == ":memory:" else None
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        with self.lock:
            c = self.conn.cursor()
            c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT UNIQUE NOT NULL,
                access_hash INTEGER NOT NULL,
                first_name TEXT DEFAULT '',
                username TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_codes (
                phone TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                code TEXT NOT NULL,
                expires INTEGER NOT NULL,
                PRIMARY KEY (phone, code_hash)
            );
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY,
                title TEXT DEFAULT '',
                members TEXT DEFAULT '[]',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dialog TEXT NOT NULL,
                msg_id INTEGER NOT NULL,
                from_id INTEGER NOT NULL,
                date INTEGER NOT NULL,
                text TEXT DEFAULT '',
                random_id INTEGER DEFAULT 0,
                media TEXT DEFAULT '',
                UNIQUE (dialog, msg_id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_dialog ON messages(dialog, msg_id);
            """)
            self.conn.commit()

    # ------------------------------------------------------------- users ----
    def get_or_create_user(self, phone: str) -> dict:
        with self.lock:
            row = self.conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
            if row:
                return dict(row)
            access_hash = int.from_bytes(os.urandom(8), "big", signed=True)
            cur = self.conn.execute(
                "INSERT INTO users (phone, access_hash, first_name, created_at) VALUES (?,?,?,?)",
                (phone, access_hash, "User " + phone, int(time.time())))
            self.conn.commit()
            return dict(self.conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())

    def get_user(self, uid: int):
        with self.lock:
            row = self.conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            return dict(row) if row else None

    def get_user_by_phone(self, phone: str):
        with self.lock:
            row = self.conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
            return dict(row) if row else None

    # -------------------------------------------------------- login codes ----
    def save_code(self, phone: str, code_hash: str, code: str, ttl: int = 300):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO login_codes (phone, code_hash, code, expires) VALUES (?,?,?,?)",
                (phone, code_hash, code, int(time.time()) + ttl))
            self.conn.commit()

    def check_code(self, phone: str, code_hash: str, code: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT code, expires FROM login_codes WHERE phone = ? AND code_hash = ?",
                (phone, code_hash)).fetchone()
            return bool(row and row["code"] == code and row["expires"] > time.time())

    # ------------------------------------------------------------- chats ----
    def create_chat(self, chat_id: int, title: str, members) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO chats (id, title, members, created_at) VALUES (?,?,?,?)",
                (chat_id, title, json.dumps(list(members)), int(time.time())))
            self.conn.commit()

    def chat_members(self, chat_id: int):
        with self.lock:
            row = self.conn.execute("SELECT members FROM chats WHERE id = ?", (chat_id,)).fetchone()
            return json.loads(row["members"]) if row else []

    # ---------------------------------------------------------- messages ----
    def next_msg_id(self, dialog: str) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT MAX(msg_id) AS m FROM messages WHERE dialog = ?", (dialog,)).fetchone()
            return (row["m"] or 0) + 1

    def add_message(self, dialog, msg_id, from_id, date, text, random_id=0, media=""):
        with self.lock:
            self.conn.execute(
                "INSERT INTO messages (dialog, msg_id, from_id, date, text, random_id, media) VALUES (?,?,?,?,?,?,?)",
                (dialog, msg_id, from_id, date, text, random_id, media))
            self.conn.commit()

    def history(self, dialog: str, limit: int = 20):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE dialog = ? ORDER BY msg_id DESC LIMIT ?",
                (dialog, limit)).fetchall()
            return [dict(r) for r in rows]

    def dialogs_for(self, uid: int):
        """All dialogs of a user with their last message id."""
        with self.lock:
            rows = self.conn.execute("""
                SELECT dialog, MAX(msg_id) AS top, COUNT(*) AS cnt FROM messages
                WHERE dialog LIKE '%' || ? || '%' OR dialog LIKE 'd' || ? || '_%' OR dialog LIKE '%_' || ?
                GROUP BY dialog ORDER BY top DESC
            """, (str(uid), str(uid), str(uid))).fetchall()
            return [dict(r) for r in rows]
