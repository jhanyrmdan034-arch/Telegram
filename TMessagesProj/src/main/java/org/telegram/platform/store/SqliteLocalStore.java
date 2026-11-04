package org.telegram.platform.store;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.util.ArrayList;
import java.util.List;

/**
 * SQLite implementation of the offline cache. Mirrors what MessagesStorage does
 * for the MTProto client, but stores the JSON documents returned by our API, so a
 * schema change on the server never requires a migration on the device.
 */
public final class SqliteLocalStore extends SQLiteOpenHelper implements LocalStore {

    private static final String DATABASE = "platform_cache.db";
    private static final int VERSION = 1;

    public SqliteLocalStore(Context context) {
        super(context.getApplicationContext(), DATABASE, null, VERSION);
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE chats (id TEXT PRIMARY KEY, position INTEGER, json TEXT NOT NULL)");
        db.execSQL("CREATE TABLE messages (chat_id TEXT NOT NULL, message_id TEXT NOT NULL, "
                + "seq INTEGER NOT NULL, json TEXT NOT NULL, PRIMARY KEY (chat_id, message_id))");
        db.execSQL("CREATE INDEX idx_messages_seq ON messages (chat_id, seq DESC)");
        db.execSQL("CREATE TABLE outbox (client_message_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, "
                + "payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)");
        db.execSQL("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)");
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        // Cache only: dropping and letting the next sync repopulate is safe.
        db.execSQL("DROP TABLE IF EXISTS chats");
        db.execSQL("DROP TABLE IF EXISTS messages");
        db.execSQL("DROP TABLE IF EXISTS outbox");
        db.execSQL("DROP TABLE IF EXISTS state");
        onCreate(db);
    }

    @Override
    public void saveChats(List<String> chatJson) {
        SQLiteDatabase db = getWritableDatabase();
        db.beginTransaction();
        try {
            db.delete("chats", null, null);
            int position = 0;
            for (String json : chatJson) {
                ContentValues values = new ContentValues();
                values.put("id", JsonFields.string(json, "id"));
                values.put("position", position++);
                values.put("json", json);
                db.insertWithOnConflict("chats", null, values, SQLiteDatabase.CONFLICT_REPLACE);
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    @Override
    public List<String> loadChats() {
        List<String> result = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query("chats", new String[]{"json"},
                null, null, null, null, "position ASC")) {
            while (cursor.moveToNext()) {
                result.add(cursor.getString(0));
            }
        }
        return result;
    }

    @Override
    public void saveChat(String chatJson) {
        ContentValues values = new ContentValues();
        values.put("id", JsonFields.string(chatJson, "id"));
        values.put("json", chatJson);
        getWritableDatabase().insertWithOnConflict("chats", null, values,
                SQLiteDatabase.CONFLICT_REPLACE);
    }

    @Override
    public String loadChat(String chatId) {
        try (Cursor cursor = getReadableDatabase().query("chats", new String[]{"json"},
                "id = ?", new String[]{chatId}, null, null, null)) {
            return cursor.moveToFirst() ? cursor.getString(0) : null;
        }
    }

    @Override
    public void saveMessages(String chatId, List<String> messageJson) {
        SQLiteDatabase db = getWritableDatabase();
        db.beginTransaction();
        try {
            for (String json : messageJson) {
                ContentValues values = new ContentValues();
                values.put("chat_id", chatId);
                values.put("message_id", JsonFields.string(json, "id"));
                values.put("seq", JsonFields.number(json, "seq"));
                values.put("json", json);
                db.insertWithOnConflict("messages", null, values, SQLiteDatabase.CONFLICT_REPLACE);
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    @Override
    public List<String> loadMessages(String chatId, int limit) {
        List<String> result = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query("messages", new String[]{"json"},
                "chat_id = ?", new String[]{chatId}, null, null, "seq DESC", String.valueOf(limit))) {
            while (cursor.moveToNext()) {
                result.add(cursor.getString(0));
            }
        }
        return result;
    }

    @Override
    public void markMessageDeleted(String chatId, String messageId) {
        getWritableDatabase().delete("messages", "chat_id = ? AND message_id = ?",
                new String[]{chatId, messageId});
    }

    @Override
    public void enqueueOutbound(String clientMessageId, String chatId, String payloadJson) {
        ContentValues values = new ContentValues();
        values.put("client_message_id", clientMessageId);
        values.put("chat_id", chatId);
        values.put("payload", payloadJson);
        getWritableDatabase().insertWithOnConflict("outbox", null, values,
                SQLiteDatabase.CONFLICT_REPLACE);
    }

    @Override
    public List<Outbound> pendingOutbound() {
        List<Outbound> result = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query("outbox",
                new String[]{"client_message_id", "chat_id", "payload", "attempts", "last_error"},
                null, null, null, null, "rowid ASC")) {
            while (cursor.moveToNext()) {
                result.add(new Outbound(cursor.getString(0), cursor.getString(1), cursor.getString(2),
                        cursor.getInt(3), cursor.getString(4)));
            }
        }
        return result;
    }

    @Override
    public void removeOutbound(String clientMessageId) {
        getWritableDatabase().delete("outbox", "client_message_id = ?",
                new String[]{clientMessageId});
    }

    @Override
    public void bumpOutboundAttempts(String clientMessageId, String lastError) {
        getWritableDatabase().execSQL(
                "UPDATE outbox SET attempts = attempts + 1, last_error = ? "
                        + "WHERE client_message_id = ?",
                new Object[]{lastError, clientMessageId});
    }

    @Override
    public void putState(String key, String value) {
        ContentValues values = new ContentValues();
        values.put("key", key);
        values.put("value", value);
        getWritableDatabase().insertWithOnConflict("state", null, values,
                SQLiteDatabase.CONFLICT_REPLACE);
    }

    @Override
    public String getState(String key) {
        try (Cursor cursor = getReadableDatabase().query("state", new String[]{"value"},
                "key = ?", new String[]{key}, null, null, null)) {
            return cursor.moveToFirst() ? cursor.getString(0) : null;
        }
    }

    /** Tiny helpers avoiding a JSON dependency in the storage layer. */
    static final class JsonFields {
        static String string(String json, String field) {
            String needle = "\"" + field + "\":\"";
            int index = json.indexOf(needle);
            if (index < 0) {
                return null;
            }
            int start = index + needle.length();
            int end = json.indexOf('"', start);
            return end < 0 ? null : json.substring(start, end);
        }

        static long number(String json, String field) {
            String needle = "\"" + field + "\":";
            int index = json.indexOf(needle);
            if (index < 0) {
                return 0L;
            }
            int start = index + needle.length();
            int end = start;
            while (end < json.length() && (Character.isDigit(json.charAt(end))
                    || json.charAt(end) == '-')) {
                end++;
            }
            try {
                return Long.parseLong(json.substring(start, end));
            } catch (NumberFormatException e) {
                return 0L;
            }
        }
    }
}
