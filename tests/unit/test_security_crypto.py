"""Envelope encryption and encrypted backups (NFR-SEC-02)."""
import os

import pytest
from hypothesis import given, settings, strategies as st

from cnai_security import crypto
from cnai_security.crypto import CryptoError, LocalKeyProvider, decrypt_field, decrypt_file, encrypt_field, encrypt_file, key_id_of, rewrap

CTX = b"identity:org:patient"


@pytest.fixture
def kp():
    return LocalKeyProvider.generate("k1")


@given(st.binary(max_size=2048), st.binary(max_size=64))
@settings(max_examples=60, deadline=None)
def test_round_trip(data, ctx):
    k = LocalKeyProvider.generate("k1")
    blob = encrypt_field(k, data, ctx)
    assert decrypt_field(k, blob, ctx) == data


def test_ciphertext_hides_the_plaintext_and_is_randomised(kp):
    pt = b"Jane Synthetic, 1980-01-01"
    a, b = encrypt_field(kp, pt, CTX), encrypt_field(kp, pt, CTX)
    assert pt not in a and a != b


def test_ciphertext_cannot_be_moved_to_another_record(kp):
    blob = encrypt_field(kp, b"secret", b"identity:org:patient-1")
    with pytest.raises(CryptoError):
        decrypt_field(kp, blob, b"identity:org:patient-2")


def test_any_bit_flip_is_detected(kp):
    blob = bytearray(encrypt_field(kp, b"secret value", CTX))
    for i in range(0, len(blob), max(1, len(blob) // 40)):
        mutated = bytearray(blob)
        mutated[i] ^= 0x01
        with pytest.raises(CryptoError):
            decrypt_field(kp, bytes(mutated), CTX)


def test_truncation_and_garbage_are_refused(kp):
    blob = encrypt_field(kp, b"secret value", CTX)
    for cut in (0, 3, 10, len(blob) - 1):
        with pytest.raises(CryptoError):
            decrypt_field(kp, blob[:cut], CTX)
    with pytest.raises(CryptoError):
        decrypt_field(kp, b"x" * 80, CTX)


def test_the_wrong_key_cannot_decrypt(kp):
    other = LocalKeyProvider.generate("k1")           # same id, different key material
    with pytest.raises(CryptoError):
        decrypt_field(other, encrypt_field(kp, b"secret", CTX), CTX)
    with pytest.raises(CryptoError):
        decrypt_field(LocalKeyProvider.generate("k2"), encrypt_field(kp, b"secret", CTX), CTX)   # unknown key id


def test_key_rotation_rewraps_without_touching_the_data(kp):
    new = LocalKeyProvider.generate("k2")
    blob = encrypt_field(kp, b"secret", CTX)
    rotated = rewrap(kp, new, blob, CTX)
    assert key_id_of(rotated) == "k2" and decrypt_field(new, rotated, CTX) == b"secret"
    assert rotated.endswith(blob[-(len(b"secret") + 16):])         # same ciphertext tail
    with pytest.raises(CryptoError):
        decrypt_field(kp, rotated, CTX)                             # old key no longer opens it
    assert decrypt_field({"k1": kp, "k2": new}, rotated, CTX) == b"secret"


def test_a_key_file_must_be_private(tmp_path):
    import base64
    f = tmp_path / "kek"
    f.write_text(base64.b64encode(os.urandom(32)).decode())
    os.chmod(f, 0o644)
    with pytest.raises(PermissionError):
        LocalKeyProvider.from_file("k", str(f))
    os.chmod(f, 0o600)
    assert LocalKeyProvider.from_file("k", str(f)).key_id == "k"


def test_bad_key_sizes_are_refused():
    with pytest.raises(ValueError):
        LocalKeyProvider("k", b"short")


# ------------------------------------------------------------------------------------------------ files ---
def make_file(tmp_path, size):
    p = tmp_path / "plain.bin"
    p.write_bytes(os.urandom(size))
    return p


@pytest.mark.parametrize("size", [0, 1, 1000, crypto.CHUNK, crypto.CHUNK + 1, 3 * crypto.CHUNK + 17])
def test_file_round_trip_including_chunk_boundaries(tmp_path, kp, size):
    src = make_file(tmp_path, size)
    enc, dec = tmp_path / "x.enc", tmp_path / "x.dec"
    encrypt_file(kp, str(src), str(enc))
    if size >= 64:                      # a 64-byte random prefix cannot appear in the ciphertext by chance
        assert src.read_bytes()[:64] not in enc.read_bytes()
    decrypt_file(kp, str(enc), str(dec))
    assert dec.read_bytes() == src.read_bytes()


def test_a_truncated_backup_is_refused_and_leaves_no_output(tmp_path, kp):
    src = make_file(tmp_path, 3 * crypto.CHUNK + 5)
    enc, dec = tmp_path / "x.enc", tmp_path / "x.dec"
    encrypt_file(kp, str(src), str(enc))
    raw = enc.read_bytes()
    enc.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(CryptoError):
        decrypt_file(kp, str(enc), str(dec))
    assert not dec.exists() and not os.path.exists(str(dec) + ".partial")


def test_dropping_the_last_whole_chunk_is_detected(tmp_path, kp):
    import struct
    src = make_file(tmp_path, 2 * crypto.CHUNK + 5)
    enc = tmp_path / "x.enc"
    encrypt_file(kp, str(src), str(enc))
    raw = enc.read_bytes()
    # walk the chunk list to find where the last chunk starts, then drop it
    hdr = 4 + 1 + len(b"k1") + 2 + struct.unpack(">H", raw[4 + 1 + 2:4 + 1 + 2 + 2])[0] + 4
    off, starts = hdr, []
    while off < len(raw):
        starts.append(off)
        off += 4 + struct.unpack(">I", raw[off:off + 4])[0]
    enc.write_bytes(raw[:starts[-1]])
    with pytest.raises(CryptoError):
        decrypt_file(kp, str(enc), str(tmp_path / "x.dec"))


def test_swapping_two_chunks_is_detected(tmp_path, kp):
    import struct
    src = make_file(tmp_path, 3 * crypto.CHUNK + 5)
    enc = tmp_path / "x.enc"
    encrypt_file(kp, str(src), str(enc))
    raw = enc.read_bytes()
    hdr = 4 + 1 + len(b"k1") + 2 + struct.unpack(">H", raw[4 + 1 + 2:4 + 1 + 2 + 2])[0] + 4
    off, chunks = hdr, []
    while off < len(raw):
        n = 4 + struct.unpack(">I", raw[off:off + 4])[0]
        chunks.append(raw[off:off + n])
        off += n
    enc.write_bytes(raw[:hdr] + chunks[1] + chunks[0] + b"".join(chunks[2:]))
    with pytest.raises(CryptoError):
        decrypt_file(kp, str(enc), str(tmp_path / "x.dec"))


def test_a_flipped_byte_in_a_backup_is_detected(tmp_path, kp):
    src = make_file(tmp_path, 5000)
    enc = tmp_path / "x.enc"
    encrypt_file(kp, str(src), str(enc))
    raw = bytearray(enc.read_bytes())
    raw[-20] ^= 1
    enc.write_bytes(bytes(raw))
    with pytest.raises(CryptoError):
        decrypt_file(kp, str(enc), str(tmp_path / "x.dec"))


def test_a_backup_is_bound_to_its_context(tmp_path, kp):
    src = make_file(tmp_path, 100)
    enc = tmp_path / "x.enc"
    encrypt_file(kp, str(src), str(enc), context=b"backup:prod")
    with pytest.raises(CryptoError):
        decrypt_file(kp, str(enc), str(tmp_path / "x.dec"), context=b"backup:test")
