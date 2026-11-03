import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { MessengerDatabase } from '../src/database.mjs';

const directory = path.dirname(fileURLToPath(import.meta.url));
const databasePath = process.env.MESSENGER_DB_PATH || path.resolve(directory, '../.data/messenger.sqlite');
const db = new MessengerDatabase(databasePath);

try {
  const alice = db.ensureUser('+999000000001', { firstName: 'Demo Alice' });
  const bob = db.ensureUser('+999000000002', { firstName: 'Demo Bob' });
  const conversation = db.createDirectConversation(alice.id, bob.id);
  const prior = db.getMessages(conversation.id, alice.id, { limit: 1 });
  if (prior.length === 0) {
    db.sendTextMessage(conversation.id, alice.id, 'پیام آزمایشی اولیه بین دو حساب');
  }
  process.stdout.write(`Demo database ready: ${databasePath}\n`);
  process.stdout.write(`User IDs: ${alice.id}, ${bob.id}; direct conversation ID: ${conversation.id}\n`);
  process.stdout.write('No session token or password was created or printed. These phone numbers are synthetic test fixtures.\n');
} finally {
  db.close();
}
