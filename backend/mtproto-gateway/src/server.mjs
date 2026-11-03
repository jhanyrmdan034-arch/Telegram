import { createPrivateKey, createPublicKey } from 'node:crypto';
import { readFileSync, statSync } from 'node:fs';
import net from 'node:net';
import { pathToFileURL } from 'node:url';
import { gunzipSync } from 'node:zlib';
import { dispatchApiRequest, serializeRpcResult } from './api.mjs';
import { createCtr } from './crypto.mjs';
import { createHandshakeState, handlePlainHandshake } from './handshake.mjs';
import { handleBindTempAuthKey, isBindTempAuthKey } from './bind-temp-auth-key.mjs';
import {
  makeAbridgedFrame,
  makeEncryptedPacket,
  makeNewSessionCreated,
  makePlainPacket,
  nextMessageId,
  parseEncryptedPacket,
  parseMessageContainer,
  parsePlainPacket,
} from './mtproto.mjs';
import { TLReader } from './tl.mjs';

const MAX_FRAME_BYTES = 2 * 1024 * 1024;
const MAX_BUFFER_BYTES = 4 * 1024 * 1024;
const MAX_CONNECTIONS = 256;
const GZIP_PACKED = 0x3072cfa1;
const MSGS_ACK = 0x62d6b459;

function isLoopback(host) {
  return host === '127.0.0.1' || host === '::1' || host === 'localhost';
}

function isZeroAuthKeyId(value) {
  return value.equals(Buffer.alloc(8));
}

function decodeCompressedQuery(body) {
  const reader = new TLReader(body);
  if (reader.uint32() !== GZIP_PACKED) return body;
  const packed = reader.bytes(MAX_FRAME_BYTES);
  reader.expectEnd();
  return gunzipSync(packed, { maxOutputLength: MAX_FRAME_BYTES });
}

function parseAcks(body) {
  const reader = new TLReader(body);
  if (reader.uint32() !== MSGS_ACK) return false;
  if (reader.uint32() !== 0x1cb5c415) throw new Error('Invalid msgs_ack vector');
  const count = reader.int32();
  if (count < 0 || count > 4096) throw new Error('Invalid msgs_ack count');
  for (let i = 0; i < count; i += 1) reader.int64();
  reader.expectEnd();
  return true;
}

class TcpPeer {
  constructor(owner, socket) {
    this.owner = owner;
    this.socket = socket;
    this.state = createHandshakeState(owner.privateKey, owner.publicKey);
    this.headerBuffer = Buffer.alloc(0);
    this.clearBuffer = Buffer.alloc(0);
    this.transportReady = false;
    this.incomingCipher = null;
    this.outgoingCipher = null;
    this.newSessionNotified = false;
    this.closed = false;

    socket.setNoDelay(true);
    socket.setTimeout(120_000);
    socket.on('data', (chunk) => this.onData(chunk));
    socket.on('timeout', () => this.close());
    socket.on('error', () => this.close());
    socket.on('close', () => this.close());
  }

  initializeObfuscatedTransport(header) {
    const incomingKey = header.subarray(8, 40);
    const incomingIv = header.subarray(40, 56);
    this.incomingCipher = createCtr(incomingKey, incomingIv, true);
    // The Android client consumes 64 bytes of its AES-CTR stream while
    // constructing its obfuscation header. Its first payload frame therefore
    // starts at stream offset 64 even though only the last 8 header bytes were
    // transformed on the wire.
    this.incomingCipher.update(Buffer.alloc(56));
    const decodedTail = this.incomingCipher.update(header.subarray(56, 64));
    if (!decodedTail.subarray(0, 4).equals(Buffer.from([0xef, 0xef, 0xef, 0xef]))) {
      throw new Error('Unsupported MTProto transport marker');
    }

    const reverseSeed = Buffer.from(header.subarray(8, 56)).reverse();
    this.outgoingCipher = createCtr(reverseSeed.subarray(0, 32), reverseSeed.subarray(32, 48), false);
    this.transportReady = true;
    this.owner.counters.transportsAccepted += 1;
  }

  onData(chunk) {
    if (this.closed) return;
    try {
      let payload = Buffer.from(chunk);
      if (!this.transportReady) {
        this.headerBuffer = Buffer.concat([this.headerBuffer, payload]);
        if (this.headerBuffer.length < 64) return;
        const header = this.headerBuffer.subarray(0, 64);
        payload = this.headerBuffer.subarray(64);
        this.headerBuffer = Buffer.alloc(0);
        this.initializeObfuscatedTransport(header);
      }
      if (payload.length) this.clearBuffer = Buffer.concat([this.clearBuffer, this.incomingCipher.update(payload)]);
      if (this.clearBuffer.length > MAX_BUFFER_BYTES) throw new Error('Inbound buffer limit exceeded');
      this.consumeFrames();
    } catch {
      this.owner.counters.protocolErrors += 1;
      this.close();
    }
  }

  consumeFrames() {
    while (!this.closed && this.clearBuffer.length > 0) {
      const first = this.clearBuffer[0];
      if (first & 0x80) {
        if (this.clearBuffer.length < 4) return;
        this.clearBuffer = this.clearBuffer.subarray(4); // quick-ack frame
        continue;
      }

      let headerLength;
      let packetLength;
      if (first === 0x7f) {
        if (this.clearBuffer.length < 4) return;
        packetLength = this.clearBuffer[1]
          | (this.clearBuffer[2] << 8)
          | (this.clearBuffer[3] << 16);
        packetLength *= 4;
        headerLength = 4;
      } else {
        packetLength = first * 4;
        headerLength = 1;
      }
      if (packetLength < 20 || packetLength > MAX_FRAME_BYTES) throw new Error('Invalid abridged MTProto frame length');
      if (this.clearBuffer.length < headerLength + packetLength) return;

      const packet = this.clearBuffer.subarray(headerLength, headerLength + packetLength);
      this.clearBuffer = this.clearBuffer.subarray(headerLength + packetLength);
      this.handlePacket(packet);
    }
  }

  sendPacket(packet) {
    if (this.closed || !this.outgoingCipher) return;
    const framed = makeAbridgedFrame(packet);
    this.socket.write(this.outgoingCipher.update(framed));
    this.owner.counters.framesSent += 1;
  }

  sendPlainBody(body) {
    this.sendPacket(this.owner.makePlain(body));
  }

  handlePacket(packet) {
    if (packet.length < 20) throw new Error('Truncated MTProto packet');
    const authKeyId = packet.subarray(0, 8);
    if (isZeroAuthKeyId(authKeyId)) {
      const plain = parsePlainPacket(packet);
      const response = handlePlainHandshake(this.state, plain.body);
      if (response) this.sendPlainBody(response);
      if (this.state.stage === 'established' && !this.state.countedEstablished) {
        this.state.countedEstablished = true;
        this.owner.sessions.add(this.state);
        this.owner.counters.handshakesSucceeded += 1;
      }
      return;
    }

    if (this.state.stage !== 'established') throw new Error('Encrypted request before auth-key handshake completion');
    const message = parseEncryptedPacket(this.state, packet);
    if (!message.salt.equals(this.state.serverSalt)) throw new Error('Unknown server salt');
    if (this.state.tempAuthBound && this.state.tempAuthExpiresAt <= Math.floor(Date.now() / 1000)) {
      throw new Error('Temporary auth key has expired');
    }
    if (this.state.sessionId === null) {
      this.state.sessionId = Buffer.from(message.sessionId);
      this.state.firstEncryptedMessageId = message.msgId;
    } else if (!message.sessionId.equals(this.state.sessionId)) {
      throw new Error('Session ID mismatch');
    }
    this.state.lastClientMessageId = message.msgId;
    this.owner.counters.encryptedPacketsReceived += 1;

    const innerMessages = parseMessageContainer(message.body);
    for (const inner of innerMessages) {
      if (parseAcks(inner.body)) continue;
      const query = decodeCompressedQuery(inner.body);
      if (!this.state.tempAuthBound && !isBindTempAuthKey(query)) {
        throw new Error('Application request received before auth.bindTempAuthKey');
      }
      const requestMessageId = inner.msgId ?? message.msgId;
      const boundResult = handleBindTempAuthKey(query, this.state);
      const dispatched = boundResult
        ? { method: 'auth.bindTempAuthKey', layer: null, initialized: false, result: boundResult }
        : dispatchApiRequest(query, requestMessageId, {
          advertiseHost: this.owner.advertiseHost,
          port: this.owner.advertisePort,
          dcId: 1,
          serverSalt: this.state.serverSalt,
        });
      if (dispatched.layer === 232) this.owner.counters.layer232Requests += 1;
      if (dispatched.method !== 'unsupported') this.owner.counters.baseRequests += 1;

      if (!this.newSessionNotified) {
        const newSession = makeNewSessionCreated(message.msgId, this.state.uniqueId, this.state.serverSalt);
        this.sendEncryptedBody(newSession);
        this.newSessionNotified = true;
      }
      const result = serializeRpcResult(inner.msgId ?? message.msgId, dispatched.result);
      this.sendEncryptedBody(result);
    }
  }

  sendEncryptedBody(body) {
    const seqNo = this.state.serverSeqNo * 2 + 1;
    this.state.serverSeqNo += 1;
    const packet = makeEncryptedPacket(this.state, body, nextMessageId(), seqNo);
    this.sendPacket(packet);
    this.owner.counters.encryptedPacketsSent += 1;
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.owner.peers.delete(this);
    this.owner.sessions.delete(this.state);
    if (this.state.authKey) this.state.authKey.fill(0);
    if (this.state.permanentAuthKey) this.state.permanentAuthKey.fill(0);
    this.socket.destroy();
  }
}

export class MtprotoGateway {
  constructor({
    privateKey,
    publicKey = createPublicKey(privateKey),
    host = '127.0.0.1',
    port = 5050,
    advertiseHost = host,
    advertisePort = port,
    allowRemote = false,
  }) {
    if (!privateKey) throw new Error('A project RSA private key must be supplied externally');
    if (!allowRemote && !isLoopback(host)) {
      throw new Error('Refusing non-loopback bind; explicitly set allowRemote=true to expose this prototype');
    }
    this.privateKey = privateKey;
    this.publicKey = publicKey;
    this.host = host;
    this.port = port;
    this.advertiseHost = advertiseHost;
    this.advertisePort = advertisePort;
    this.server = null;
    this.peers = new Set();
    this.sessions = new Set();
    this.counters = {
      connectionsAccepted: 0,
      transportsAccepted: 0,
      handshakesSucceeded: 0,
      encryptedPacketsReceived: 0,
      encryptedPacketsSent: 0,
      layer232Requests: 0,
      baseRequests: 0,
      framesSent: 0,
      protocolErrors: 0,
      rejectedConnections: 0,
    };
  }

  async listen() {
    if (this.server) throw new Error('Gateway is already listening');
    this.server = net.createServer((socket) => {
      if (this.peers.size >= MAX_CONNECTIONS) {
        this.counters.rejectedConnections += 1;
        socket.destroy();
        return;
      }
      this.counters.connectionsAccepted += 1;
      const peer = new TcpPeer(this, socket);
      this.peers.add(peer);
    });
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
    if (address && typeof address === 'object') {
      this.port = address.port;
      if (this.advertisePort === 0) this.advertisePort = address.port;
    }
    return this.address();
  }

  address() {
    return { host: this.host, port: this.port };
  }

  makePlain(body) {
    return makePlainPacket(body, nextMessageId());
  }

  snapshot() {
    return {
      ...this.counters,
      activeConnections: this.peers.size,
      activeSessions: this.sessions.size,
    };
  }

  async close() {
    for (const peer of [...this.peers]) peer.close();
    if (!this.server) return;
    const server = this.server;
    this.server = null;
    await new Promise((resolve) => server.close(() => resolve()));
  }
}

function loadPrivateKey(path) {
  if (!path) throw new Error('Set MTG_RSA_PRIVATE_KEY to an external private-key path');
  const metadata = statSync(path);
  if ((metadata.mode & 0o077) !== 0) throw new Error('Private-key file permissions must be 0600 or stricter');
  return createPrivateKey(readFileSync(path));
}

async function runFromEnvironment() {
  const host = process.env.MTG_HOST || '127.0.0.1';
  const port = Number.parseInt(process.env.MTG_PORT || '5050', 10);
  const privateKey = loadPrivateKey(process.env.MTG_RSA_PRIVATE_KEY);
  const gateway = new MtprotoGateway({
    privateKey,
    host,
    port,
    advertiseHost: process.env.MTG_ADVERTISE_HOST || host,
    advertisePort: Number.parseInt(process.env.MTG_ADVERTISE_PORT || String(port), 10),
    allowRemote: process.env.MTG_ALLOW_REMOTE === '1',
  });
  const address = await gateway.listen();
  process.stdout.write(`mtproto-gateway listening on ${address.host}:${address.port}; layer=232-subset\n`);
  process.stdout.write('outbound_connections=0; secrets are not logged\n');

  const shutdown = async () => {
    await gateway.close();
    process.exit(0);
  };
  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runFromEnvironment().catch(() => {
    process.stderr.write('mtproto-gateway startup failed; check local configuration and key-file permissions\n');
    process.exitCode = 1;
  });
}
