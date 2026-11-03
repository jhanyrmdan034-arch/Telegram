import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync, statSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { MessengerApiServer } from '../src/server.mjs';

async function requestJson(baseUrl, route, { token, method = 'GET', body } = {}) {
  const headers = {};
  if (token) headers.authorization = `Bearer ${token}`;
  if (body !== undefined) headers['content-type'] = 'application/json';
  const response = await fetch(`${baseUrl}${route}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  return { status: response.status, data };
}

async function readSseMessage(reader) {
  const decoder = new TextDecoder();
  let buffered = '';
  while (!buffered.includes('event: message\n')) {
    const { done, value } = await reader.read();
    if (done) throw new Error('SSE stream closed before a message event');
    buffered += decoder.decode(value, { stream: true });
    if (buffered.length > 64 * 1024) throw new Error('SSE message event exceeded test limit');
  }
  return buffered;
}

test('two phone-only test accounts exchange text, update profiles, receive SSE and survive DB restart', async (t) => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'messenger-api-test-'));
  const databasePath = path.join(directory, 'messenger.sqlite');
  let api = new MessengerApiServer({ databasePath, host: '127.0.0.1', port: 0 });
  let { port } = await api.listen();
  let baseUrl = `http://127.0.0.1:${port}`;
  t.after(async () => {
    if (api) await api.close();
    rmSync(directory, { recursive: true, force: true });
  });

  const health = await requestJson(baseUrl, '/health');
  assert.equal(health.status, 200);
  assert.deepEqual(health.data, { status: 'ok', database: 'sqlite' });

  const aliceLogin = await requestJson(baseUrl, '/api/v1/auth/phone', {
    method: 'POST',
    body: { phone: '+999000000001' },
  });
  assert.equal(aliceLogin.status, 200);
  assert.equal(aliceLogin.data.created, true);
  assert.equal(aliceLogin.data.authMode, 'phone-only-test-no-otp');
  assert.equal(aliceLogin.data.tokenType, 'Bearer');
  assert.ok(aliceLogin.data.accessToken.length >= 40);

  const bobLogin = await requestJson(baseUrl, '/api/v1/auth/phone', {
    method: 'POST',
    body: { phone: '+999000000002' },
  });
  assert.equal(bobLogin.status, 200);
  assert.equal(bobLogin.data.created, true);
  const aliceToken = aliceLogin.data.accessToken;
  const bobToken = bobLogin.data.accessToken;
  const aliceId = aliceLogin.data.user.id;
  const bobId = bobLogin.data.user.id;

  const existingLogin = await requestJson(baseUrl, '/api/v1/auth/phone', {
    method: 'POST',
    body: { phone: '+999000000001' },
  });
  assert.equal(existingLogin.status, 200);
  assert.equal(existingLogin.data.created, false);
  assert.equal(existingLogin.data.user.id, aliceId);
  assert.notEqual(existingLogin.data.accessToken, aliceToken);

  const profileUpdate = await requestJson(baseUrl, '/api/v1/me', {
    method: 'PATCH',
    token: aliceToken,
    body: { firstName: 'Alice', lastName: 'Test', username: 'alice_demo', bio: 'آزمایشی' },
  });
  assert.equal(profileUpdate.status, 200);
  assert.equal(profileUpdate.data.user.firstName, 'Alice');
  assert.equal(profileUpdate.data.user.username, 'alice_demo');
  assert.equal(profileUpdate.data.user.phone, '+999000000001');

  const publicProfile = await requestJson(baseUrl, `/api/v1/users/${aliceId}`, { token: bobToken });
  assert.equal(publicProfile.status, 200);
  assert.equal(publicProfile.data.user.firstName, 'Alice');
  assert.equal('phone' in publicProfile.data.user, false, 'public profiles do not expose phone numbers');

  const searchByUsername = await requestJson(baseUrl, '/api/v1/users/search?q=alice_demo', { token: bobToken });
  assert.equal(searchByUsername.status, 200);
  assert.equal(searchByUsername.data.users.length, 1);
  assert.equal(searchByUsername.data.users[0].id, aliceId);
  assert.equal('phone' in searchByUsername.data.users[0], false, 'user search does not expose phone numbers');
  const searchDoesNotReturnSelf = await requestJson(baseUrl, '/api/v1/users/search?q=999000000002', { token: bobToken });
  assert.equal(searchDoesNotReturnSelf.status, 200);
  assert.equal(searchDoesNotReturnSelf.data.users.length, 0);

  // The recipient listens before this conversation exists. The
  // account-level stream must announce the first message in a new conversation.
  const accountEventAbort = new AbortController();
  const accountEventResponse = await fetch(`${baseUrl}/api/v1/events`, {
    headers: { authorization: `Bearer ${bobToken}` },
    signal: accountEventAbort.signal,
  });
  assert.equal(accountEventResponse.status, 200);
  const accountEventReader = accountEventResponse.body.getReader();
  const accountConnected = await accountEventReader.read();
  assert.match(new TextDecoder().decode(accountConnected.value), /: connected/);
  const accountIncomingEvent = readSseMessage(accountEventReader);

  const createdConversation = await requestJson(baseUrl, '/api/v1/conversations/direct', {
    method: 'POST',
    token: aliceToken,
    body: { peerUserId: bobId },
  });
  assert.equal(createdConversation.status, 200);
  const conversationId = createdConversation.data.conversation.id;
  assert.equal(createdConversation.data.conversation.peer.id, bobId);

  const eventAbort = new AbortController();
  const eventResponse = await fetch(`${baseUrl}/api/v1/conversations/${conversationId}/events`, {
    headers: { authorization: `Bearer ${bobToken}` },
    signal: eventAbort.signal,
  });
  assert.equal(eventResponse.status, 200);
  assert.match(eventResponse.headers.get('content-type'), /^text\/event-stream/);
  const eventReader = eventResponse.body.getReader();
  const connected = await eventReader.read();
  assert.match(new TextDecoder().decode(connected.value), /: connected/);
  const incomingEvent = readSseMessage(eventReader);

  const sent = await requestJson(baseUrl, `/api/v1/conversations/${conversationId}/messages`, {
    method: 'POST',
    token: aliceToken,
    body: { text: 'سلام، این پیام در SQLite ذخیره می‌شود.' },
  });
  assert.equal(sent.status, 201);
  const eventText = await incomingEvent;
  assert.match(eventText, /event: message\n/);
  assert.match(eventText, /سلام، این پیام در SQLite ذخیره می‌شود/);

  const accountEventText = await accountIncomingEvent;
  assert.match(accountEventText, /event: message\n/);
  const accountEventData = accountEventText.match(/^data: (.+)$/m);
  assert.ok(accountEventData);
  assert.equal(JSON.parse(accountEventData[1]).conversationId, conversationId);
  assert.match(accountEventText, /سلام، این پیام در SQLite ذخیره می‌شود/);

  const replayAbort = new AbortController();
  const replayResponse = await fetch(`${baseUrl}/api/v1/events`, {
    headers: { authorization: `Bearer ${bobToken}`, 'last-event-id': '0' },
    signal: replayAbort.signal,
  });
  assert.equal(replayResponse.status, 200);
  const replayReader = replayResponse.body.getReader();
  const replayEvent = await readSseMessage(replayReader);
  assert.match(replayEvent, /سلام، این پیام در SQLite ذخیره می‌شود/);
  replayAbort.abort();
  await replayReader.cancel().catch(() => {});

  const bobConversations = await requestJson(baseUrl, '/api/v1/conversations', { token: bobToken });
  assert.equal(bobConversations.status, 200);
  assert.equal(bobConversations.data.conversations.length, 1);
  assert.equal(bobConversations.data.conversations[0].lastMessage.text, sent.data.message.text);

  const received = await requestJson(baseUrl, `/api/v1/conversations/${conversationId}/messages?limit=20`, { token: bobToken });
  assert.equal(received.status, 200);
  assert.equal(received.data.messages.length, 1);
  assert.equal(received.data.messages[0].sender.id, aliceId);
  assert.equal(received.data.messages[0].text, sent.data.message.text);
  assert.equal(received.data.messages[0].id, sent.data.message.id);

  const outsider = await requestJson(baseUrl, '/api/v1/auth/phone', {
    method: 'POST',
    body: { phone: '+999000000003' },
  });
  const forbiddenHistory = await requestJson(baseUrl, `/api/v1/conversations/${conversationId}/messages`, {
    token: outsider.data.accessToken,
  });
  assert.equal(forbiddenHistory.status, 404, 'non-members cannot read a private conversation');

  accountEventAbort.abort();
  await accountEventReader.cancel().catch(() => {});
  eventAbort.abort();
  await eventReader.cancel().catch(() => {});

  const stored = new DatabaseSync(databasePath);
  assert.equal(stored.prepare('SELECT COUNT(*) AS count FROM users').get().count, 3);
  assert.equal(stored.prepare('SELECT COUNT(*) AS count FROM messages').get().count, 1);
  const storedTokenHash = stored.prepare('SELECT token_hash FROM user_sessions WHERE user_id = ? ORDER BY created_at LIMIT 1').get(aliceId).token_hash;
  assert.equal(storedTokenHash.length, 64);
  assert.notEqual(storedTokenHash, aliceToken, 'only token hashes are persisted');
  stored.close();
  assert.ok(statSync(databasePath).size > 0);

  await api.close();
  api = new MessengerApiServer({ databasePath, host: '127.0.0.1', port: 0 });
  ({ port } = await api.listen());
  baseUrl = `http://127.0.0.1:${port}`;

  const restoredSession = await requestJson(baseUrl, '/api/v1/me', { token: bobToken });
  assert.equal(restoredSession.status, 200, 'hashed bearer session survives server restart');
  assert.equal(restoredSession.data.user.id, bobId);
  const restoredHistory = await requestJson(baseUrl, `/api/v1/conversations/${conversationId}/messages`, { token: bobToken });
  assert.equal(restoredHistory.status, 200);
  assert.equal(restoredHistory.data.messages[0].text, sent.data.message.text);
});

test('phone-only route validates E.164-style input and protected endpoints reject missing sessions', async (t) => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'messenger-api-validation-'));
  const api = new MessengerApiServer({ databasePath: path.join(directory, 'db.sqlite'), host: '127.0.0.1', port: 0 });
  const { port } = await api.listen();
  const baseUrl = `http://127.0.0.1:${port}`;
  t.after(async () => {
    await api.close();
    rmSync(directory, { recursive: true, force: true });
  });

  const invalidPhone = await requestJson(baseUrl, '/api/v1/auth/phone', {
    method: 'POST',
    body: { phone: 'not-a-phone' },
  });
  assert.equal(invalidPhone.status, 400);
  assert.equal(invalidPhone.data.error.code, 'INVALID_REQUEST');
  const unauthorized = await requestJson(baseUrl, '/api/v1/me');
  assert.equal(unauthorized.status, 401);
  assert.equal(unauthorized.data.error.code, 'UNAUTHORIZED');
});
