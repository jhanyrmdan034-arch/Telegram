#pragma once

// ---------------------------------------------------------------------------
// Custom backend configuration — self-hosted MTProto server (see server/mtserver).
//
// The mtserver prints a ready-to-paste version of this header at startup:
//   "Paste this into TMessagesProj/jni/tgnet/CustomBackendConfig.h"
//
// To point the app at YOUR server instead of Telegram's datacenters:
//   1. set CUSTOM_BACKEND to 1
//   2. fill in your server's host / port / RSA public key + fingerprint
//      (the values the server printed at startup)
// ---------------------------------------------------------------------------

#ifndef CUSTOM_BACKEND
#define CUSTOM_BACKEND 0
#endif

#define CUSTOM_BACKEND_DC_ID 1
#define CUSTOM_BACKEND_HOST "127.0.0.1"
#define CUSTOM_BACKEND_PORT 443
#define CUSTOM_BACKEND_RSA_FINGERPRINT 0x0000000000000000ULL

// PKCS#1 PEM ("BEGIN RSA PUBLIC KEY") of the server's RSA key. Must match the
// fingerprint the server advertises in resPQ (server_public_key_fingerprints).
static const char *CUSTOM_BACKEND_RSA_PUBLIC_KEY_PEM =
    "-----BEGIN RSA PUBLIC KEY-----\n"
    "-----END RSA PUBLIC KEY-----\n";
