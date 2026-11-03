import { chmodSync, mkdirSync, unlinkSync, writeFileSync } from 'node:fs';
import { generateKeyPairSync } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { rsaPublicFingerprint } from '../src/crypto.mjs';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, '../../..');
const outputDirectory = path.resolve(
  process.argv[2] || path.join(os.homedir(), '.config', 'telegram-mtproto-gateway'),
);
if (outputDirectory === repositoryRoot || outputDirectory.startsWith(`${repositoryRoot}${path.sep}`)) {
  throw new Error('Refusing to place private-key material inside the Git repository');
}

mkdirSync(outputDirectory, { recursive: true, mode: 0o700 });
const privatePath = path.join(outputDirectory, 'private.pem');
const publicPath = path.join(outputDirectory, 'public.pem');
const { privateKey, publicKey } = generateKeyPairSync('rsa', {
  modulusLength: 2048,
  publicExponent: 0x10001,
  privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
  publicKeyEncoding: { type: 'pkcs1', format: 'pem' },
});

let createdPrivateKey = false;
try {
  writeFileSync(privatePath, privateKey, { mode: 0o600, flag: 'wx' });
  createdPrivateKey = true;
  chmodSync(privatePath, 0o600);
  writeFileSync(publicPath, publicKey, { mode: 0o644, flag: 'wx' });
  chmodSync(publicPath, 0o644);
} catch (error) {
  // Do not leave a newly-created private key if the matching public key could
  // not be created; preserve any pre-existing private key untouched.
  if (createdPrivateKey) unlinkSync(privatePath);
  throw error;
}

const fingerprint = rsaPublicFingerprint(publicKey).toString('hex');
process.stdout.write(`Private key created outside Git at: ${privatePath}\n`);
process.stdout.write(`Public key created at: ${publicPath}\n`);
process.stdout.write(`Public-key fingerprint (non-secret): ${fingerprint}\n`);
process.stdout.write('Private-key contents were not printed.\n');
