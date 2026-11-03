import { randomBytes } from 'node:crypto';
import { decryptMtproto, encryptMtproto } from './crypto.mjs';
import { TLReader, TLWriter } from './tl.mjs';

export const MTProto = Object.freeze({
  msgContainer: 0x73f1f8dc,
  msgsAck: 0x62d6b459,
  newSessionCreated: 0x9ec20908,
});

export function nextMessageId() {
  const seconds = BigInt(Math.floor(Date.now() / 1000));
  const low = BigInt(randomBytes(4).readUInt32LE(0) & 0xfffffffc);
  return (seconds << 32n) | low;
}

export function makePlainPacket(body, msgId = nextMessageId()) {
  const payload = Buffer.from(body);
  return new TLWriter()
    .fixed(Buffer.alloc(8))
    .int64(msgId)
    .int32(payload.length)
    .fixed(payload)
    .toBuffer();
}

export function parsePlainPacket(packet) {
  const reader = new TLReader(packet);
  const authKeyId = reader.fixed(8);
  if (!authKeyId.equals(Buffer.alloc(8))) throw new Error('Expected an unencrypted MTProto packet');
  const msgId = reader.int64();
  const length = reader.int32();
  if (length < 4 || length > 1024 * 1024) throw new Error('Invalid unencrypted MTProto body length');
  const body = reader.fixed(length);
  reader.expectEnd();
  return { msgId, body };
}

export function makeAbridgedFrame(packet) {
  const payload = Buffer.from(packet);
  if (payload.length % 4 !== 0) throw new RangeError('Abridged MTProto packets must be word aligned');
  const words = payload.length / 4;
  let header;
  if (words < 0x7f) {
    header = Buffer.from([words]);
  } else {
    if (words > 0xffffff) throw new RangeError('Abridged packet exceeds 24-bit word length');
    header = Buffer.from([0x7f, words & 0xff, (words >>> 8) & 0xff, (words >>> 16) & 0xff]);
  }
  return Buffer.concat([header, payload]);
}

export function makeEncryptedPacket(state, body, msgId = nextMessageId(), seqNo = 1, padding = undefined) {
  if (!state.authKey || !state.authKeyId || !state.serverSalt || !state.sessionId) {
    throw new Error('MTProto session is not established');
  }
  const payload = Buffer.from(body);
  const base = new TLWriter()
    .fixed(state.serverSalt, 8)
    .fixed(state.sessionId, 8)
    .int64(msgId)
    .int32(seqNo)
    .int32(payload.length)
    .fixed(payload)
    .toBuffer();
  const minAlignedPadding = (16 - (base.length % 16)) % 16;
  const paddingLength = padding ?? (minAlignedPadding + 32);
  if (paddingLength < 12 || paddingLength > 1024 || (base.length + paddingLength) % 16 !== 0) {
    throw new RangeError('Invalid MTProto 2.0 padding length');
  }
  const plaintext = Buffer.concat([base, randomBytes(paddingLength)]);
  const { msgKey, ciphertext } = encryptMtproto(state.authKey, plaintext, 8);
  return Buffer.concat([state.authKeyId, msgKey, ciphertext]);
}

export function parseEncryptedPacket(state, packet) {
  const bytes = Buffer.from(packet);
  if (bytes.length < 56 || (bytes.length - 24) % 16 !== 0) throw new Error('Invalid encrypted MTProto packet size');
  const authKeyId = bytes.subarray(0, 8);
  if (!authKeyId.equals(state.authKeyId)) throw new Error('Unknown auth_key_id');
  const msgKey = bytes.subarray(8, 24);
  const plaintext = decryptMtproto(state.authKey, msgKey, bytes.subarray(24), 0);
  const reader = new TLReader(plaintext);
  const salt = reader.fixed(8);
  const sessionId = reader.fixed(8);
  const msgId = reader.int64();
  const seqNo = reader.int32();
  const bodyLength = reader.int32();
  if (bodyLength < 4 || bodyLength > 1024 * 1024 || bodyLength > reader.remaining) {
    throw new Error('Invalid encrypted MTProto body length');
  }
  const body = reader.fixed(bodyLength);
  const paddingLength = reader.remaining;
  if (paddingLength < 12 || paddingLength > 1024) throw new Error('Invalid MTProto padding');
  return { salt, sessionId, msgId, seqNo, body, paddingLength };
}

export function parseMessageContainer(body) {
  const input = Buffer.from(body);
  const reader = new TLReader(input);
  if (reader.uint32() !== MTProto.msgContainer) return [{ msgId: null, seqNo: null, body: input }];
  const count = reader.int32();
  if (count < 0 || count > 100) throw new Error('Invalid MTProto message-container count');
  const messages = [];
  for (let i = 0; i < count; i += 1) {
    const msgId = reader.int64();
    const seqNo = reader.int32();
    const length = reader.int32();
    if (length < 4 || length > 1024 * 1024) throw new Error('Invalid inner MTProto message length');
    messages.push({ msgId, seqNo, body: reader.fixed(length) });
  }
  reader.expectEnd();
  return messages;
}

export function makeNewSessionCreated(firstMessageId, uniqueId, serverSalt) {
  return new TLWriter()
    .uint32(MTProto.newSessionCreated)
    .int64(firstMessageId)
    .fixed(uniqueId, 8)
    .fixed(serverSalt, 8)
    .toBuffer();
}

export function makeRpcResult(requestMessageId, result) {
  return new TLWriter()
    .uint32(0xf35c6d01)
    .int64(requestMessageId)
    .fixed(result)
    .toBuffer();
}
