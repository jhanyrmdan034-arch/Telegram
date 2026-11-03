import { decryptMtproto1 } from './crypto.mjs';
import { TLReader, TLWriter } from './tl.mjs';

const AUTH_BIND_TEMP_AUTH_KEY = 0xcdd42a05;
const BIND_AUTH_KEY_INNER = 0x75a3f765;
const BOOL_TRUE = 0x997275b5;

export function isBindTempAuthKey(body) {
  const value = Buffer.from(body);
  return value.length >= 4 && value.readUInt32LE(0) === AUTH_BIND_TEMP_AUTH_KEY;
}

export function handleBindTempAuthKey(body, state) {
  if (!isBindTempAuthKey(body)) return null;
  const reader = new TLReader(body);
  reader.uint32();
  if (!state.permanentAuthKey || !state.permanentAuthKeyId || !state.authKey || !state.authKeyId) {
    throw new Error('Auth-key binding requested before both keys exist');
  }

  const permanentId = reader.fixed(8);
  const nonce = reader.int64();
  const expiresAt = reader.int32();
  const encryptedMessage = reader.bytes(4096);
  reader.expectEnd();
  if (!permanentId.equals(state.permanentAuthKeyId)) throw new Error('Permanent auth-key ID mismatch');
  const now = Math.floor(Date.now() / 1000);
  if (!Number.isInteger(expiresAt) || expiresAt <= now) throw new Error('Expired temporary auth key');
  if (state.tempAuthExpiresIn) {
    const remaining = expiresAt - now;
    if (Math.abs(remaining - state.tempAuthExpiresIn) > 300) throw new Error('Temporary auth-key expiry does not match the handshake');
  }

  const innerPacket = decryptMtproto1(state.permanentAuthKey, encryptedMessage, 0);
  if (innerPacket.seqNo !== 0) throw new Error('Unexpected MTProto 1.0 bind-message sequence number');
  const inner = new TLReader(innerPacket.body);
  if (inner.uint32() !== BIND_AUTH_KEY_INNER) throw new Error('Invalid bind_auth_key_inner constructor');
  const innerNonce = inner.int64();
  const temporaryId = inner.fixed(8);
  const innerPermanentId = inner.fixed(8);
  const temporarySessionId = inner.fixed(8);
  const innerExpiresAt = inner.int32();
  inner.expectEnd();

  if (innerNonce !== nonce) throw new Error('Temp auth-key binding nonce mismatch');
  if (!temporaryId.equals(state.authKeyId)) throw new Error('Temporary auth-key ID mismatch');
  if (!innerPermanentId.equals(state.permanentAuthKeyId)) throw new Error('Inner permanent auth-key ID mismatch');
  if (!state.sessionId || !temporarySessionId.equals(state.sessionId)) throw new Error('Temporary session ID mismatch');
  if (innerExpiresAt !== expiresAt) throw new Error('Temporary auth-key expiry mismatch');

  state.tempAuthBound = true;
  state.tempAuthExpiresAt = expiresAt;
  return new TLWriter().uint32(BOOL_TRUE).toBuffer();
}
