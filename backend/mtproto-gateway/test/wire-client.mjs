import assert from 'node:assert/strict';
import { createCipheriv, createDecipheriv, publicEncrypt, randomBytes, constants as cryptoConstants } from 'node:crypto';
import net from 'node:net';
import {
  aesIge,
  createCtr,
  decryptMtproto,
  encryptMtproto,
  encryptMtproto1,
  fromBigInt,
  modPow,
  rsaPublicFingerprint,
  sha1,
  sha256,
  toBigInt,
  xorBuffers,
} from '../src/crypto.mjs';
import { makeAbridgedFrame, makePlainPacket, parsePlainPacket } from '../src/mtproto.mjs';
import { TLReader, TLWriter, VECTOR_CONSTRUCTOR } from '../src/tl.mjs';

const C = Object.freeze({
  reqPqMulti: 0xbe7e8ef1,
  resPq: 0x05162463,
  reqDhParams: 0xd712e4be,
  serverDhParamsOk: 0xd0e8075c,
  pqInnerDataDc: 0xa9f55f95,
  pqInnerDataTempDc: 0x56fddf88,
  serverDhInnerData: 0xb5890dba,
  setClientDhParams: 0xf5045f1f,
  clientDhInnerData: 0x6643b654,
  dhGenOk: 0x3bcbf734,
  invokeWithLayer: 0xda9b0d0d,
  initConnection: 0xc1cd5ea9,
  jsonObject: 0x99c1d49d,
  jsonObjectValue: 0xc0de1bd9,
  jsonString: 0xb71e767a,
  jsonNumber: 0x2be0dfa4,
  helpGetConfig: 0xc4f9186b,
  ping: 0x7abe77ec,
  getFutureSalts: 0xb921bd04,
  futureSalts: 0xae500895,
  futureSalt: 0x0949d9dc,
  rpcResult: 0xf35c6d01,
  authBindTempAuthKey: 0xcdd42a05,
  bindAuthKeyInner: 0x75a3f765,
  boolTrue: 0x997275b5,
  newSessionCreated: 0x9ec20908,
  pong: 0x347773c5,
  config: 0xcc1a241e,
  dcOption: 0x18b7a10d,
});

const DH_PRIME = BigInt(
  '0xc71caeb9c6b1c9048e6c522f70f13f73980d40238e3e21c14934d037563d930f' +
  '48198a0aa7c14058229493d22530f4dbfa336f6e0ac925139543aed44cce7c372' +
  '0fd51f69458705ac68cd4fe6b6b13abdc9746512969328454f18faf8c595f642' +
  '477fe96bb2a941d5bcd1d4ac8cc49880708fa9b378e3c4f3a9060bee67cf9a4a' +
  '4a695811051907e162753b56b0f6b410dba74d8a84b2a14b3144e0ef1284754f' +
  'd17ed950d5965b4b9dd46582db1178d169c6bc465b0d6ff9ca3928fef5b9ae4e' +
  '418fc15e83ebea0f87fa9ff5eed70050ded2849f47bf959d956850ce929851f0' +
  'd8115f635b105ee2e4e15d04b2454bf6f4fadf034b10403119cd8e3b92fcc5b',
);
const DH_G = 3;

function makeMessageId() {
  return (BigInt(Math.floor(Date.now() / 1000)) << 32n) | 4n;
}

function minimalBytes(value) {
  let hex = BigInt(value).toString(16);
  if (hex.length & 1) hex = `0${hex}`;
  return Buffer.from(hex, 'hex');
}

function factorPq(value) {
  const numeric = Number(value);
  for (let candidate = 2; candidate * candidate <= numeric; candidate += 1) {
    if (numeric % candidate === 0) return [BigInt(candidate), BigInt(numeric / candidate)];
  }
  throw new Error('Gateway returned a non-factorable pq test value');
}

function makeClientRsaBlock(inner, publicKey) {
  if (inner.length > 192) throw new Error('Test P_Q_inner_data exceeds RSA padding envelope');
  const modulus = toBigInt(Buffer.from(publicKey.export({ format: 'jwk' }).n, 'base64url'));
  for (let attempt = 0; attempt < 64; attempt += 1) {
    const forwardData = Buffer.concat([inner, randomBytes(192 - inner.length)]);
    const randomKey = randomBytes(32);
    const reversed = Buffer.from(forwardData).reverse();
    const innerHash = sha256(Buffer.concat([randomKey, forwardData]));
    const encrypted = aesIge(Buffer.concat([reversed, innerHash]), randomKey, Buffer.alloc(32), false);
    const maskedKey = xorBuffers(randomKey, sha256(encrypted));
    const rsaInput = Buffer.concat([maskedKey, encrypted]);
    assert.equal(rsaInput.length, 256);
    if (toBigInt(rsaInput) < modulus) {
      return publicEncrypt({ key: publicKey, padding: cryptoConstants.RSA_NO_PADDING }, rsaInput);
    }
  }
  throw new Error('Could not generate a valid RSA no-padding test block');
}

function parseHashedDhInner(encrypted, key, iv) {
  const value = aesIge(encrypted, key, iv, true);
  for (let padding = 0; padding <= 15; padding += 1) {
    const bodyEnd = value.length - padding;
    if (bodyEnd < 20) continue;
    if (sha1(value.subarray(20, bodyEnd)).equals(value.subarray(0, 20))) {
      return value.subarray(20, bodyEnd);
    }
  }
  throw new Error('DH inner SHA-1 verification failed in test client');
}

function deriveTmpKeyIv(newNonce, serverNonce) {
  const a = sha1(Buffer.concat([newNonce, serverNonce]));
  const b = sha1(Buffer.concat([serverNonce, newNonce]));
  const c = sha1(Buffer.concat([newNonce, newNonce]));
  return {
    key: Buffer.concat([a, b.subarray(0, 12)]),
    iv: Buffer.concat([b.subarray(12, 20), c, newNonce.subarray(0, 4)]),
  };
}

function makeEncryptedClientPacket(authKey, authKeyId, salt, sessionId, body, msgId, seqNo = 1) {
  const payload = Buffer.from(body);
  const base = new TLWriter()
    .fixed(salt, 8)
    .fixed(sessionId, 8)
    .int64(msgId)
    .int32(seqNo)
    .int32(payload.length)
    .fixed(payload)
    .toBuffer();
  const pad = ((16 - (base.length % 16)) % 16) + 32;
  const plaintext = Buffer.concat([base, randomBytes(pad)]);
  const { msgKey, ciphertext } = encryptMtproto(authKey, plaintext, 0);
  return Buffer.concat([authKeyId, msgKey, ciphertext]);
}

function buildInitConfigQuery(layer = 232) {
  const helpGetConfig = new TLWriter().uint32(C.helpGetConfig).toBuffer();
  const params = new TLWriter()
    .uint32(C.jsonObject)
    .uint32(VECTOR_CONSTRUCTOR)
    .int32(2)
    .uint32(C.jsonObjectValue)
    .string('package_id')
    .uint32(C.jsonString)
    .string('org.example.gateway-test')
    .uint32(C.jsonObjectValue)
    .string('tz_offset')
    .uint32(C.jsonNumber)
    .fixed(Buffer.alloc(8))
    .toBuffer();
  const initConnection = new TLWriter()
    .uint32(C.initConnection)
    .int32(2) // params flag; mirrors the Android client's JSON init metadata
    .int32(4)
    .string('wire-test')
    .string('test-os')
    .string('poc-0.1')
    .string('en')
    .string('android')
    .string('en')
    .fixed(params)
    .fixed(helpGetConfig)
    .toBuffer();
  return new TLWriter()
    .uint32(C.invokeWithLayer)
    .int32(layer)
    .fixed(initConnection)
    .toBuffer();
}

function parseConfig(buffer) {
  const reader = new TLReader(buffer);
  assert.equal(reader.uint32(), C.config);
  const flags = reader.int32();
  const date = reader.int32();
  const expires = reader.int32();
  const bool = reader.uint32();
  assert.ok(bool === 0x997275b5 || bool === 0xbc799737);
  const thisDc = reader.int32();
  assert.equal(reader.uint32(), VECTOR_CONSTRUCTOR);
  const dcCount = reader.int32();
  assert.ok(dcCount > 0 && dcCount <= 10);
  const dcOptions = [];
  for (let i = 0; i < dcCount; i += 1) {
    assert.equal(reader.uint32(), C.dcOption);
    const dcFlags = reader.int32();
    const id = reader.int32();
    const ip = reader.string(4096);
    const port = reader.int32();
    if (dcFlags & 1024) reader.bytes(4096);
    dcOptions.push({ id, ip, port, flags: dcFlags });
  }
  reader.string(4096); // dc_txt_domain_name
  for (let i = 0; i < 17; i += 1) reader.int32();
  if (flags & 1) reader.int32();
  for (let i = 0; i < 4; i += 1) reader.int32();
  reader.string(4096); // me_url_prefix
  if (flags & 128) reader.string(4096);
  if (flags & 512) reader.string(4096);
  if (flags & 1024) reader.string(4096);
  if (flags & 2048) reader.string(4096);
  if (flags & 4096) reader.string(4096);
  reader.int32(); // caption_length_max
  reader.int32(); // message_length_max
  reader.int32(); // webfile_dc_id
  if (flags & 4) {
    reader.string(4096);
    reader.int32();
    reader.int32();
  }
  // The POC leaves the optional reaction and autologin fields disabled.
  if (flags & 32768) throw new Error('Unexpected optional default-reaction field in POC config');
  if (flags & 65536) reader.string(4096);
  reader.expectEnd();
  return { flags, date, expires, thisDc, dcOptions };
}

export class WireTestClient {
  constructor(socket, publicKey) {
    this.socket = socket;
    this.publicKey = publicKey;
    this.outgoingCipher = null;
    this.incomingCipher = null;
    this.clearBuffer = Buffer.alloc(0);
    this.frameQueue = [];
    this.waiters = [];
    this.closedError = null;
    this.cryptoState = null;
    this.lastServerSeqNo = null;
    this.remoteAddress = socket.remoteAddress;
    socket.on('data', (data) => this.onData(data));
    socket.on('error', (error) => this.fail(error));
    socket.on('close', () => this.fail(new Error('Gateway socket closed')));
  }

  static async connect({ host, port, publicKey }) {
    const socket = net.createConnection({ host, port });
    await new Promise((resolve, reject) => {
      socket.once('connect', resolve);
      socket.once('error', reject);
    });
    const client = new WireTestClient(socket, publicKey);
    client.initializeObfuscation();
    return client;
  }

  initializeObfuscation() {
    const header = randomBytes(64);
    header[0] = 0x11;
    header[4] = 0x22;
    header.fill(0xef, 56, 60);
    const clientKey = header.subarray(8, 40);
    const clientIv = header.subarray(40, 56);
    const sendCipher = createCipheriv('aes-256-ctr', clientKey, clientIv);
    const transformedHeader = sendCipher.update(header);
    transformedHeader.copy(header, 56, 56, 64);

    const reverseSeed = Buffer.from(header.subarray(8, 56)).reverse();
    this.outgoingCipher = sendCipher; // already advanced by the 64-byte header
    this.incomingCipher = createCtr(reverseSeed.subarray(0, 32), reverseSeed.subarray(32, 48), true);
    this.socket.write(header);
  }

  onData(data) {
    if (this.closedError) return;
    try {
      this.clearBuffer = Buffer.concat([this.clearBuffer, this.incomingCipher.update(data)]);
      this.drainFrames();
    } catch (error) {
      this.fail(error);
    }
  }

  drainFrames() {
    while (this.clearBuffer.length) {
      const first = this.clearBuffer[0];
      if (first & 0x80) {
        if (this.clearBuffer.length < 4) return;
        this.clearBuffer = this.clearBuffer.subarray(4);
        continue;
      }
      let headerLength;
      let bytes;
      if (first === 0x7f) {
        if (this.clearBuffer.length < 4) return;
        bytes = (this.clearBuffer[1] | (this.clearBuffer[2] << 8) | (this.clearBuffer[3] << 16)) * 4;
        headerLength = 4;
      } else {
        bytes = first * 4;
        headerLength = 1;
      }
      if (bytes < 20 || bytes > 2 * 1024 * 1024) throw new Error('Invalid server abridged frame');
      if (this.clearBuffer.length < headerLength + bytes) return;
      this.frameQueue.push(this.clearBuffer.subarray(headerLength, headerLength + bytes));
      this.clearBuffer = this.clearBuffer.subarray(headerLength + bytes);
    }
    while (this.frameQueue.length && this.waiters.length) this.waiters.shift().resolve(this.frameQueue.shift());
  }

  fail(error) {
    if (this.closedError) return;
    this.closedError = error;
    while (this.waiters.length) this.waiters.shift().reject(error);
  }

  async readFrame(timeoutMs = 5000) {
    if (this.frameQueue.length) return this.frameQueue.shift();
    if (this.closedError) throw this.closedError;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const index = this.waiters.indexOf(waiter);
        if (index >= 0) this.waiters.splice(index, 1);
        reject(new Error('Timed out waiting for MTProto response'));
      }, timeoutMs);
      const waiter = {
        resolve: (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      };
      this.waiters.push(waiter);
    });
  }

  sendFrame(packet) {
    this.socket.write(this.outgoingCipher.update(makeAbridgedFrame(packet)));
  }

  sendPlain(body) {
    this.sendFrame(makePlainPacket(body, makeMessageId()));
  }

  async readPlain(expectedConstructor) {
    const packet = parsePlainPacket(await this.readFrame());
    const reader = new TLReader(packet.body);
    assert.equal(reader.uint32(), expectedConstructor);
    return { packet, reader };
  }

  async performHandshake(role = 'permanent') {
    if (role !== 'permanent' && role !== 'temporary') throw new Error('Unknown handshake role');
    const clientNonce = randomBytes(16);
    this.sendPlain(new TLWriter().uint32(C.reqPqMulti).fixed(clientNonce).toBuffer());

    const { reader: pqReader } = await this.readPlain(C.resPq);
    assert.ok(pqReader.fixed(16).equals(clientNonce), 'resPQ echoed client nonce');
    const serverNonce = Buffer.from(pqReader.fixed(16));
    const pqBytes = pqReader.bytes(8);
    assert.equal(pqReader.uint32(), VECTOR_CONSTRUCTOR);
    const fingerprintCount = pqReader.int32();
    assert.ok(fingerprintCount >= 1 && fingerprintCount <= 8);
    const fingerprints = [];
    for (let i = 0; i < fingerprintCount; i += 1) fingerprints.push(Buffer.from(pqReader.fixed(8)));
    pqReader.expectEnd();
    const fingerprint = rsaPublicFingerprint(this.publicKey);
    assert.ok(fingerprints.some((candidate) => candidate.equals(fingerprint)), "resPQ advertised this project's RSA fingerprint");

    const [pValue, qValue] = factorPq(toBigInt(pqBytes));
    const pBytes = fromBigInt(pValue, 4);
    const qBytes = fromBigInt(qValue, 4);
    const newNonce = randomBytes(32);
    const innerWriter = new TLWriter()
      .uint32(role === 'permanent' ? C.pqInnerDataDc : C.pqInnerDataTempDc)
      .bytes(pqBytes)
      .bytes(pBytes)
      .bytes(qBytes)
      .fixed(clientNonce)
      .fixed(serverNonce)
      .fixed(newNonce)
      .int32(1);
    if (role === 'temporary') innerWriter.int32(3600);
    const pqInner = innerWriter.toBuffer();
    const rsaBlock = makeClientRsaBlock(pqInner, this.publicKey);
    const reqDh = new TLWriter()
      .uint32(C.reqDhParams)
      .fixed(clientNonce)
      .fixed(serverNonce)
      .bytes(pBytes)
      .bytes(qBytes)
      .fixed(fingerprint)
      .bytes(rsaBlock)
      .toBuffer();
    this.sendPlain(reqDh);

    const { reader: dhReader } = await this.readPlain(C.serverDhParamsOk);
    assert.ok(dhReader.fixed(16).equals(clientNonce), 'server_DH_params echoed client nonce');
    assert.ok(dhReader.fixed(16).equals(serverNonce), 'server_DH_params echoed server nonce');
    const encryptedAnswer = dhReader.bytes(2048);
    dhReader.expectEnd();
    const tmp = deriveTmpKeyIv(newNonce, serverNonce);
    const serverDhBytes = parseHashedDhInner(encryptedAnswer, tmp.key, tmp.iv);
    const serverDh = new TLReader(serverDhBytes);
    assert.equal(serverDh.uint32(), C.serverDhInnerData);
    assert.ok(serverDh.fixed(16).equals(clientNonce));
    assert.ok(serverDh.fixed(16).equals(serverNonce));
    const generator = serverDh.int32();
    const dhPrime = Buffer.from(serverDh.bytes(256));
    const gABytes = Buffer.from(serverDh.bytes(256));
    const serverTime = serverDh.int32();
    serverDh.expectEnd();
    assert.equal(generator, DH_G);
    assert.equal(toBigInt(dhPrime), DH_PRIME);
    assert.ok(Math.abs(Math.floor(Date.now() / 1000) - serverTime) < 60);

    const a = toBigInt(randomBytes(256));
    const gB = modPow(BigInt(generator), a, DH_PRIME);
    const authKey = fromBigInt(modPow(toBigInt(gABytes), a, DH_PRIME), 256);
    const gBBytes = minimalBytes(gB);
    const clientDhInner = new TLWriter()
      .uint32(C.clientDhInnerData)
      .fixed(clientNonce)
      .fixed(serverNonce)
      .int64(0)
      .bytes(gBBytes)
      .toBuffer();
    const padding = (16 - ((20 + clientDhInner.length) % 16)) % 16;
    const setClientPayload = Buffer.concat([sha1(clientDhInner), clientDhInner, randomBytes(padding)]);
    const encryptedClientDh = aesIge(setClientPayload, tmp.key, tmp.iv, false);
    this.sendPlain(new TLWriter()
      .uint32(C.setClientDhParams)
      .fixed(clientNonce)
      .fixed(serverNonce)
      .bytes(encryptedClientDh)
      .toBuffer());

    const { reader: dhOk } = await this.readPlain(C.dhGenOk);
    assert.ok(dhOk.fixed(16).equals(clientNonce));
    assert.ok(dhOk.fixed(16).equals(serverNonce));
    const receivedHash = Buffer.from(dhOk.fixed(16));
    dhOk.expectEnd();
    const authHash = sha1(authKey);
    const expectedHash = sha1(Buffer.concat([newNonce, Buffer.from([1]), authHash.subarray(0, 8)])).subarray(4, 20);
    assert.ok(receivedHash.equals(expectedHash), 'dh_gen_ok proves both parties derived the same auth key');

    const serverSalt = xorBuffers(newNonce.subarray(0, 8), serverNonce.subarray(0, 8));
    const keyState = {
      authKey,
      authKeyId: Buffer.from(authHash.subarray(12, 20)),
      serverSalt: Buffer.from(serverSalt),
      sessionId: randomBytes(8),
    };
    if (role === 'permanent') {
      this.permanentCryptoState = keyState;
    } else {
      this.cryptoState = keyState;
    }
    return { role, authKeyId: Buffer.from(keyState.authKeyId), serverSalt: Buffer.from(serverSalt) };
  }

  sendEncryptedRequest(body, msgId = makeMessageId(), seqNo = 1) {
    const packet = makeEncryptedClientPacket(
      this.cryptoState.authKey,
      this.cryptoState.authKeyId,
      this.cryptoState.serverSalt,
      this.cryptoState.sessionId,
      body,
      msgId,
      seqNo,
    );
    this.sendFrame(packet);
    return msgId;
  }

  async readEncryptedResponse() {
    const packet = await this.readFrame();
    assert.ok(packet.subarray(0, 8).equals(this.cryptoState.authKeyId), 'response auth_key_id matches');
    const plaintext = decryptMtproto(
      this.cryptoState.authKey,
      packet.subarray(8, 24),
      packet.subarray(24),
      8,
    );
    const reader = new TLReader(plaintext);
    const salt = reader.fixed(8);
    const sessionId = reader.fixed(8);
    const msgId = reader.int64();
    const seqNo = reader.int32();
    assert.equal(seqNo & 1, 1, 'server response is content-related');
    if (this.lastServerSeqNo === null) assert.equal(seqNo, 1);
    else assert.equal(seqNo, this.lastServerSeqNo + 2, 'server content sequence number advances');
    this.lastServerSeqNo = seqNo;
    const bodyLength = reader.int32();
    const body = reader.fixed(bodyLength);
    assert.ok(reader.remaining >= 12 && reader.remaining <= 1024, 'response padding is in MTProto bounds');
    assert.ok(salt.equals(this.cryptoState.serverSalt), 'response carries the negotiated server salt');
    assert.ok(sessionId.equals(this.cryptoState.sessionId), 'response is bound to the test session');
    return { msgId, seqNo, body };
  }

  async bindTemporaryAuthKey() {
    assert.ok(this.permanentCryptoState && this.cryptoState, 'both permanent and temporary auth keys exist');
    const nonceBytes = randomBytes(8);
    const nonce = nonceBytes.readBigInt64LE(0);
    const expiresAt = Math.floor(Date.now() / 1000) + 3600;
    const innerBody = new TLWriter()
      .uint32(C.bindAuthKeyInner)
      .fixed(nonceBytes)
      .fixed(this.cryptoState.authKeyId)
      .fixed(this.permanentCryptoState.authKeyId)
      .fixed(this.cryptoState.sessionId)
      .int32(expiresAt)
      .toBuffer();
    const innerMessageId = makeMessageId();
    const innerBase = new TLWriter()
      .fixed(randomBytes(8))
      .fixed(randomBytes(8))
      .int64(innerMessageId)
      .int32(0)
      .int32(innerBody.length)
      .fixed(innerBody)
      .toBuffer();
    const innerPadding = (16 - (innerBase.length % 16)) % 16;
    const innerPlaintext = Buffer.concat([innerBase, randomBytes(innerPadding)]);
    const innerEncrypted = encryptMtproto1(this.permanentCryptoState.authKey, innerPlaintext, innerBody.length, 0);
    const encryptedMessage = Buffer.concat([
      this.permanentCryptoState.authKeyId,
      innerEncrypted.messageKey,
      innerEncrypted.ciphertext,
    ]);
    const bindBody = new TLWriter()
      .uint32(C.authBindTempAuthKey)
      .fixed(this.permanentCryptoState.authKeyId)
      .fixed(nonceBytes)
      .int32(expiresAt)
      .bytes(encryptedMessage)
      .toBuffer();

    const requestMsgId = this.sendEncryptedRequest(bindBody);
    let sawSession = false;
    for (let i = 0; i < 3; i += 1) {
      const response = await this.readEncryptedResponse();
      const reader = new TLReader(response.body);
      const constructor = reader.uint32();
      if (constructor === C.newSessionCreated) {
        assert.equal(reader.int64(), requestMsgId);
        reader.fixed(8);
        assert.ok(reader.fixed(8).equals(this.cryptoState.serverSalt));
        reader.expectEnd();
        sawSession = true;
        continue;
      }
      if (constructor === C.rpcResult) {
        assert.equal(reader.int64(), requestMsgId);
        assert.equal(reader.uint32(), C.boolTrue, 'server accepted the authenticated temp-key binding');
        reader.expectEnd();
        return { requestMsgId, sawSession };
      }
      throw new Error(`Unexpected auth.bindTempAuthKey response 0x${constructor.toString(16)}`);
    }
    throw new Error('No rpc_result received for auth.bindTempAuthKey');
  }

  async requestGetConfig() {
    const requestMsgId = this.sendEncryptedRequest(buildInitConfigQuery());
    let sawSession = false;
    for (let i = 0; i < 3; i += 1) {
      const response = await this.readEncryptedResponse();
      const reader = new TLReader(response.body);
      const constructor = reader.uint32();
      if (constructor === C.newSessionCreated) {
        assert.equal(reader.int64(), requestMsgId);
        reader.fixed(8); // unique_id
        assert.ok(reader.fixed(8).equals(this.cryptoState.serverSalt));
        reader.expectEnd();
        sawSession = true;
        continue;
      }
      if (constructor === C.rpcResult) {
        assert.equal(reader.int64(), requestMsgId);
        const config = parseConfig(reader.buffer.subarray(reader.offset));
        return { requestMsgId, sawSession, config };
      }
      throw new Error(`Unexpected encrypted response constructor 0x${constructor.toString(16)}`);
    }
    throw new Error('No rpc_result received for help.getConfig');
  }

  async requestPing(pingId = 0x102030405060708n) {
    const requestMsgId = this.sendEncryptedRequest(new TLWriter().uint32(C.ping).int64(pingId).toBuffer(), undefined, 1);
    const response = await this.readEncryptedResponse();
    const reader = new TLReader(response.body);
    assert.equal(reader.uint32(), C.rpcResult);
    assert.equal(reader.int64(), requestMsgId);
    assert.equal(reader.uint32(), C.pong);
    assert.equal(reader.int64(), requestMsgId);
    assert.equal(reader.int64(), pingId);
    reader.expectEnd();
    return response;
  }

  async requestFutureSalts(count = 2) {
    const body = new TLWriter().uint32(C.getFutureSalts).int32(count).toBuffer();
    const requestMsgId = this.sendEncryptedRequest(body);
    const response = await this.readEncryptedResponse();
    const rpc = new TLReader(response.body);
    assert.equal(rpc.uint32(), C.rpcResult);
    assert.equal(rpc.int64(), requestMsgId);
    const salts = new TLReader(rpc.buffer.subarray(rpc.offset));
    assert.equal(salts.uint32(), C.futureSalts);
    assert.equal(salts.int64(), requestMsgId);
    const now = salts.int32();
    assert.equal(salts.uint32(), VECTOR_CONSTRUCTOR);
    const saltCount = salts.int32();
    assert.equal(saltCount, count);
    const values = [];
    for (let i = 0; i < saltCount; i += 1) {
      assert.equal(salts.uint32(), C.futureSalt);
      const validSince = salts.int32();
      const validUntil = salts.int32();
      const salt = Buffer.from(salts.fixed(8));
      assert.ok(validSince <= now && validUntil > now);
      assert.ok(salt.equals(this.cryptoState.serverSalt), 'future salt matches the active session salt');
      values.push({ validSince, validUntil, salt });
    }
    salts.expectEnd();
    return { requestMsgId, now, values };
  }

  destroy() {
    if (this.cryptoState?.authKey) this.cryptoState.authKey.fill(0);
    if (this.permanentCryptoState?.authKey) this.permanentCryptoState.authKey.fill(0);
    this.socket.destroy();
  }
}
