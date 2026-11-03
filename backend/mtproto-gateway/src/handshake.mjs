import {
  privateDecrypt,
  constants as cryptoConstants,
  randomBytes,
} from 'node:crypto';
import {
  TELEGRAM_CLIENT_DH_GENERATOR,
  TELEGRAM_CLIENT_DH_PRIME,
  aesIge,
  deriveTmpAesKeyIv,
  fromBigInt,
  modPow,
  rsaPublicFingerprint,
  sha1,
  sha256,
  toBigInt,
  xorBuffers,
} from './crypto.mjs';
import { TLReader, TLWriter } from './tl.mjs';

const CONSTRUCTOR = Object.freeze({
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
  msgsAck: 0x62d6b459,
});

const PQ_P = 65537n;
const PQ_Q = 65539n;
const PQ = PQ_P * PQ_Q;
const DC_ID = 1;

function messageId() {
  const seconds = BigInt(Math.floor(Date.now() / 1000));
  return (seconds << 32n) & ~3n;
}

function encodeResPq(session, publicFingerprint) {
  const writer = new TLWriter()
    .uint32(CONSTRUCTOR.resPq)
    .fixed(session.clientNonce, 16)
    .fixed(session.serverNonce, 16)
    .bytes(fromBigInt(PQ, 8))
    .uint32(0x1cb5c415)
    .int32(1)
    .fixed(publicFingerprint, 8);
  return writer.toBuffer();
}

function decryptPqInner(privateKey, encryptedData) {
  if (encryptedData.length !== 256) throw new Error('Invalid RSA handshake block size');
  const decoded = privateDecrypt({
    key: privateKey,
    padding: cryptoConstants.RSA_NO_PADDING,
  }, encryptedData);
  if (decoded.length !== 256) throw new Error('Invalid RSA handshake plaintext');

  const maskedKey = decoded.subarray(0, 32);
  const encryptedInner = decoded.subarray(32);
  const key = xorBuffers(maskedKey, sha256(encryptedInner));
  const reversedWithHash = aesIge(encryptedInner, key, Buffer.alloc(32), true);
  const forwardPadded = Buffer.from(reversedWithHash.subarray(0, 192)).reverse();
  const expectedHash = reversedWithHash.subarray(192, 224);
  if (!sha256(Buffer.concat([key, forwardPadded])).equals(expectedHash)) {
    throw new Error('RSA inner-data digest mismatch');
  }

  const reader = new TLReader(forwardPadded);
  const innerConstructor = reader.uint32();
  let role;
  if (innerConstructor === CONSTRUCTOR.pqInnerDataDc) role = 'permanent';
  else if (innerConstructor === CONSTRUCTOR.pqInnerDataTempDc) role = 'temporary';
  else throw new Error('Unexpected P_Q_inner_data constructor');
  const pq = reader.bytes(8);
  const p = reader.bytes(4);
  const q = reader.bytes(4);
  const nonce = reader.fixed(16);
  const serverNonce = reader.fixed(16);
  const newNonce = reader.fixed(32);
  const dc = reader.int32();
  const expiresIn = role === 'temporary' ? reader.int32() : null;
  return { role, pq, p, q, nonce, serverNonce, newNonce, dc, expiresIn };
}

function encodeServerDhInner(session, gA) {
  const inner = new TLWriter()
    .uint32(CONSTRUCTOR.serverDhInnerData)
    .fixed(session.clientNonce, 16)
    .fixed(session.serverNonce, 16)
    .int32(TELEGRAM_CLIENT_DH_GENERATOR)
    .bytes(fromBigInt(TELEGRAM_CLIENT_DH_PRIME, 256))
    .bytes(gA)
    .int32(Math.floor(Date.now() / 1000))
    .toBuffer();
  const padding = (16 - ((20 + inner.length) % 16)) % 16;
  const hashAndBody = Buffer.concat([sha1(inner), inner, randomBytes(padding)]);
  const aes = deriveTmpAesKeyIv(session.newNonce, session.serverNonce);
  return aesIge(hashAndBody, aes.key, aes.iv, false);
}

function decryptClientDhInner(session, encryptedData) {
  if (!encryptedData.length || encryptedData.length % 16 !== 0) throw new Error('Invalid encrypted client DH block');
  const aes = deriveTmpAesKeyIv(session.newNonce, session.serverNonce);
  const hashAndBody = aesIge(encryptedData, aes.key, aes.iv, true);
  if (hashAndBody.length < 20) throw new Error('Truncated client DH inner data');

  let bodyEnd = -1;
  for (let padding = 0; padding <= 15 && 20 + padding <= hashAndBody.length; padding += 1) {
    const candidateEnd = hashAndBody.length - padding;
    if (sha1(hashAndBody.subarray(20, candidateEnd)).equals(hashAndBody.subarray(0, 20))) {
      bodyEnd = candidateEnd;
      break;
    }
  }
  if (bodyEnd < 0) throw new Error('Client DH inner-data digest mismatch');

  const reader = new TLReader(hashAndBody.subarray(20, bodyEnd));
  if (reader.uint32() !== CONSTRUCTOR.clientDhInnerData) throw new Error('Unexpected client_DH_inner_data constructor');
  const nonce = reader.fixed(16);
  const serverNonce = reader.fixed(16);
  const retryId = reader.int64();
  const gB = reader.bytes(256);
  reader.expectEnd();
  return { nonce, serverNonce, retryId, gB };
}

function validateNonce(expected, actual, label) {
  if (!Buffer.from(expected).equals(Buffer.from(actual))) throw new Error(`${label} mismatch`);
}

export function createHandshakeState(privateKey, publicKey) {
  return {
    stage: 'await_req_pq',
    privateKey,
    publicFingerprint: rsaPublicFingerprint(publicKey),
    clientNonce: null,
    serverNonce: null,
    newNonce: null,
    serverPrivateExponent: null,
    gA: null,
    nextKeyRole: 'permanent',
    pendingKeyRole: null,
    permanentAuthKey: null,
    permanentAuthKeyId: null,
    authKey: null,
    authKeyId: null,
    serverSalt: null,
    tempAuthExpiresIn: null,
    tempAuthBound: false,
    tempAuthExpiresAt: null,
    sessionId: null,
    firstEncryptedMessageId: null,
    uniqueId: randomBytes(8),
    serverSeqNo: 0,
    lastClientMessageId: null,
  };
}

export function handlePlainHandshake(state, body) {
  const reader = new TLReader(body);
  const constructor = reader.uint32();

  if (constructor === CONSTRUCTOR.msgsAck) {
    // Acknowledgements can be emitted between handshake packets. They carry no
    // credentials and do not need a response for this narrow prototype.
    return null;
  }

  if (constructor === CONSTRUCTOR.reqPqMulti) {
    if (state.stage !== 'await_req_pq') throw new Error('Unexpected req_pq_multi');
    state.clientNonce = reader.fixed(16);
    reader.expectEnd();
    state.pendingKeyRole = state.nextKeyRole;
    state.serverNonce = randomBytes(16);
    state.stage = 'await_req_dh';
    return encodeResPq(state, state.publicFingerprint);
  }

  if (constructor === CONSTRUCTOR.reqDhParams) {
    if (state.stage !== 'await_req_dh') throw new Error('Unexpected req_DH_params');
    const nonce = reader.fixed(16);
    const serverNonce = reader.fixed(16);
    const p = reader.bytes(4);
    const q = reader.bytes(4);
    const fingerprint = reader.fixed(8);
    const encryptedData = reader.bytes(512);
    reader.expectEnd();
    validateNonce(state.clientNonce, nonce, 'client nonce');
    validateNonce(state.serverNonce, serverNonce, 'server nonce');
    if (!fingerprint.equals(state.publicFingerprint)) throw new Error('Unknown RSA public-key fingerprint');

    const inner = decryptPqInner(state.privateKey, encryptedData);
    validateNonce(state.clientNonce, inner.nonce, 'inner client nonce');
    validateNonce(state.serverNonce, inner.serverNonce, 'inner server nonce');
    if (inner.role !== state.pendingKeyRole) throw new Error('Unexpected auth-key role in handshake');
    if (inner.dc !== DC_ID) throw new Error('Unsupported DC id');
    if (inner.role === 'temporary') {
      if (!Number.isInteger(inner.expiresIn) || inner.expiresIn <= 0 || inner.expiresIn > 30 * 24 * 60 * 60) {
        throw new Error('Invalid temporary auth-key expiry');
      }
      state.tempAuthExpiresIn = inner.expiresIn;
    }
    if (!inner.p.equals(p) || !inner.q.equals(q) || toBigInt(p) * toBigInt(q) !== toBigInt(inner.pq)) {
      throw new Error('Invalid p, q or pq values');
    }

    state.newNonce = Buffer.from(inner.newNonce);
    state.serverPrivateExponent = toBigInt(randomBytes(256)) % (TELEGRAM_CLIENT_DH_PRIME - 3n) + 2n;
    state.gA = modPow(BigInt(TELEGRAM_CLIENT_DH_GENERATOR), state.serverPrivateExponent, TELEGRAM_CLIENT_DH_PRIME);
    const encryptedAnswer = encodeServerDhInner(state, fromBigInt(state.gA, 256));
    state.stage = 'await_set_client_dh';

    return new TLWriter()
      .uint32(CONSTRUCTOR.serverDhParamsOk)
      .fixed(state.clientNonce, 16)
      .fixed(state.serverNonce, 16)
      .bytes(encryptedAnswer)
      .toBuffer();
  }

  if (constructor === CONSTRUCTOR.setClientDhParams) {
    if (state.stage !== 'await_set_client_dh') throw new Error('Unexpected set_client_DH_params');
    const nonce = reader.fixed(16);
    const serverNonce = reader.fixed(16);
    const encryptedData = reader.bytes(2048);
    reader.expectEnd();
    validateNonce(state.clientNonce, nonce, 'client nonce');
    validateNonce(state.serverNonce, serverNonce, 'server nonce');

    const inner = decryptClientDhInner(state, encryptedData);
    validateNonce(state.clientNonce, inner.nonce, 'inner client nonce');
    validateNonce(state.serverNonce, inner.serverNonce, 'inner server nonce');
    const clientPublic = toBigInt(inner.gB);
    const minPublic = 1n << 1984n;
    if (clientPublic < minPublic || clientPublic >= TELEGRAM_CLIENT_DH_PRIME - minPublic) {
      throw new Error('Client DH public value is outside the accepted range');
    }

    const derivedAuthKey = fromBigInt(modPow(clientPublic, state.serverPrivateExponent, TELEGRAM_CLIENT_DH_PRIME), 256);
    const authSha1 = sha1(derivedAuthKey);
    const derivedAuthKeyId = authSha1.subarray(12, 20);
    const newNonceHash1 = sha1(Buffer.concat([state.newNonce, Buffer.from([1]), authSha1.subarray(0, 8)])).subarray(4, 20);
    if (state.pendingKeyRole === 'permanent') {
      state.permanentAuthKey = derivedAuthKey;
      state.permanentAuthKeyId = Buffer.from(derivedAuthKeyId);
      state.nextKeyRole = 'temporary';
      state.pendingKeyRole = null;
      state.authKey = null;
      state.authKeyId = null;
      state.stage = 'await_req_pq';
    } else {
      state.authKey = derivedAuthKey;
      state.authKeyId = Buffer.from(derivedAuthKeyId);
      state.serverSalt = xorBuffers(state.newNonce.subarray(0, 8), state.serverNonce.subarray(0, 8));
      state.nextKeyRole = 'complete';
      state.pendingKeyRole = null;
      state.stage = 'established';
    }

    return new TLWriter()
      .uint32(CONSTRUCTOR.dhGenOk)
      .fixed(state.clientNonce, 16)
      .fixed(state.serverNonce, 16)
      .fixed(newNonceHash1, 16)
      .toBuffer();
  }

  throw new Error(`Unsupported unencrypted MTProto constructor 0x${constructor.toString(16)}`);
}

export const HANDSHAKE_CONSTRUCTORS = CONSTRUCTOR;
export const HANDSHAKE_DC_ID = DC_ID;
