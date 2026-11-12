"""Pure-Python Ed25519 (RFC 8032, section 6 reference style). Stdlib only.

`verify` is what the plugin uses to check release manifests (Decky's Python bundle has no
crypto package). `publickey` / `sign` exist for tools/release/* only. They are NOT constant
time: fine for signing on a maintainer's machine or in CI, never use them on attacker-timed data.

Checked against the RFC 8032 section 7.1 vectors and, when the `cryptography` package is
installed, against it on random inputs (decky/tests/ota/test_ota_ed25519.py).

Strictness of `verify`: S must be < q, R and A must decode canonically (y < p), A must not
have small order, and the equation is the RFC's  [S]B == R + [h]A.  Never raises on junk.
"""
from __future__ import annotations

import hashlib

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


_NEUTRAL = (0, 1, 1, 0)


def _mul(s: int, p):
    q = _NEUTRAL
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(a, b) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(p) -> bytes:
    zi = _inv(p[2])
    x, y = p[0] * zi % _P, p[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(s: bytes):  # s is exactly 32 bytes (checked by verify)
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _h512_modq(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def _expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def publickey(seed: bytes) -> bytes:
    return _compress(_mul(_expand(seed)[0], _G))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(seed)
    A = _compress(_mul(a, _G))
    r = _h512_modq(prefix + msg)
    Rs = _compress(_mul(r, _G))
    s = (r + _h512_modq(Rs + A + msg) * a) % _Q
    return Rs + s.to_bytes(32, "little")


def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    try:
        public, msg, signature = bytes(public), bytes(msg), bytes(signature)
    except (TypeError, ValueError):
        return False
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    R = _decompress(signature[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    # small-order public key (order divides 8) makes the equation forgeable
    A8 = A
    for _ in range(3):
        A8 = _add(A8, A8)
    if _equal(A8, _NEUTRAL):
        return False
    h = _h512_modq(signature[:32] + public + msg)
    return _equal(_mul(s, _G), _add(R, _mul(h, A)))
