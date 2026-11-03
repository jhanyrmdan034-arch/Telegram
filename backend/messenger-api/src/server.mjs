import http from 'node:http';
import { pathToFileURL } from 'node:url';
import { MessengerDatabase } from './database.mjs';

const MAX_BODY_BYTES = 64 * 1024;
const MAX_EVENT_STREAMS_PER_CONVERSATION = 100;
const MAX_EVENT_STREAMS_PER_USER = 20;
const MAX_EVENT_REPLAY_MESSAGES = 1000;
const PHONE_LOGIN_WINDOW_MS = 60_000;
const PHONE_LOGIN_LIMIT = 20;

function isLoopback(host) {
  return host === '127.0.0.1' || host === '::1' || host === 'localhost';
}

function writeJson(response, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': body.length,
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
  });
  response.end(body);
}

async function readJson(request) {
  const contentType = String(request.headers['content-type'] || '').split(';')[0].trim().toLowerCase();
  if (contentType !== 'application/json') {
    const error = new Error('Content-Type must be application/json');
    error.statusCode = 415;
    error.code = 'UNSUPPORTED_MEDIA_TYPE';
    throw error;
  }
  const chunks = [];
  let length = 0;
  for await (const chunk of request) {
    length += chunk.length;
    if (length > MAX_BODY_BYTES) {
      const error = new Error('Request body is too large');
      error.statusCode = 413;
      error.code = 'BODY_TOO_LARGE';
      throw error;
    }
    chunks.push(chunk);
  }
  if (length === 0) return {};
  try {
    const value = JSON.parse(Buffer.concat(chunks, length).toString('utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('JSON body must be an object');
    return value;
  } catch {
    const error = new Error('Malformed JSON request body');
    error.statusCode = 400;
    error.code = 'INVALID_JSON';
    throw error;
  }
}

function parseEventCursor(value) {
  if (typeof value !== 'string' || !/^\d{1,15}$/.test(value)) return 0;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : 0;
}

function eventRecord(message) {
  return `id: ${message.id}\nevent: message\ndata: ${JSON.stringify(message)}\n\n`;
}

function parseId(value, name) {
  if (!/^\d{1,15}$/.test(value || '')) {
    const error = new Error(`${name} must be a positive integer`);
    error.statusCode = 400;
    error.code = 'INVALID_ID';
    throw error;
  }
  const id = Number(value);
  if (!Number.isSafeInteger(id) || id <= 0) {
    const error = new Error(`${name} must be a positive integer`);
    error.statusCode = 400;
    error.code = 'INVALID_ID';
    throw error;
  }
  return id;
}

function bearerToken(request) {
  const header = request.headers.authorization;
  if (typeof header !== 'string') return null;
  const match = /^Bearer ([A-Za-z0-9_-]{32,256})$/.exec(header);
  return match ? match[1] : null;
}

function mapError(error) {
  if (error.statusCode) return { status: error.statusCode, code: error.code || 'REQUEST_FAILED', message: error.message };
  if (error instanceof RangeError || error instanceof TypeError) {
    return { status: 400, code: 'INVALID_REQUEST', message: error.message };
  }
  return { status: 500, code: 'INTERNAL_ERROR', message: 'Internal server error' };
}

export class MessengerApiServer {
  constructor({ databasePath, host = '127.0.0.1', port = 5050, allowRemote = false } = {}) {
    if (!databasePath) throw new Error('databasePath is required; set MESSENGER_DB_PATH');
    if (!allowRemote && !isLoopback(host)) {
      throw new Error('Refusing non-loopback bind; set allowRemote=true only behind a trusted TLS proxy');
    }
    this.database = new MessengerDatabase(databasePath);
    this.host = host;
    this.port = port;
    this.server = null;
    this.eventStreams = new Map();
    this.userEventStreams = new Map();
    this.eventResponses = new Set();
    this.phoneLoginAttempts = new Map();
  }

  allowPhoneLogin(remoteAddress) {
    const now = Date.now();
    const current = this.phoneLoginAttempts.get(remoteAddress);
    if (!current || now - current.startedAt >= PHONE_LOGIN_WINDOW_MS) {
      this.phoneLoginAttempts.set(remoteAddress, { startedAt: now, count: 1 });
    } else if (current.count >= PHONE_LOGIN_LIMIT) {
      return false;
    } else {
      current.count += 1;
    }
    if (this.phoneLoginAttempts.size > 4096) {
      for (const [address, entry] of this.phoneLoginAttempts) {
        if (now - entry.startedAt >= PHONE_LOGIN_WINDOW_MS) this.phoneLoginAttempts.delete(address);
      }
    }
    return true;
  }

  authenticate(request, response) {
    const token = bearerToken(request);
    const user = token && this.database.authenticate(token);
    if (!user) {
      writeJson(response, 401, { error: { code: 'UNAUTHORIZED', message: 'A valid bearer session is required' } });
      return null;
    }
    return { token, user };
  }

  publish(conversationId, message) {
    const record = eventRecord(message);
    const conversationStreams = this.eventStreams.get(conversationId);
    if (conversationStreams) {
      for (const response of [...conversationStreams]) response.write(record);
    }
    for (const userId of this.database.getConversationMemberIds(conversationId)) {
      const userStreams = this.userEventStreams.get(userId);
      if (userStreams) {
        for (const response of [...userStreams]) response.write(record);
      }
    }
  }

  attachEventStream(response, conversationId) {
    let streams = this.eventStreams.get(conversationId);
    if (!streams) {
      streams = new Set();
      this.eventStreams.set(conversationId, streams);
    }
    if (streams.size >= MAX_EVENT_STREAMS_PER_CONVERSATION) {
      writeJson(response, 429, { error: { code: 'TOO_MANY_STREAMS', message: 'Too many event streams for this conversation' } });
      return;
    }

    response.writeHead(200, {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
      'x-accel-buffering': 'no',
      'x-content-type-options': 'nosniff',
    });
    response.write(': connected\n\n');
    streams.add(response);
    this.eventResponses.add(response);
    const heartbeat = setInterval(() => response.write(': keep-alive\n\n'), 20_000);
    heartbeat.unref?.();
    const cleanup = () => {
      clearInterval(heartbeat);
      streams.delete(response);
      this.eventResponses.delete(response);
      if (!streams.size) this.eventStreams.delete(conversationId);
    };
    response.once('close', cleanup);
  }


  attachUserEventStream(response, userId, afterId) {
    let streams = this.userEventStreams.get(userId);
    if (!streams) {
      streams = new Set();
      this.userEventStreams.set(userId, streams);
    }
    if (streams.size >= MAX_EVENT_STREAMS_PER_USER) {
      writeJson(response, 429, { error: { code: 'TOO_MANY_STREAMS', message: 'Too many event streams for this user' } });
      return;
    }

    response.writeHead(200, {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
      'x-accel-buffering': 'no',
      'x-content-type-options': 'nosniff',
    });
    response.write(': connected\n\n');
    streams.add(response);
    this.eventResponses.add(response);
    const heartbeat = setInterval(() => response.write(': keep-alive\n\n'), 20_000);
    heartbeat.unref?.();
    const cleanup = () => {
      clearInterval(heartbeat);
      streams.delete(response);
      this.eventResponses.delete(response);
      if (!streams.size) this.userEventStreams.delete(userId);
    };
    response.once('close', cleanup);

    const missedMessages = this.database.getMessagesForUserAfter(userId, afterId, MAX_EVENT_REPLAY_MESSAGES);
    for (const message of missedMessages) response.write(eventRecord(message));
    if (missedMessages.length === MAX_EVENT_REPLAY_MESSAGES) response.end();
  }

  async handle(request, response) {
    const url = new URL(request.url || '/', 'http://127.0.0.1');
    const pathname = url.pathname;
    try {
      if (request.method === 'GET' && pathname === '/health') {
        return writeJson(response, 200, { status: this.database.health() ? 'ok' : 'degraded', database: 'sqlite' });
      }

      if (request.method === 'POST' && pathname === '/api/v1/auth/phone') {
        if (!this.allowPhoneLogin(request.socket.remoteAddress || 'unknown')) {
          return writeJson(response, 429, { error: { code: 'RATE_LIMITED', message: 'Too many phone-login attempts from this address' } });
        }
        const body = await readJson(request);
        const result = this.database.createOrLogin(body.phone);
        return writeJson(response, 200, {
          created: result.created,
          accessToken: result.token,
          tokenType: 'Bearer',
          expiresAt: result.expiresAt,
          user: result.user,
          authMode: 'phone-only-test-no-otp',
        });
      }

      const auth = this.authenticate(request, response);
      if (!auth) return;

      if (request.method === 'GET' && pathname === '/api/v1/events') {
        return this.attachUserEventStream(response, auth.user.id, parseEventCursor(request.headers['last-event-id']));
      }

      if (request.method === 'GET' && pathname === '/api/v1/me') {
        return writeJson(response, 200, { user: auth.user });
      }

      if (request.method === 'PATCH' && pathname === '/api/v1/me') {
        const body = await readJson(request);
        const user = this.database.updateProfile(auth.user.id, body);
        if (!user) return writeJson(response, 404, { error: { code: 'USER_NOT_FOUND', message: 'User not found' } });
        return writeJson(response, 200, { user });
      }

      if (request.method === 'DELETE' && pathname === '/api/v1/auth/session') {
        this.database.revokeSession(auth.token);
        return writeJson(response, 200, { revoked: true });
      }

      if (request.method === 'GET' && pathname === '/api/v1/users/search') {
        const requestedLimit = Number(url.searchParams.get('limit') || 20);
        if (!Number.isSafeInteger(requestedLimit) || requestedLimit < 1 || requestedLimit > 50) {
          const error = new RangeError('limit must be an integer from 1 to 50');
          error.statusCode = 400;
          error.code = 'INVALID_LIMIT';
          throw error;
        }
        const users = this.database.searchUsers(auth.user.id, url.searchParams.get('q') || '', requestedLimit);
        return writeJson(response, 200, { users });
      }

      const publicUserMatch = /^\/api\/v1\/users\/(\d+)$/.exec(pathname);
      if (request.method === 'GET' && publicUserMatch) {
        const user = this.database.getUser(parseId(publicUserMatch[1], 'userId'));
        if (!user) return writeJson(response, 404, { error: { code: 'USER_NOT_FOUND', message: 'User not found' } });
        return writeJson(response, 200, { user });
      }

      if (request.method === 'POST' && pathname === '/api/v1/conversations/direct') {
        const body = await readJson(request);
        const conversation = this.database.createDirectConversation(auth.user.id, body.peerUserId);
        return writeJson(response, 200, { conversation });
      }

      if (request.method === 'GET' && pathname === '/api/v1/conversations') {
        return writeJson(response, 200, { conversations: this.database.listConversations(auth.user.id) });
      }

      const conversationMatch = /^\/api\/v1\/conversations\/(\d+)(?:\/(messages|events))?$/.exec(pathname);
      if (conversationMatch) {
        const conversationId = parseId(conversationMatch[1], 'conversationId');
        const suffix = conversationMatch[2] || '';
        if (suffix === 'events' && request.method === 'GET') {
          if (!this.database.isConversationMember(conversationId, auth.user.id)) {
            return writeJson(response, 404, { error: { code: 'CONVERSATION_NOT_FOUND', message: 'Conversation not found' } });
          }
          this.attachEventStream(response, conversationId);
          return;
        }
        if (suffix === 'messages' && request.method === 'GET') {
          const limit = url.searchParams.get('limit') || 50;
          const before = url.searchParams.get('before');
          const messages = this.database.getMessages(conversationId, auth.user.id, {
            limit: Number(limit),
            beforeId: before === null ? null : parseId(before, 'before'),
          });
          return writeJson(response, 200, { messages });
        }
        if (suffix === 'messages' && request.method === 'POST') {
          const body = await readJson(request);
          const message = this.database.sendTextMessage(conversationId, auth.user.id, body.text);
          this.publish(conversationId, message);
          return writeJson(response, 201, { message });
        }
      }

      return writeJson(response, 404, { error: { code: 'ROUTE_NOT_FOUND', message: 'Route not found' } });
    } catch (error) {
      if (response.headersSent) {
        response.destroy();
        return;
      }
      const mapped = mapError(error);
      writeJson(response, mapped.status, { error: { code: mapped.code, message: mapped.message } });
    }
  }

  async listen() {
    if (this.server) throw new Error('API server is already listening');
    this.server = http.createServer({ maxHeaderSize: 16 * 1024 }, (request, response) => {
      void this.handle(request, response);
    });
    this.server.headersTimeout = 10_000;
    this.server.requestTimeout = 15_000;
    this.server.keepAliveTimeout = 5_000;
    await new Promise((resolve, reject) => {
      const onError = (error) => {
        this.server?.off('listening', onListening);
        reject(error);
      };
      const onListening = () => {
        this.server?.off('error', onError);
        resolve();
      };
      this.server.once('error', onError);
      this.server.once('listening', onListening);
      this.server.listen(this.port, this.host);
    });
    const address = this.server.address();
    if (address && typeof address === 'object') this.port = address.port;
    return { host: this.host, port: this.port };
  }

  async close() {
    for (const response of this.eventResponses) response.destroy();
    this.eventResponses.clear();
    this.eventStreams.clear();
    this.userEventStreams.clear();
    if (this.server) {
      const server = this.server;
      this.server = null;
      await new Promise((resolve) => server.close(() => resolve()));
    }
    this.database.close();
  }
}

async function runFromEnvironment() {
  const host = process.env.MESSENGER_HOST || '127.0.0.1';
  const port = Number.parseInt(process.env.MESSENGER_PORT || '8080', 10);
  const databasePath = process.env.MESSENGER_DB_PATH || './.data/messenger.sqlite';
  const allowRemote = process.env.MESSENGER_ALLOW_REMOTE === '1';
  const api = new MessengerApiServer({ databasePath, host, port, allowRemote });
  const address = await api.listen();
  process.stdout.write(`messenger api listening on ${address.host}:${address.port}\n`);
  process.stdout.write(`sqlite database: ${api.database.databasePath}\n`);
  process.stdout.write('phone-only login is test-only and unsafe; place behind TLS before any network exposure\n');
  const shutdown = async () => {
    await api.close();
    process.exit(0);
  };
  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runFromEnvironment().catch(() => {
    process.stderr.write('messenger api startup failed; check local configuration\n');
    process.exitCode = 1;
  });
}
