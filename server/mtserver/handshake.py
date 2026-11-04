"""MTProto auth-key creation (DH handshake), server side.

Flow (mirrors what the Android app in this repo expects, see jni/tgnet/Handshake.cpp):
  1. req_pq_multi        -> resPQ (we advertise OUR RSA fingerprint)
  2. req_DH_params       -> server_DH_params_ok (encrypted_answer, SHA-1 prefixed)
  3. set_client_DH_params-> dh_gen_ok (auth key established)

The client may use SHA-1 (MTProto 1.0 style, e.g. Telethon) or SHA-256 (MTProto 2.0
style, the Android app) for the "data-with-hash" prefix of req_DH_params /
set_client_DH_params; we accept both.
"""
import os
import struct
import time
from hashlib import sha1, sha256

from telethon.tl import types as T
from telethon.tl import functions as F

from .crypto import (DH_G, DH_PRIME, calc_new_nonce_hash, generate_key_data_from_nonce,
                     get_byte_array, ige_decrypt, ige_encrypt, sha1b, sha256b)
from .tlmini import TLReader, parse_object


def _rand_int(bits: int) -> int:
    return int.from_bytes(os.urandom(bits // 8), "little", signed=True)


def _rsa_decrypt_strip(rsa, encrypted_data: bytes) -> bytes:
    """Raw RSA decrypt; result is 256 bytes with a leading zero, then data-with-hash (255 bytes)."""
    m = rsa.decrypt_raw(encrypted_data)
    if m[0] == 0:
        m = m[1:]
    return m


def _split_data_with_hash(payload: bytes, expected_ctor: int):
    """Try SHA-1 then SHA-256 prefix; return (hash_fn_len, inner_bytes) or None."""
    for hlen, hfn in ((20, sha1b), (32, sha256b)):
        data = payload[hlen:]
        if len(data) < 4:
            continue
        ctor = struct.unpack("<I", data[:4])[0]
        if ctor != expected_ctor:
            continue
        # parse to find the exact length of the inner TL object
        try:
            reader = TLReader(data)
            reader.u32()  # constructor
            obj_len = _measure(expected_ctor, reader)
        except Exception:
            continue
        inner = data[:obj_len]
        if hfn(inner) == payload[:hlen]:
            return hlen, inner
    return None


def _measure(ctor: int, r: TLReader) -> int:
    """Consume fields of a known handshake constructor, returning bytes consumed."""
    start = r.o
    if ctor == 0x83C95AEC:  # pq_inner_data: pq:bytes p:bytes q:bytes nonce:int128 server_nonce:int128 new_nonce:int256
        r.tbytes(); r.tbytes(); r.tbytes(); r.raw(16); r.raw(16); r.raw(32)
    elif ctor == 0x6643B654:  # client_DH_inner_data: nonce server_nonce retry_id:long g_b:bytes
        r.raw(16); r.raw(16); r.raw(8); r.tbytes()
    elif ctor == 0xB5890DBA:  # server_DH_inner_data: nonce server_nonce g:int dh_prime:bytes g_a:bytes server_time:int
        r.raw(16); r.raw(16); r.u32(); r.tbytes(); r.tbytes(); r.u32()
    else:
        raise ValueError("unknown ctor %x" % ctor)
    return r.o - start + 4  # +4 for the constructor id


async def handle_handshake(obj, state: dict, server) -> tuple:
    """Returns (response_object, auth_key_bytes_or_None)."""
    rsa = server.config.rsa

    if isinstance(obj, F.ReqPqMultiRequest):
        # generate a fresh 64-bit pq (product of two primes)
        import random
        from math import gcd

        def is_probable_prime(n, k=16):
            if n < 2:
                return False
            for sp in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
                if n % sp == 0:
                    return n == sp
            d, s = n - 1, 0
            while d % 2 == 0:
                d //= 2
                s += 1
            for _ in range(k):
                a = random.randrange(2, n - 1)
                x = pow(a, d, n)
                if x in (1, n - 1):
                    continue
                for _ in range(s - 1):
                    x = pow(x, 2, n)
                    if x == n - 1:
                        break
                else:
                    return False
            return True

        def nextprime(n):
            if n % 2 == 0:
                n += 1
            while not is_probable_prime(n):
                n += 2
            return n

        p = nextprime(int.from_bytes(os.urandom(4), "big") | (1 << 31))
        q = nextprime(int.from_bytes(os.urandom(4), "big") | (1 << 31))
        pq = p * q
        state["server_nonce"] = _rand_int(128)
        state["pq"] = pq
        res = T.ResPQ(
            nonce=obj.nonce,
            server_nonce=state["server_nonce"],
            pq=get_byte_array(pq),
            server_public_key_fingerprints=[rsa.fingerprint],
        )
        return res, None

    if isinstance(obj, F.ReqDHParamsRequest):
        if obj.nonce != state.get("nonce") and "nonce" in state:
            pass
        payload = _rsa_decrypt_strip(rsa, obj.encrypted_data)
        split = _split_data_with_hash(payload, 0x83C95AEC)
        if not split:
            return T.ServerDHParamsFail(
                nonce=obj.nonce, server_nonce=obj.server_nonce,
                new_nonce_hash=_rand_int(128)), None
        _, inner = split
        pq_inner = parse_object(inner)
        if pq_inner.nonce != obj.nonce or pq_inner.server_nonce != obj.server_nonce:
            return T.ServerDHParamsFail(
                nonce=obj.nonce, server_nonce=obj.server_nonce,
                new_nonce_hash=_rand_int(128)), None
        new_nonce = pq_inner.new_nonce
        a = int.from_bytes(os.urandom(256), "big")
        g_a = pow(DH_G, a, DH_PRIME)
        state["a"] = a
        state["new_nonce"] = new_nonce
        inner_data = T.ServerDHInnerData(
            nonce=obj.nonce,
            server_nonce=obj.server_nonce,
            g=DH_G,
            dh_prime=get_byte_array(DH_PRIME),
            g_a=get_byte_array(g_a),
            server_time=int(time.time()),
        )
        inner_bytes = bytes(inner_data)
        plaintext = sha1b(inner_bytes) + inner_bytes
        plaintext += os.urandom((-len(plaintext)) % 16)
        key, iv = generate_key_data_from_nonce(obj.server_nonce, new_nonce)
        encrypted_answer = ige_encrypt(plaintext, key, iv)
        return T.ServerDHParamsOk(
            nonce=obj.nonce, server_nonce=obj.server_nonce,
            encrypted_answer=encrypted_answer), None

    if isinstance(obj, F.SetClientDHParamsRequest):
        key, iv = generate_key_data_from_nonce(obj.server_nonce, state["new_nonce"])
        plaintext = ige_decrypt(obj.encrypted_data, key, iv)
        split = _split_data_with_hash(plaintext, 0x6643B654)
        if not split:
            return T.DhGenFail(
                nonce=obj.nonce, server_nonce=obj.server_nonce,
                new_nonce_hash3=_rand_int(128)), None
        _, inner = split
        client_inner = parse_object(inner)
        if client_inner.nonce != obj.nonce or client_inner.server_nonce != obj.server_nonce:
            return T.DhGenFail(
                nonce=obj.nonce, server_nonce=obj.server_nonce,
                new_nonce_hash3=_rand_int(128)), None
        g_b = int.from_bytes(client_inner.g_b, "big")
        auth_key = get_byte_array(pow(g_b, state["a"], DH_PRIME))
        # pad to 256 bytes like the client does
        if len(auth_key) < 256:
            auth_key = b"\x00" * (256 - len(auth_key)) + auth_key
        nnh1 = calc_new_nonce_hash(auth_key, state["new_nonce"], 1)
        return T.DhGenOk(
            nonce=obj.nonce, server_nonce=obj.server_nonce,
            new_nonce_hash1=nnh1), auth_key

    raise ValueError("unexpected handshake object: %r" % obj)
