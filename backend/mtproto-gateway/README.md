# MTProto Gateway — protocol proof of concept

This is an isolated, dependency-free Node.js proof of concept. It does not modify `TMessagesProj` and it has no outbound networking code. By default it binds only to `127.0.0.1`.

## Implemented scope

- Telegram Android's obfuscated abridged TCP transport (`0xef`), based on the transport used by `jni/tgnet/Connection.cpp`.
- MTProto 2.0 permanent-auth-key handshake, followed by the PFS temporary-auth-key handshake: each uses `req_pq_multi` → `req_DH_params` → `set_client_DH_params`.
- `auth.bindTempAuthKey` support: decrypts and validates the MTProto 1.0 `bind_auth_key_inner` envelope under the permanent key before accepting the temporary key.
- Per-connection in-memory permanent and temporary auth keys, key IDs, temporary-key expiry, server salt, session ID and message counters.
- Encrypted MTProto 2.0 packets and a deliberately small Layer 232 API subset: `invokeWithLayer` / `initConnection` wrapping `help.getConfig`, plus `ping` and `get_future_salts` handling.
- A loopback-only wire-test client that exercises the permanent → temporary → bind sequence and basic encrypted API calls without contacting any Telegram host.

This is **not** a production Telegram-compatible server. It does not implement authorization, accounts, messages, uploads, updates, CDN, multiple DCs, persistent auth keys, abuse controls or calls. The test harness is not the official Android APK. The Android client must be changed later to use the project's bootstrap address and pinned public-key fingerprint before it can connect here; the official client source has intentionally not been changed in this phase.

## Requirements

- Node.js 22 or newer.
- OpenSSL is optional; key generation uses Node's built-in crypto.

No npm packages are required.

## Generate a project RSA key outside this Git repository

```sh
cd backend/mtproto-gateway
node scripts/keygen.mjs
```

By default this creates `~/.config/telegram-mtproto-gateway/private.pem` (mode `0600`) and `public.pem` (mode `0644`). The private key is never printed and both key paths are outside the repository. Do not copy the private key into Git or logs.

## Run locally

```sh
cd backend/mtproto-gateway
MTG_RSA_PRIVATE_KEY="$HOME/.config/telegram-mtproto-gateway/private.pem" \
MTG_HOST=127.0.0.1 MTG_PORT=5050 node src/server.mjs
```

The server rejects non-loopback bind addresses unless `MTG_ALLOW_REMOTE=1` is explicitly set. This guard is only a prototype safety measure, not a production exposure policy.

## Run tests

```sh
cd backend/mtproto-gateway
npm test
```

The integration test generates an ephemeral RSA key pair in memory, starts a loopback listener on an ephemeral port, performs permanent and temporary DH handshakes, validates `auth.bindTempAuthKey` (including its MTProto 1.0 inner packet), then sends encrypted Layer 232 `help.getConfig`, `ping` and `get_future_salts` requests. It verifies the encrypted responses and session state without creating key files or contacting an external host. It is a custom wire harness, not a test of the official Android APK.

The current POC intentionally uses a project-specific RSA key fingerprint. The stock Android client does not trust that key and the Android networking code has not been redirected to this gateway. Passing `npm test` proves only the local protocol harness and gateway agree on the tested exchange; it does **not** prove compatibility with an unmodified or rebuilt Telegram Android client.

## Prototype security limitations

- This demonstrates protocol mechanics, not a reviewed cryptographic implementation.
- DH parameters, request rates, message IDs, replay protection, auth-key persistence, multiple connections, salt rotation, session binding and resource limits need production-grade hardening.
- The DH prime and TL constructors are public protocol data, not credentials.
- Logs intentionally omit nonces, phone numbers, auth keys, message keys and private key material.
- The repository contains no private RSA key. Never add one.
