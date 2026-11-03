import assert from 'node:assert/strict';
import { generateKeyPairSync } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { MtprotoGateway } from '../src/server.mjs';
import { WireTestClient } from './wire-client.mjs';

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const sourceDirectory = path.resolve(testDirectory, '../src');

function listSourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(directory, entry.name);
    return entry.isDirectory() ? listSourceFiles(fullPath) : [fullPath];
  });
}

test('loopback transport, PFS handshakes, auth-key binding and Layer 232 base requests', async (t) => {
  const { privateKey, publicKey } = generateKeyPairSync('rsa', {
    modulusLength: 2048,
    publicExponent: 0x10001,
  });
  const gateway = new MtprotoGateway({
    privateKey,
    publicKey,
    host: '127.0.0.1',
    port: 0,
    advertiseHost: '127.0.0.1',
    advertisePort: 0,
  });
  const address = await gateway.listen();
  const client = await WireTestClient.connect({ ...address, publicKey });
  t.after(async () => {
    client.destroy();
    await gateway.close();
  });

  assert.equal(client.remoteAddress, '127.0.0.1', 'the test client connects only to loopback');

  const permanentHandshake = await client.performHandshake('permanent');
  assert.equal(permanentHandshake.authKeyId.length, 8);
  assert.equal(permanentHandshake.serverSalt.length, 8);
  let metrics = gateway.snapshot();
  assert.equal(metrics.handshakesSucceeded, 0, 'permanent DH key is prepared before the PFS session');

  const temporaryHandshake = await client.performHandshake('temporary');
  assert.equal(temporaryHandshake.authKeyId.length, 8);
  assert.equal(temporaryHandshake.serverSalt.length, 8);
  metrics = gateway.snapshot();
  assert.equal(metrics.handshakesSucceeded, 1, 'temporary DH exchange completed and server registered a session');
  assert.equal(metrics.activeSessions, 1, 'session remains associated with the open TCP connection');

  const binding = await client.bindTemporaryAuthKey();
  assert.equal(binding.sawSession, true, 'server announced the encrypted MTProto session');
  const configResult = await client.requestGetConfig();
  assert.equal(configResult.sawSession, false, 'session announcement was already received during key binding');
  assert.equal(configResult.config.flags, 0);
  assert.equal(configResult.config.thisDc, 1);
  assert.deepEqual(configResult.config.dcOptions[0], {
    id: 1,
    ip: '127.0.0.1',
    port: address.port,
    flags: 16,
  });

  await client.requestPing();
  const futureSalts = await client.requestFutureSalts(2);
  assert.equal(futureSalts.values.length, 2);
  metrics = gateway.snapshot();
  assert.equal(metrics.layer232Requests, 1, 'invokeWithLayer(232) reached help.getConfig');
  assert.equal(metrics.baseRequests, 4, 'key binding, help.getConfig, ping and future salts received base responses');
  assert.equal(metrics.encryptedPacketsReceived, 4);
  assert.ok(metrics.encryptedPacketsSent >= 5, 'encrypted session and RPC responses were emitted');
  assert.equal(metrics.protocolErrors, 0);

  const serverSources = listSourceFiles(sourceDirectory).map((file) => readFileSync(file, 'utf8')).join('\n');
  assert.doesNotMatch(serverSources, /\bfetch\s*\(|\bhttps?\.request\s*\(|\bnet\.connect\s*\(|\bcreateConnection\s*\(/,
    'Gateway source contains no outbound HTTP/TCP connection calls');
});

test('rejects encrypted application requests until the temporary auth key is bound', async (t) => {
  const { privateKey, publicKey } = generateKeyPairSync('rsa', {
    modulusLength: 2048,
    publicExponent: 0x10001,
  });
  const gateway = new MtprotoGateway({ privateKey, publicKey, host: '127.0.0.1', port: 0 });
  const address = await gateway.listen();
  const client = await WireTestClient.connect({ ...address, publicKey });
  t.after(async () => {
    client.destroy();
    await gateway.close();
  });

  await client.performHandshake('permanent');
  await client.performHandshake('temporary');
  await assert.rejects(client.requestPing(), /Gateway socket closed/);
  assert.equal(gateway.snapshot().protocolErrors, 1);
});

test('prototype refuses an external bind unless explicitly enabled', () => {
  const { privateKey, publicKey } = generateKeyPairSync('rsa', {
    modulusLength: 2048,
    publicExponent: 0x10001,
  });
  assert.throws(
    () => new MtprotoGateway({ privateKey, publicKey, host: '0.0.0.0', port: 5050 }),
    /Refusing non-loopback bind/,
  );
});
