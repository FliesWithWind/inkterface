import json
import os
import random

import pytest

from inkterface_core.ota import ed25519

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
VECTORS = json.load(open(os.path.join(FIX, "rfc8032_ed25519.json")))


@pytest.mark.parametrize("v", VECTORS, ids=[v["name"] for v in VECTORS])
def test_rfc8032_vectors(v):
    seed, pub, msg, sig = (bytes.fromhex(v[k]) for k in ("secret", "public", "message", "signature"))
    assert ed25519.publickey(seed) == pub
    assert ed25519.sign(seed, msg) == sig
    assert ed25519.verify(pub, msg, sig)


def test_vectors_cover_rfc():
    assert [v["name"] for v in VECTORS] == ["1", "2", "3", "1024", "SHA(abc)"]


V = VECTORS[1]
PUB, MSG, SIG = (bytes.fromhex(V[k]) for k in ("public", "message", "signature"))


def flip(b: bytes, bit: int) -> bytes:
    x = bytearray(b)
    x[bit // 8] ^= 1 << (bit % 8)
    return bytes(x)


def test_every_single_bit_flip_is_rejected():
    # signature (512 bits), public key (256 bits), message (8 bits)
    assert all(not ed25519.verify(PUB, MSG, flip(SIG, i)) for i in range(512))
    assert all(not ed25519.verify(flip(PUB, i), MSG, SIG) for i in range(256))
    assert all(not ed25519.verify(PUB, flip(MSG, i), SIG) for i in range(8))


def test_wrong_key_and_truncation_and_extension():
    other = ed25519.publickey(b"\x01" * 32)
    assert not ed25519.verify(other, MSG, SIG)
    assert not ed25519.verify(PUB, MSG + b"x", SIG)
    assert not ed25519.verify(PUB, MSG, SIG[:-1])
    assert not ed25519.verify(PUB, MSG, SIG + b"\0")
    assert not ed25519.verify(PUB[:-1], MSG, SIG)
    assert not ed25519.verify(PUB, MSG, b"")


def test_non_bytes_input_returns_false():
    assert not ed25519.verify(None, MSG, SIG)
    assert not ed25519.verify(PUB, "text", SIG)
    assert not ed25519.verify(PUB, MSG, 5)


def test_s_not_reduced_is_rejected():
    q = 2**252 + 27742317777372353535851937790883648493
    s = int.from_bytes(SIG[32:], "little")
    assert not ed25519.verify(PUB, MSG, SIG[:32] + (s + q).to_bytes(32, "little"))  # malleable twin
    assert not ed25519.verify(PUB, MSG, SIG[:32] + b"\xff" * 32)


def test_small_order_and_undecodable_keys_rejected():
    identity = (1).to_bytes(32, "little")  # y=1: the neutral point, order 1
    order2 = (2**255 - 20).to_bytes(32, "little")  # y=-1, order 2
    assert not ed25519.verify(identity, MSG, identity + bytes(32))
    assert not ed25519.verify(order2, MSG, SIG)
    noncanonical = (2**255 - 19 + 1).to_bytes(32, "little")  # y >= p
    assert not ed25519.verify(noncanonical, MSG, SIG)
    off_curve = (2).to_bytes(32, "little")  # no x for y=2
    assert not ed25519.verify(off_curve, MSG, SIG)
    assert not ed25519.verify(PUB, MSG, off_curve + SIG[32:])  # undecodable R
    # y=0 on the curve with the sign bit set is x=0 case only for y=+-1; check the sign-bit reject
    bad_sign = bytearray(identity)
    bad_sign[31] |= 0x80  # x=0 with sign bit 1 is invalid
    assert not ed25519.verify(bytes(bad_sign), MSG, SIG)


def test_seed_length_checked():
    with pytest.raises(ValueError):
        ed25519.publickey(b"short")


def test_cross_check_with_cryptography():
    ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization as s

    rnd = random.Random(7)
    for n in (0, 1, 31, 200, 1500):
        seed = bytes(rnd.getrandbits(8) for _ in range(32))
        msg = bytes(rnd.getrandbits(8) for _ in range(n))
        k = ec.Ed25519PrivateKey.from_private_bytes(seed)
        pub = k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)
        assert ed25519.publickey(seed) == pub
        assert ed25519.sign(seed, msg) == k.sign(msg)
        assert ed25519.verify(pub, msg, k.sign(msg))
        assert not ed25519.verify(pub, msg + b"!", k.sign(msg))
