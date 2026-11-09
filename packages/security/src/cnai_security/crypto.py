"""Envelope encryption for identity fields, notes and backups (NFR-SEC-02, SECURITY.md section 5).

Each value gets its own random 256-bit data key (DEK). The DEK is wrapped by a key-encryption key (KEK) held by a
KeyProvider (a KMS, HSM or secret manager in production). Ciphertext is bound to the caller's context (for example the
patient id) so it cannot be moved to another record. Only the `cryptography` library's AES-GCM is used; there are no
custom primitives. Rotation re-wraps the DEK and never touches the data.

LocalKeyProvider is for development and tests only. Start-up checks refuse it in clinical mode.
"""
import base64
import os
import stat
import struct
from pathlib import Path
from typing import Mapping, Union

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"CNE1"
FILE_MAGIC = b"CNB1"
CHUNK = 1024 * 1024


class CryptoError(Exception):
    """Decryption refused (wrong key, wrong context, or tampered data). Never includes key material."""


class LocalKeyProvider:
    development_only = True

    def __init__(self, key_id: str, kek: bytes):
        if len(kek) != 32:
            raise ValueError("the key-encryption key must be 32 bytes")
        if not key_id or len(key_id.encode()) > 255:
            raise ValueError("invalid key id")
        self.key_id, self._aes = key_id, AESGCM(kek)

    @classmethod
    def generate(cls, key_id: str) -> "LocalKeyProvider":
        return cls(key_id, AESGCM.generate_key(256))

    @classmethod
    def from_file(cls, key_id: str, path: str) -> "LocalKeyProvider":
        """Read a base64 key from a file outside the repository, readable by the owner only."""
        p = Path(path)
        if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise PermissionError(f"{path} must not be readable by group or others (chmod 600)")
        return cls(key_id, base64.b64decode(p.read_text().strip()))

    def wrap(self, dek: bytes, context: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, dek, self.key_id.encode() + b"\x00" + context)

    def unwrap(self, wrapped: bytes, context: bytes) -> bytes:
        try:
            return self._aes.decrypt(wrapped[:12], wrapped[12:], self.key_id.encode() + b"\x00" + context)
        except InvalidTag:
            raise CryptoError("data key could not be unwrapped (wrong key or context)") from None


Providers = Union[LocalKeyProvider, Mapping[str, LocalKeyProvider]]


def _pick(providers: Providers, key_id: str):
    if isinstance(providers, Mapping):
        if key_id not in providers:
            raise CryptoError(f"no key available for key id {key_id!r}")
        return providers[key_id]
    if providers.key_id != key_id:
        raise CryptoError(f"no key available for key id {key_id!r}")
    return providers


def encrypt_field(provider, plaintext: bytes, context: bytes) -> bytes:
    dek, nonce = AESGCM.generate_key(256), os.urandom(12)
    wrapped = provider.wrap(dek, context)
    ct = AESGCM(dek).encrypt(nonce, plaintext, MAGIC + context)
    kid = provider.key_id.encode()
    return MAGIC + struct.pack(">B", len(kid)) + kid + struct.pack(">H", len(wrapped)) + wrapped + nonce + ct


def _parse(blob: bytes):
    if blob[:4] != MAGIC:
        raise CryptoError("not an encrypted field")
    try:
        klen = blob[4]
        kid = blob[5:5 + klen].decode()
        off = 5 + klen
        (wlen,) = struct.unpack(">H", blob[off:off + 2])
        wrapped = blob[off + 2:off + 2 + wlen]
        nonce = blob[off + 2 + wlen:off + 14 + wlen]
        ct = blob[off + 14 + wlen:]
    except (IndexError, struct.error, UnicodeDecodeError):
        raise CryptoError("malformed encrypted field") from None
    if len(wrapped) != wlen or len(nonce) != 12 or not ct:
        raise CryptoError("malformed encrypted field")
    return kid, wrapped, nonce, ct


def key_id_of(blob: bytes) -> str:
    return _parse(blob)[0]


def decrypt_field(providers: Providers, blob: bytes, context: bytes) -> bytes:
    kid, wrapped, nonce, ct = _parse(blob)
    dek = _pick(providers, kid).unwrap(wrapped, context)
    try:
        return AESGCM(dek).decrypt(nonce, ct, MAGIC + context)
    except InvalidTag:
        raise CryptoError("ciphertext failed authentication (tampered or wrong context)") from None


def rewrap(old_providers: Providers, new_provider, blob: bytes, context: bytes) -> bytes:
    """Key rotation: unwrap the data key with the old key and wrap it with the new one. The data is not re-encrypted."""
    kid, wrapped, nonce, ct = _parse(blob)
    dek = _pick(old_providers, kid).unwrap(wrapped, context)
    new_wrapped, new_kid = new_provider.wrap(dek, context), new_provider.key_id.encode()
    return MAGIC + struct.pack(">B", len(new_kid)) + new_kid + struct.pack(">H", len(new_wrapped)) + new_wrapped + nonce + ct


# ---------------------------------------------------------------------------------------------- files (backups)
def _chunk_nonce(prefix: bytes, index: int) -> bytes:
    return prefix + struct.pack(">Q", index)


def encrypt_file(provider, src: str, dst: str, context: bytes = b"backup") -> None:
    """Chunked AES-GCM (STREAM construction): each chunk has its own nonce and a final-chunk flag in its associated
    data, so reordering, dropping or truncating chunks is detected."""
    dek, prefix = AESGCM.generate_key(256), os.urandom(4)
    header = FILE_MAGIC + struct.pack(">B", len(provider.key_id.encode())) + provider.key_id.encode()
    wrapped = provider.wrap(dek, context)
    header += struct.pack(">H", len(wrapped)) + wrapped + prefix
    aes = AESGCM(dek)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(header)
        buf, idx = fin.read(CHUNK), 0
        while True:
            nxt = fin.read(CHUNK)
            final = not nxt
            ct = aes.encrypt(_chunk_nonce(prefix, idx), buf, header + (b"\x01" if final else b"\x00") + context)
            fout.write(struct.pack(">I", len(ct)) + ct)
            if final:
                break
            buf, idx = nxt, idx + 1


def decrypt_file(providers: Providers, src: str, dst: str, context: bytes = b"backup") -> None:
    with open(src, "rb") as fin:
        raw = fin.read(4)
        if raw != FILE_MAGIC:
            raise CryptoError("not an encrypted backup")
        try:
            klen = fin.read(1)[0]
            kid = fin.read(klen).decode()
            (wlen,) = struct.unpack(">H", fin.read(2))
            wrapped = fin.read(wlen)
            prefix = fin.read(4)
        except (IndexError, struct.error):
            raise CryptoError("malformed encrypted backup") from None
        header = FILE_MAGIC + struct.pack(">B", klen) + kid.encode() + struct.pack(">H", wlen) + wrapped + prefix
        aes = AESGCM(_pick(providers, kid).unwrap(wrapped, context))
        tmp = dst + ".partial"
        try:
            with open(tmp, "wb") as fout:
                idx, saw_final = 0, False
                while True:
                    lenb = fin.read(4)
                    if not lenb:
                        break
                    if saw_final:
                        raise CryptoError("data after the final chunk")
                    (n,) = struct.unpack(">I", lenb)
                    ct = fin.read(n)
                    if len(ct) != n:
                        raise CryptoError("backup is truncated")
                    nonce = _chunk_nonce(prefix, idx)
                    pt = None
                    for flag, final in ((b"\x00", False), (b"\x01", True)):
                        try:
                            pt = aes.decrypt(nonce, ct, header + flag + context)
                            saw_final = final
                            break
                        except InvalidTag:
                            continue
                    if pt is None:
                        raise CryptoError("backup chunk failed authentication (tampered, reordered or wrong key)")
                    fout.write(pt)
                    idx += 1
                if not saw_final:
                    raise CryptoError("backup is truncated (no final chunk)")
            os.replace(tmp, dst)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
