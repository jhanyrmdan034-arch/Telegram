"""MTProto 2.0 cryptographic primitives.

Implemented from the public protocol specification and cross-checked against the
Android client in this repo (TMessagesProj/jni/tgnet/Handshake.cpp) and against
Telethon's client implementation (used as the TL library + test client).

- AES-256-IGE for payload encryption
- SHA-256 based msg_key / KDF for MTProto 2.0 encrypted messages (x=0 client->server, x=8 server->client)
- SHA-1 based data-with-hash + raw RSA for the auth-key DH handshake
- Telegram's well-known 2048-bit DH prime (same constant the app validates against, see Handshake.cpp "goodPrime")
"""
import os
import struct
from hashlib import sha1, sha256

from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def sha1b(b: bytes) -> bytes:
    return sha1(b).digest()


def sha256b(b: bytes) -> bytes:
    return sha256(b).digest()


def get_byte_array(i: int) -> bytes:
    """Big-endian minimal byte representation of a natural number (MTProto convention)."""
    if i == 0:
        return b"\x00"
    return i.to_bytes((i.bit_length() + 7) // 8, "big")


def tl_serialize_bytes(data: bytes) -> bytes:
    """TL 'bytes' serialization (used for the RSA fingerprint computation)."""
    if len(data) < 254:
        pad = (-(len(data) + 1)) % 4
        return bytes([len(data)]) + data + b"\x00" * pad
    pad = (-len(data)) % 4
    return b"\xfe" + struct.pack("<I", len(data))[:3] + data + b"\x00" * pad


# ---------------------------------------------------------------- AES-IGE ----
def _ecb(key: bytes, encrypt: bool):
    c = Cipher(algorithms.AES(key), modes.ECB())
    return c.encryptor() if encrypt else c.decryptor()


def ige_encrypt(plain: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-IGE encryption; pads input up to a multiple of 16 with random bytes."""
    if len(plain) % 16:
        plain = plain + os.urandom(16 - len(plain) % 16)
    enc = _ecb(key, True)
    iv1, iv2 = iv[:16], iv[16:]
    out = bytearray()
    for i in range(0, len(plain), 16):
        blk = bytes(x ^ y for x, y in zip(plain[i:i + 16], iv1))
        c = enc.update(blk)
        c = bytes(x ^ y for x, y in zip(c, iv2))
        out += c
        iv1, iv2 = c, plain[i:i + 16]
    return bytes(out)


def ige_decrypt(cipher: bytes, key: bytes, iv: bytes) -> bytes:
    dec = _ecb(key, False)
    iv1, iv2 = iv[:16], iv[16:]
    out = bytearray()
    for i in range(0, len(cipher), 16):
        blk = bytes(x ^ y for x, y in zip(cipher[i:i + 16], iv2))
        p = dec.update(blk)
        p = bytes(x ^ y for x, y in zip(p, iv1))
        out += p
        iv1, iv2 = cipher[i:i + 16], p
    return bytes(out)


# ------------------------------------------------- MTProto 2.0 message KDF ----
def calc_key(auth_key: bytes, msg_key: bytes, x: int):
    """Derive AES key/iv from auth_key + msg_key. x=0 for client->server, x=8 for server->client."""
    a = sha256(msg_key + auth_key[x:x + 36]).digest()
    b = sha256(auth_key[x + 40:x + 76] + msg_key).digest()
    aes_key = a[:8] + b[8:24] + a[24:32]
    aes_iv = b[:8] + a[8:24] + b[24:32]
    return aes_key, aes_iv


def msg_key_of(auth_key: bytes, plaintext_with_padding: bytes, x: int) -> bytes:
    """msg_key = bytes [8:24] of SHA256(auth_key[88+x : 88+x+32] + pt + padding)."""
    return sha256(auth_key[88 + x: 88 + x + 32] + plaintext_with_padding).digest()[8:24]


def auth_key_id(auth_key: bytes) -> bytes:
    """The 8-byte key_id sent in every encrypted message = SHA1(auth_key)[12:20]."""
    return sha1(auth_key).digest()[12:20]


def aux_hash(auth_key: bytes) -> int:
    return int.from_bytes(sha1(auth_key).digest()[:8], "little", signed=True)


def calc_new_nonce_hash(auth_key: bytes, new_nonce: int, number: int) -> int:
    """new_nonce_hashN used to confirm the DH exchange (SHA-1 based, like the app expects)."""
    nn = new_nonce.to_bytes(32, "little", signed=True)
    data = nn + struct.pack("<Bq", number, aux_hash(auth_key))
    return int.from_bytes(sha1(data).digest()[4:20], "little", signed=True)


# ------------------------------------------- handshake temp key derivation ----
def generate_key_data_from_nonce(server_nonce: int, new_nonce: int):
    """SHA-1 based tmp AES key/iv for the DH handshake (matches the app and Telethon)."""
    sn = server_nonce.to_bytes(16, "little", signed=True)
    nn = new_nonce.to_bytes(32, "little", signed=True)
    h1 = sha1(nn + sn).digest()
    h2 = sha1(sn + nn).digest()
    h3 = sha1(nn + nn).digest()
    key = h1 + h2[:12]
    iv = h2[12:20] + h3 + nn[:4]
    return key, iv


# ------------------------------------------------------------------- RSA ------
class RSAKey:
    """2048-bit RSA key with raw (textbook) encrypt/decrypt, as MTProto requires."""

    def __init__(self, priv=None):
        if priv is None:
            priv = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.priv = priv
        nums = priv.private_numbers()
        self.n = nums.public_numbers.n
        self.e = nums.public_numbers.e
        self.d = nums.d
        self.size = (self.n.bit_length() + 7) // 8

    @classmethod
    def load(cls, pem: bytes):
        priv = serialization.load_pem_private_key(pem, password=None)
        return cls(priv)

    def public_pem(self) -> bytes:
        return self.priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.PKCS1,  # "BEGIN RSA PUBLIC KEY" — what the client embeds
        )

    def private_pem(self) -> bytes:
        return self.priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )

    @property
    def fingerprint(self) -> int:
        """Telegram-style RSA fingerprint: last 8 bytes of SHA1(TL-bytes(n) + TL-bytes(e)), little-endian."""
        fp = sha1(tl_serialize_bytes(get_byte_array(self.n)) +
                  tl_serialize_bytes(get_byte_array(self.e))).digest()[-8:]
        return struct.unpack("<q", fp)[0]

    def encrypt_raw(self, data: bytes) -> bytes:
        m = int.from_bytes(data, "big")
        return pow(m, self.e, self.n).to_bytes(self.size, "big")

    def decrypt_raw(self, ct: bytes) -> bytes:
        c = int.from_bytes(ct, "big")
        return pow(c, self.d, self.n).to_bytes(self.size, "big")


# ------------------------------------------------------------ factorization ---
def factorize(pq: int):
    """Pollard-Brent factorization of the 64-bit pq sent in resPQ."""
    if pq % 2 == 0:
        return 2, pq // 2
    import random
    from math import gcd
    y, c, m = random.randint(1, pq - 1), random.randint(1, pq - 1), random.randint(1, pq - 1)
    g = r = q = 1
    x = ys = 0
    while g == 1:
        x = y
        for _ in range(r):
            y = (pow(y, 2, pq) + c) % pq
        k = 0
        while k < r and g == 1:
            ys = y
            for _ in range(min(m, r - k)):
                y = (pow(y, 2, pq) + c) % pq
                q = q * abs(x - y) % pq
            g = gcd(q, pq)
            k += m
        r *= 2
    if g == pq:
        while True:
            ys = (pow(ys, 2, pq) + c) % pq
            g = gcd(abs(x - ys), pq)
            if g > 1:
                break
    return g, pq // g


# ------------------------------------------------------- DH prime / generator --
# Telegram's standard 2048-bit DH prime. The client only validates that it is a
# 2048-bit prime with the required properties, so this (public, documented) prime
# is used. Same constant as TMessagesProj/jni/tgnet/Handshake.cpp "goodPrime".
DH_PRIME = int(
    "c71caeb9c6b1c9048e6c522f70f13f73980d40238e3e21c14934d037563d930"
    "f48198a0aa7c14058229493d22530f4dbfa336f6e0ac925139543aed44cce7c37"
    "20fd51f69458705ac68cd4fe6b6b13abdc9746512969328454f18faf8c595f642"
    "477fe96bb2a941d5bcd1d4ac8cc49880708fa9b378e3c4f3a9060bee67cf9a4a4"
    "a695811051907e162753b56b0f6b410dba74d8a84b2a14b3144e0ef1284754fd"
    "17ed950d5965b4b9dd46582db1178d169c6bc465b0d6ff9ca3928fef5b9ae4e4"
    "18fc15e83ebea0f87fa9ff5eed70050ded2849f47bf959d956850ce929851f0"
    "d8115f635b105ee2e4e15d04b2454bf6f4fadf034b10403119cd8e3b92fcc5b",
    16,
)
DH_G = 3
