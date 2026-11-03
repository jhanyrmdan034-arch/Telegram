import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';

const MIGRATION_VERSION = '001_initial';
const MIGRATION_PATH = fileURLToPath(new URL('../migrations/001_initial.sql', import.meta.url));
const SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60;
const E164_PATTERN = /^\+[1-9][0-9]{7,14}$/;

function nowSeconds() {
  return Math.floor(Date.now() / 1000);
}

function normalizePhone(phone) {
  if (typeof phone !== 'string') throw new TypeError('phone must be a string');
  const normalized = phone.replace(/[\s()-]/g, '');
  if (!E164_PATTERN.test(normalized)) throw new RangeError('phone must be a valid E.164-style number');
  return normalized;
}

function profileRow(db, userId) {
  return db.prepare(`
    SELECT u.id, u.phone_e164, u.created_at, u.updated_at, u.disabled_at,
           p.first_name, p.last_name, p.username, p.bio, p.updated_at AS profile_updated_at
    FROM users u JOIN user_profiles p ON p.user_id = u.id
    WHERE u.id = ?
  `).get(userId);
}

function publicUser(row, includePhone = false) {
  if (!row) return null;
  const user = {
    id: Number(row.id),
    firstName: row.first_name,
    lastName: row.last_name,
    username: row.username,
    bio: row.bio,
    createdAt: Number(row.created_at),
    updatedAt: Number(row.profile_updated_at ?? row.updated_at),
  };
  if (includePhone) user.phone = row.phone_e164;
  return user;
}

function messageRow(row) {
  if (!row) return null;
  return {
    id: Number(row.id),
    conversationId: Number(row.conversation_id),
    sender: {
      id: Number(row.sender_id),
      firstName: row.sender_first_name,
      lastName: row.sender_last_name,
      username: row.sender_username,
    },
    text: row.text,
    createdAt: Number(row.created_at),
    editedAt: row.edited_at === null ? null : Number(row.edited_at),
    deletedAt: row.deleted_at === null ? null : Number(row.deleted_at),
  };
}

export class MessengerDatabase {
  constructor(databasePath) {
    if (typeof databasePath !== 'string' || !databasePath) throw new TypeError('databasePath is required');
    this.databasePath = databasePath === ':memory:' ? databasePath : path.resolve(databasePath);
    if (this.databasePath !== ':memory:') mkdirSync(path.dirname(this.databasePath), { recursive: true, mode: 0o700 });
    this.db = new DatabaseSync(this.databasePath);
    this.db.exec('PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000; PRAGMA journal_mode = WAL;');
    this.applyMigrations();
  }

  applyMigrations() {
    this.db.exec('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)');
    const applied = this.db.prepare('SELECT 1 FROM schema_migrations WHERE version = ?').get(MIGRATION_VERSION);
    if (applied) return;

    const migration = readFileSync(MIGRATION_PATH, 'utf8');
    this.transaction(() => {
      this.db.exec(migration);
      this.db.prepare('INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)').run(MIGRATION_VERSION, nowSeconds());
    });
  }

  transaction(action) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const result = action();
      this.db.exec('COMMIT');
      return result;
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }

  ensureUser(phone, { firstName = '', lastName = '' } = {}) {
    const normalized = normalizePhone(phone);
    const existing = this.db.prepare('SELECT id FROM users WHERE phone_e164 = ?').get(normalized);
    if (existing) return publicUser(profileRow(this.db, Number(existing.id)), true);

    const now = nowSeconds();
    const displayFirstName = String(firstName || `User ${normalized.slice(-4)}`).trim().slice(0, 64);
    const id = this.transaction(() => {
      const user = this.db.prepare('INSERT INTO users(phone_e164, created_at, updated_at) VALUES (?, ?, ?)').run(normalized, now, now);
      const userId = Number(user.lastInsertRowid);
      this.db.prepare(`
        INSERT INTO user_profiles(user_id, first_name, last_name, updated_at)
        VALUES (?, ?, ?, ?)
      `).run(userId, displayFirstName, String(lastName).trim().slice(0, 64), now);
      return userId;
    });
    return publicUser(profileRow(this.db, id), true);
  }

  createOrLogin(phone) {
    const normalized = normalizePhone(phone);
    let row = this.db.prepare('SELECT id, disabled_at FROM users WHERE phone_e164 = ?').get(normalized);
    let created = false;
    if (!row) {
      const user = this.ensureUser(normalized);
      row = { id: user.id, disabled_at: null };
      created = true;
    }
    if (row.disabled_at !== null) {
      const error = new Error('This account is disabled');
      error.statusCode = 403;
      error.code = 'ACCOUNT_DISABLED';
      throw error;
    }

    const token = randomBytes(32).toString('base64url');
    const tokenHash = createHash('sha256').update(token).digest('hex');
    const createdAt = nowSeconds();
    const sessionId = randomUUID();
    this.db.prepare(`
      INSERT INTO user_sessions(id, user_id, token_hash, created_at, expires_at)
      VALUES (?, ?, ?, ?, ?)
    `).run(sessionId, Number(row.id), tokenHash, createdAt, createdAt + SESSION_LIFETIME_SECONDS);

    return {
      created,
      token,
      expiresAt: createdAt + SESSION_LIFETIME_SECONDS,
      user: publicUser(profileRow(this.db, Number(row.id)), true),
    };
  }

  authenticate(token) {
    if (typeof token !== 'string' || token.length < 32 || token.length > 256) return null;
    const tokenHash = createHash('sha256').update(token).digest('hex');
    const row = this.db.prepare(`
      SELECT s.user_id
      FROM user_sessions s JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ? AND u.disabled_at IS NULL
    `).get(tokenHash, nowSeconds());
    return row ? publicUser(profileRow(this.db, Number(row.user_id)), true) : null;
  }

  revokeSession(token) {
    if (typeof token !== 'string') return false;
    const tokenHash = createHash('sha256').update(token).digest('hex');
    const result = this.db.prepare(`UPDATE user_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL`)
      .run(nowSeconds(), tokenHash);
    return Number(result.changes) === 1;
  }

  getUser(userId, includePhone = false) {
    return publicUser(profileRow(this.db, userId), includePhone);
  }

  searchUsers(requesterId, query, limit = 20) {
    if (typeof query !== 'string') throw new TypeError('query must be a string');
    const value = query.trim().replace(/^@/, '').toLowerCase();
    if (value.length > 64) throw new RangeError('query must be at most 64 characters');
    if (!value) return [];

    const safeLimit = Math.max(1, Math.min(50, Number(limit) || 20));
    const escapeLike = (input) => input.replace(/[!%_]/g, (character) => `!${character}`);
    const pattern = `%${escapeLike(value)}%`;
    const phoneQuery = query.replace(/[^0-9+]/g, '');
    const rows = this.db.prepare(`
      SELECT u.id, u.phone_e164, u.created_at, u.updated_at, u.disabled_at,
             p.first_name, p.last_name, p.username, p.bio, p.updated_at AS profile_updated_at
      FROM users u JOIN user_profiles p ON p.user_id = u.id
      WHERE u.id <> ? AND u.disabled_at IS NULL AND (
        LOWER(p.first_name || ' ' || p.last_name) LIKE ? ESCAPE '!'
        OR LOWER(COALESCE(p.username, '')) LIKE ? ESCAPE '!'
        OR (? <> '' AND u.phone_e164 LIKE ?)
      )
      ORDER BY CASE
        WHEN LOWER(COALESCE(p.username, '')) = ? THEN 0
        WHEN LOWER(p.first_name || ' ' || p.last_name) = ? THEN 1
        ELSE 2
      END, LOWER(p.first_name), u.id
      LIMIT ?
    `).all(requesterId, pattern, pattern, phoneQuery, `%${phoneQuery}%`, value, value, safeLimit);
    return rows.map((row) => publicUser(row));
  }

  updateProfile(userId, patch) {
    const allowed = new Set(['firstName', 'lastName', 'username', 'bio']);
    for (const key of Object.keys(patch)) {
      if (!allowed.has(key)) throw new RangeError(`Unsupported profile field: ${key}`);
    }
    const current = profileRow(this.db, userId);
    if (!current) return null;

    const firstName = patch.firstName === undefined ? current.first_name : String(patch.firstName).trim();
    const lastName = patch.lastName === undefined ? current.last_name : String(patch.lastName).trim();
    const bio = patch.bio === undefined ? current.bio : String(patch.bio).trim();
    let username = patch.username === undefined ? current.username : patch.username;
    if (username !== null && username !== undefined) {
      username = String(username).trim().replace(/^@/, '').toLowerCase();
      if (username && !/^[a-z][a-z0-9_]{4,31}$/.test(username)) {
        throw new RangeError('username must be 5-32 characters using letters, digits or underscore');
      }
      if (!username) username = null;
    }
    if (firstName.length > 64 || lastName.length > 64 || bio.length > 500) {
      throw new RangeError('profile text exceeds its maximum length');
    }

    try {
      this.db.prepare(`
        UPDATE user_profiles SET first_name = ?, last_name = ?, username = ?, bio = ?, updated_at = ?
        WHERE user_id = ?
      `).run(firstName, lastName, username ?? null, bio, nowSeconds(), userId);
    } catch (error) {
      if (String(error.message).includes('UNIQUE constraint failed: user_profiles.username')) {
        const conflict = new Error('Username is already taken');
        conflict.statusCode = 409;
        conflict.code = 'USERNAME_TAKEN';
        throw conflict;
      }
      throw error;
    }
    return this.getUser(userId, true);
  }

  createDirectConversation(userId, peerUserId) {
    const ownerId = Number(userId);
    const otherId = Number(peerUserId);
    if (!Number.isSafeInteger(otherId) || otherId <= 0) throw new RangeError('peerUserId must be a positive integer');
    if (ownerId === otherId) throw new RangeError('A direct conversation requires another user');
    const peer = this.db.prepare('SELECT id FROM users WHERE id = ? AND disabled_at IS NULL').get(otherId);
    if (!peer) {
      const error = new Error('User not found');
      error.statusCode = 404;
      error.code = 'USER_NOT_FOUND';
      throw error;
    }

    const directKey = `${Math.min(ownerId, otherId)}:${Math.max(ownerId, otherId)}`;
    const now = nowSeconds();
    const conversationId = this.transaction(() => {
      this.db.prepare(`
        INSERT OR IGNORE INTO conversations(kind, direct_key, created_at)
        VALUES ('direct', ?, ?)
      `).run(directKey, now);
      const conversation = this.db.prepare('SELECT id FROM conversations WHERE direct_key = ?').get(directKey);
      const id = Number(conversation.id);
      this.db.prepare('INSERT OR IGNORE INTO conversation_members(conversation_id, user_id, joined_at) VALUES (?, ?, ?)')
        .run(id, ownerId, now);
      this.db.prepare('INSERT OR IGNORE INTO conversation_members(conversation_id, user_id, joined_at) VALUES (?, ?, ?)')
        .run(id, otherId, now);
      return id;
    });
    return { id: conversationId, kind: 'direct', peer: this.getUser(otherId), createdAt: now };
  }

  isConversationMember(conversationId, userId) {
    return Boolean(this.db.prepare(`
      SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?
    `).get(conversationId, userId));
  }

  getConversationMemberIds(conversationId) {
    return this.db.prepare(`
      SELECT user_id FROM conversation_members WHERE conversation_id = ? ORDER BY user_id
    `).all(conversationId).map((row) => Number(row.user_id));
  }

  listConversations(userId) {
    const rows = this.db.prepare(`
      SELECT c.id, c.created_at, c.last_message_id,
             peer.id AS peer_id, peer.phone_e164 AS peer_phone, peer.created_at AS peer_created_at,
             profile.first_name AS peer_first_name, profile.last_name AS peer_last_name,
             profile.username AS peer_username, profile.bio AS peer_bio, profile.updated_at AS peer_updated_at,
             last.text AS last_text, last.sender_id AS last_sender_id, last.created_at AS last_created_at,
             last.edited_at AS last_edited_at, last.deleted_at AS last_deleted_at,
             last_profile.first_name AS last_sender_first_name,
             last_profile.last_name AS last_sender_last_name,
             last_profile.username AS last_sender_username
      FROM conversations c
      JOIN conversation_members mine ON mine.conversation_id = c.id AND mine.user_id = ?
      JOIN conversation_members other ON other.conversation_id = c.id AND other.user_id <> ?
      JOIN users peer ON peer.id = other.user_id
      JOIN user_profiles profile ON profile.user_id = peer.id
      LEFT JOIN messages last ON last.id = c.last_message_id
      LEFT JOIN user_profiles last_profile ON last_profile.user_id = last.sender_id
      ORDER BY COALESCE(last.created_at, c.created_at) DESC, c.id DESC
    `).all(userId, userId);
    return rows.map((row) => ({
      id: Number(row.id),
      kind: 'direct',
      createdAt: Number(row.created_at),
      peer: publicUser({
        id: row.peer_id,
        phone_e164: row.peer_phone,
        created_at: row.peer_created_at,
        first_name: row.peer_first_name,
        last_name: row.peer_last_name,
        username: row.peer_username,
        bio: row.peer_bio,
        profile_updated_at: row.peer_updated_at,
      }),
      lastMessage: row.last_message_id === null ? null : messageRow({
        id: row.last_message_id,
        conversation_id: row.id,
        sender_id: row.last_sender_id,
        sender_first_name: row.last_sender_first_name,
        sender_last_name: row.last_sender_last_name,
        sender_username: row.last_sender_username,
        text: row.last_text,
        created_at: row.last_created_at,
        edited_at: row.last_edited_at,
        deleted_at: row.last_deleted_at,
      }),
    }));
  }

  sendTextMessage(conversationId, senderId, text) {
    if (typeof text !== 'string') throw new TypeError('text must be a string');
    const value = text.trim();
    if (!value || value.length > 4096) throw new RangeError('message text must be 1-4096 characters');
    if (!this.isConversationMember(conversationId, senderId)) {
      const error = new Error('Conversation not found');
      error.statusCode = 404;
      error.code = 'CONVERSATION_NOT_FOUND';
      throw error;
    }

    const now = nowSeconds();
    const messageId = this.transaction(() => {
      const insert = this.db.prepare(`
        INSERT INTO messages(conversation_id, sender_id, text, created_at)
        VALUES (?, ?, ?, ?)
      `).run(conversationId, senderId, value, now);
      const id = Number(insert.lastInsertRowid);
      this.db.prepare('UPDATE conversations SET last_message_id = ? WHERE id = ?').run(id, conversationId);
      this.db.prepare(`
        INSERT INTO message_receipts(message_id, user_id)
        SELECT ?, user_id FROM conversation_members WHERE conversation_id = ? AND user_id <> ?
      `).run(id, conversationId, senderId);
      return id;
    });
    return this.getMessage(conversationId, messageId, senderId);
  }

  getMessage(conversationId, messageId, requesterId) {
    if (!this.isConversationMember(conversationId, requesterId)) return null;
    const row = this.db.prepare(`
      SELECT m.id, m.conversation_id, m.sender_id, m.text, m.created_at, m.edited_at, m.deleted_at,
             p.first_name AS sender_first_name, p.last_name AS sender_last_name, p.username AS sender_username
      FROM messages m JOIN user_profiles p ON p.user_id = m.sender_id
      WHERE m.conversation_id = ? AND m.id = ? AND m.deleted_at IS NULL
    `).get(conversationId, messageId);
    return messageRow(row);
  }

  getMessages(conversationId, requesterId, { beforeId = null, limit = 50 } = {}) {
    if (!this.isConversationMember(conversationId, requesterId)) {
      const error = new Error('Conversation not found');
      error.statusCode = 404;
      error.code = 'CONVERSATION_NOT_FOUND';
      throw error;
    }
    const safeLimit = Math.max(1, Math.min(100, Number(limit) || 50));
    const before = beforeId === null ? null : Number(beforeId);
    const baseQuery = `
      SELECT m.id, m.conversation_id, m.sender_id, m.text, m.created_at, m.edited_at, m.deleted_at,
             p.first_name AS sender_first_name, p.last_name AS sender_last_name, p.username AS sender_username
      FROM messages m JOIN user_profiles p ON p.user_id = m.sender_id
      WHERE m.conversation_id = ? AND m.deleted_at IS NULL
    `;
    const rows = before === null
      ? this.db.prepare(`${baseQuery} ORDER BY m.id DESC LIMIT ?`).all(conversationId, safeLimit)
      : this.db.prepare(`${baseQuery} AND m.id < ? ORDER BY m.id DESC LIMIT ?`).all(conversationId, before, safeLimit);
    return rows.map(messageRow).reverse();
  }

  getMessagesForUserAfter(userId, afterId = 0, limit = 1000) {
    const safeAfterId = Math.max(0, Number(afterId) || 0);
    const safeLimit = Math.max(1, Math.min(1000, Number(limit) || 1000));
    const rows = this.db.prepare(`
      SELECT m.id, m.conversation_id, m.sender_id, m.text, m.created_at, m.edited_at, m.deleted_at,
             p.first_name AS sender_first_name, p.last_name AS sender_last_name, p.username AS sender_username
      FROM messages m
      JOIN conversation_members cm ON cm.conversation_id = m.conversation_id AND cm.user_id = ?
      JOIN user_profiles p ON p.user_id = m.sender_id
      WHERE m.id > ? AND m.deleted_at IS NULL
      ORDER BY m.id ASC
      LIMIT ?
    `).all(userId, safeAfterId, safeLimit);
    return rows.map(messageRow);
  }

  health() {
    const row = this.db.prepare('PRAGMA quick_check').get();
    return row?.quick_check === 'ok';
  }

  close() {
    this.db.close();
  }
}

export { normalizePhone, SESSION_LIFETIME_SECONDS };
