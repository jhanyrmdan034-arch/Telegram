import {
  createCipheriv,
  createDecipheriv,
  createHash,
  createPublicKey,
} from 'node:crypto';
import { TLWriter } from './tl.mjs';

export const TELEGRAM_CLIENT_DH_PRIME = BigInt(
  '0xc71caeb9c6b1c9048e6c522f70f13f73980d40238e3e21c14934d037563d930f' +
  '48198a0aa7c14058229493d22530f4dbfa336f6e0ac925139543aed44cce7c372' +
  '0fd51f69458705ac68cd4fe6b6b13abdc9746512969328454f18faf8c595f642' +
  '477fe96bb2a941d5bcd1d4ac8cc49880708fa9b378e3c4f3a9060bee67cf9a4a' +
  '4a695811051907e162753b56b0f6b410dba74d8a84b2a14b3144e0ef1284754f' +
  'd17ed950d5965b4b9dd46582db1178d169c6bc465b0d6ff9ca3928fef5b9ae4e' +
  '418fc15e83ebea0f87fa9ff5eed70050ded2849f47bf959d956850ce929851f0' +
  'd8115f635b105ee2e4e15d04b2454bf6f4fadf034b10403119cd8e3b92fcc5b',
);
export const TELEGRAM_CLIENT_DH_GENERATOR = 3;

export function sha1(value) {
  return createHash('sha1').update(value).digest();
}

export function sha256(value) {
  return createHash('sha256').update(value).digest();
}

export function xorBuffers(left, right) {
  if (left.length !== right.length) throw new RangeError('XOR operands must have equal length');
  const out = Buffer.allocUnsafe(left.length);
  for (let i = 0; i < left.length; i += 1) out[i] = left[i] ^ right[i];
  return out;
}

export function toBigInt(buffer) {
  const hex = Buffer.from(buffer).toString('hex');
  return hex.length ? BigInt(`0x${hex}`) : 0n;
}

export function fromBigInt(value, length) {
  let hex = BigInt(value).toString(16);
  if (hex.length & 1) hex = `0${hex}`;
  const raw = Buffer.from(hex, 'hex');
  if (raw.length > length) throw new RangeError('Integer does not fit requested byte length');
  return Buffer.concat([Buffer.alloc(length - raw.length), raw]);
}

export function modPow(base, exponent, modulus) {
  let result = 1n;
  let factor = BigInt(base) % BigInt(modulus);
  let power = BigInt(exponent);
  const mod = BigInt(modulus);
  while (power > 0n) {
    if (power & 1n) result = (result * factor) % mod;
    factor = (factor * factor) % mod;
    power >>= 1n;
  }
  return result;
}

function ecbTransform(data, key, decrypt) {
  if (data.length % 16 !== 0) throw new RangeError('AES-ECB input must be block aligned');
  const transform = decrypt
    ? createDecipheriv('aes-256-ecb', key, null)
    : createCipheriv('aes-256-ecb', key, null);
  transform.setAutoPadding(false);
  return Buffer.concat([transform.update(data), transform.final()]);
}

export function aesIge(data, key, iv, decrypt = false) {
  const input = Buffer.from(data);
  const aesKey = Buffer.from(key);
  const initialIv = Buffer.from(iv);
  if (aesKey.length !== 32 || initialIv.length !== 32 || input.length % 16 !== 0) {
    throw new RangeError('AES-IGE requires a 32-byte key, 32-byte IV and block-aligned input');
  }

  const out = Buffer.allocUnsafe(input.length);
  let previousCipher = Buffer.from(initialIv.subarray(0, 16));
  let previousPlain = Buffer.from(initialIv.subarray(16, 32));

  for (let offset = 0; offset < input.length; offset += 16) {
    const block = input.subarray(offset, offset + 16);
    if (!decrypt) {
      const mixed = xorBuffers(block, previousPlain);
      const encrypted = ecbTransform(mixed, aesKey, false);
      const cipherBlock = xorBuffers(encrypted, previousCipher);
      cipherBlock.copy(out, offset);
      previousCipher = cipherBlock;
      previousPlain = Buffer.from(block);
    } else {
      const mixed = xorBuffers(block, previousCipher);
      const decrypted = ecbTransform(mixed, aesKey, true);
      const plainBlock = xorBuffers(decrypted, previousPlain);
      plainBlock.copy(out, offset);
      previousCipher = Buffer.from(block);
      previousPlain = plainBlock;
    }
  }
  return out;
}

export function deriveTmpAesKeyIv(newNonce, serverNonce) {
  const nonce = Buffer.from(newNonce);
  const server = Buffer.from(serverNonce);
  if (nonce.length !== 32 || server.length !== 16) throw new RangeError('Invalid handshake nonce sizes');
  const a = sha1(Buffer.concat([nonce, server]));
  const b = sha1(Buffer.concat([server, nonce]));
  const c = sha1(Buffer.concat([nonce, nonce]));
  return {
    key: Buffer.concat([a, b.subarray(0, 12)]),
    iv: Buffer.concat([b.subarray(12, 20), c, nonce.subarray(0, 4)]),
  };
}

export function rsaPublicFingerprint(publicKeyOrPem) {
  const publicKey = typeof publicKeyOrPem === 'string' || Buffer.isBuffer(publicKeyOrPem)
    ? createPublicKey(publicKeyOrPem)
    : publicKeyOrPem;
  const jwk = publicKey.export({ format: 'jwk' });
  const modulus = Buffer.from(jwk.n, 'base64url');
  const exponent = Buffer.from(jwk.e, 'base64url');
  const serialized = new TLWriter().bytes(modulus).bytes(exponent).toBuffer();
  return sha1(serialized).subarray(12, 20);
}

function deriveMessageAesV2(authKey, msgKey, x) {
  const key = Buffer.from(authKey);
  const messageKey = Buffer.from(msgKey);
  const a = sha256(Buffer.concat([messageKey, key.subarray(x, x + 36)]));
  const b = sha256(Buffer.concat([key.subarray(40 + x, 76 + x), messageKey]));
  return {
    key: Buffer.concat([a.subarray(0, 8), b.subarray(8, 24), a.subarray(24, 32)]),
    iv: Buffer.concat([b.subarray(0, 8), a.subarray(8, 24), b.subarray(24, 32)]),
  };
}

function deriveMessageAesV1(authKey, msgKey, x) {
  const key = Buffer.from(authKey);
  const messageKey = Buffer.from(msgKey);
  const a = sha1(Buffer.concat([messageKey, key.subarray(x, x + 32)]));
  const b = sha1(Buffer.concat([key.subarray(32 + x, 48 + x), messageKey, key.subarray(48 + x, 64 + x)]));
  const c = sha1(Buffer.concat([key.subarray(64 + x, 96 + x), messageKey]));
  const d = sha1(Buffer.concat([messageKey, key.subarray(96 + x, 128 + x)]));
  return {
    key: Buffer.concat([a.subarray(0, 8), b.subarray(8, 20), c.subarray(4, 16)]),
    iv: Buffer.concat([a.subarray(8, 20), b.subarray(0, 8), c.subarray(16, 20), d.subarray(0, 8)]),
  };
}

export function encryptMtproto(authKey, plaintext, directionX = 8) {
  const key = Buffer.from(authKey);
  const data = Buffer.from(plaintext);
  if (key.length !== 256 || data.length < 32 || data.length % 16 !== 0) {
    throw new RangeError('Invalid MTProto 2.0 auth key or plaintext size');
  }
  const msgKey = sha256(Buffer.concat([key.subarray(88 + directionX, 120 + directionX), data])).subarray(8, 24);
  const aes = deriveMessageAesV2(key, msgKey, directionX);
  return { msgKey, ciphertext: aesIge(data, aes.key, aes.iv, false) };
}

export function decryptMtproto(authKey, msgKey, ciphertext, directionX = 0) {
  const key = Buffer.from(authKey);
  const messageKey = Buffer.from(msgKey);
  const encrypted = Buffer.from(ciphertext);
  if (key.length !== 256 || messageKey.length !== 16 || encrypted.length < 32 || encrypted.length % 16 !== 0) {
    throw new RangeError('Invalid MTProto 2.0 encrypted packet');
  }
  const aes = deriveMessageAesV2(key, messageKey, directionX);
  const plaintext = aesIge(encrypted, aes.key, aes.iv, true);
  const expected = sha256(Buffer.concat([key.subarray(88 + directionX, 120 + directionX), plaintext])).subarray(8, 24);
  if (!expected.equals(messageKey)) throw new Error('MTProto msg_key verification failed');
  return plaintext;
}

export function encryptMtproto1(authKey, plaintext, messageLength, directionX = 0) {
  const key = Buffer.from(authKey);
  const data = Buffer.from(plaintext);
  if (key.length !== 256 || data.length < 32 || data.length % 16 !== 0) {
    throw new RangeError('Invalid MTProto 1.0 auth key or plaintext size');
  }
  const messageKey = sha1(data.subarray(0, 32 + messageLength)).subarray(4, 20);
  const aes = deriveMessageAesV1(key, messageKey, directionX);
  return { messageKey, ciphertext: aesIge(data, aes.key, aes.iv, false) };
}

export function decryptMtproto1(authKey, packet, directionX = 0) {
  const key = Buffer.from(authKey);
  const bytes = Buffer.from(packet);
  if (key.length !== 256 || bytes.length < 56 || (bytes.length - 24) % 16 !== 0) {
    throw new RangeError('Invalid MTProto 1.0 encrypted packet');
  }
  const authKeyId = sha1(key).subarray(12, 20);
  if (!bytes.subarray(0, 8).equals(authKeyId)) throw new Error('MTProto 1.0 auth_key_id mismatch');
  const messageKey = bytes.subarray(8, 24);
  const aes = deriveMessageAesV1(key, messageKey, directionX);
  const plaintext = aesIge(bytes.subarray(24), aes.key, aes.iv, true);
  const reader = new DataView(plaintext.buffer, plaintext.byteOffset, plaintext.byteLength);
  const messageLength = reader.getInt32(28, true);
  if (messageLength < 4 || 32 + messageLength > plaintext.length) throw new Error('Invalid MTProto 1.0 message length');
  const paddingLength = plaintext.length - (32 + messageLength);
  if (paddingLength < 0 || paddingLength > 15) throw new Error('Invalid MTProto 1.0 padding');
  const expected = sha1(plaintext.subarray(0, 32 + messageLength)).subarray(4, 20);
  if (!expected.equals(messageKey)) throw new Error('MTProto 1.0 msg_key verification failed');
  return {
    salt: plaintext.subarray(0, 8),
    sessionId: plaintext.subarray(8, 16),
    msgId: plaintext.readBigInt64LE(16),
    seqNo: plaintext.readInt32LE(24),
    body: plaintext.subarray(32, 32 + messageLength),
  };
}

export function createCtr(key, iv, decrypt = false) {
  return decrypt
    ? createDecipheriv('aes-256-ctr', key, iv)
    : createCipheriv('aes-256-ctr', key, iv);
}
