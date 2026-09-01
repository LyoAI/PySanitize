"""Reversible masking: scrypt(passphrase) → AES-256-GCM ciphertext per value.

A ciphertext — ``ENC(v1:<base64url>)`` — never appears in the document: the
sanitized output keeps the normal field placeholder, and the ciphertext lives
in ``audit.json`` (per span, next to its position) so ``--recover`` can put
the original back. The same value always maps to the same ciphertext within
a run.

The KDF is stdlib ``hashlib.scrypt``; only the AEAD needs the optional
``cryptography`` package (the ``recover`` extra). Key material never lives in
the audit: it stores only the scrypt salt and parameters (public), so anyone
holding the passphrase can recover. The passphrase itself is materialized in
the run's ``.recover.key`` (0600) — every ``--recoverable`` run writes the
effective key there, whichever way it was supplied, so recovery just reads it.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from pathlib import Path

from dotenv import load_dotenv

# The repo-root ``.env`` may hold the passphrase (the same file the rest of the
# app reads for API keys). The recover package loads it directly rather than
# importing pysanitize.config, keeping recovery an independent consumer — only
# the audit + passphrase, never the sanitize pipeline. Harmless no-op when
# there is no ``.env`` (e.g. a pip install); shell-exported vars always win.
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

MAGIC = "ENC"
VERSION = "v1"
ALGORITHM = "AES-256-GCM"
KDF_NAME = "scrypt"
KDF_PARAMS = {"n": 2**14, "r": 8, "p": 1}  # OWASP-recommended floor
KEY_LEN = 32  # AES-256
NONCE_LEN = 12  # GCM standard
ENV_KEY = "PYSANITIZE_RECOVER_KEY"
KEYFILE_NAME = ".recover.key"

# Format of a ciphertext as stored in audit.json — used to parse/validate a
# token (never to search documents: restoration splices by the recorded,
# placeholder-verified ``md`` ranges instead).
TOKEN_RE = re.compile(rf"{MAGIC}\({VERSION}:([A-Za-z0-9_-]+)\)")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_salt() -> str:
    return secrets.token_hex(16)


def derive_key(passphrase: str, salt: str, params: dict | None = None) -> bytes:
    """scrypt-derive the AES key (salt/params are public, stored in the audit)."""
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=bytes.fromhex(salt),
        **(params or KDF_PARAMS),
        dklen=KEY_LEN,
    )


def obtain_passphrase(
    explicit: str | None, keyfile: Path, *, allow_generate: bool = True
) -> tuple[str, bool]:
    """Resolve the recovery passphrase: arg > env > keyfile > generate.

    Returns ``(passphrase, generated)`` — ``generated`` is True only when a new
    key was minted. On the sanitize path (``allow_generate=True``) the *effective*
    key is always materialized into ``keyfile`` (0600, beside the audit), whether
    it came from the flag, the environment or a fresh draw, so ``.recover.key``
    is the single source of truth for ``--recover`` — no need to remember how
    the key was supplied. With ``allow_generate=False`` (recovery) the keyfile
    is never created or rewritten: a missing key is an error, never an invented
    one, and an explicitly passed key is used as-is.
    """
    if explicit:
        secret, generated = explicit, False
    elif env := os.environ.get(ENV_KEY):
        secret, generated = env, False
    elif keyfile.is_file() and (existing := keyfile.read_text(encoding="utf-8").strip()):
        return existing, False  # already materialized — reuse as-is
    elif allow_generate:
        secret, generated = secrets.token_urlsafe(24), True
    else:
        raise ValueError(
            f"no recovery passphrase: pass it explicitly, set {ENV_KEY}, "
            f"or keep {keyfile.name} beside audit.json"
        )
    if allow_generate:
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        keyfile.write_text(secret + "\n", encoding="utf-8")
        keyfile.chmod(0o600)
    return secret, generated


class TokenCipher:
    """Encrypts field values into reversible ciphertext for the audit trail.

    Not a masker: the document keeps its normal placeholders — the pipeline
    calls :meth:`token` per detection and records the result in audit.json.
    """

    def __init__(self, key: bytes, salt: str, kdf_params: dict | None = None):
        self._key = key
        self.salt = salt
        self.kdf_params = dict(kdf_params or KDF_PARAMS)
        self._cache: dict[str, str] = {}  # value → token: one nonce per value
        self._aead_obj = None  # lazy — keeps the cryptography import optional

    @classmethod
    def from_passphrase(
        cls, passphrase: str, salt: str | None = None, kdf_params: dict | None = None
    ) -> "TokenCipher":
        salt = salt or new_salt()
        return cls(derive_key(passphrase, salt, kdf_params), salt, kdf_params)

    def _aead(self):
        if self._aead_obj is None:
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            except ImportError as e:  # pragma: no cover - exercised via CLI path
                raise RuntimeError(
                    "recoverable masking needs the 'recover' extra: "
                    "uv sync --extra recover"
                ) from e
            self._aead_obj = AESGCM(self._key)
        return self._aead_obj

    def token(self, value: str) -> str:
        """Return the (cached) reversible ciphertext for ``value``."""
        tok = self._cache.get(value)
        if tok is None:
            nonce = os.urandom(NONCE_LEN)
            ct = self._aead().encrypt(nonce, value.encode("utf-8"), None)
            tok = f"{MAGIC}({VERSION}:{_b64url(nonce + ct)})"
            self._cache[value] = tok
        return tok

    def decrypt_token(self, token: str) -> str:
        """Invert :meth:`token` — raises on foreign keys or tampered tokens."""
        m = TOKEN_RE.fullmatch(token.strip())
        if not m:
            raise ValueError(f"not a recovery token: {token[:24]!r}")
        raw = _b64url_decode(m.group(1))
        nonce, ct = raw[:NONCE_LEN], raw[NONCE_LEN:]
        return self._aead().decrypt(nonce, ct, None).decode("utf-8")

    def meta(self) -> dict:
        """The audit ``recovery`` block — cipher parameters, never key material."""
        return {
            "enabled": True,
            "algorithm": ALGORITHM,
            "kdf": KDF_NAME,
            "kdf_salt": self.salt,
            "kdf_params": self.kdf_params,
            "ciphertext_format": f"{MAGIC}({VERSION}:<base64url>)",
        }
