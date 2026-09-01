"""Recovery: turn sanitized artifacts back into the original text.

Independent of the sanitize pipeline. Recovery needs three things — the
sanitized document, its ``audit.json`` (records the cipher parameters plus
each span's ciphertext and position) and the passphrase (arg >
``PY_SANITIZE_RECOVER_KEY`` > the run's ``.recover.key`` keyfile). Public API:

- :class:`~pysanitize.recover.crypto.TokenCipher` — ciphertext, KDF, key management
- :func:`~pysanitize.recover.restore.recover_file` — md/pdf → ``*_recovered.*``
"""

from pysanitize.recover.crypto import (
    ALGORITHM,
    ENV_KEY,
    KEYFILE_NAME,
    TOKEN_RE,
    TokenCipher,
    obtain_passphrase,
)
from pysanitize.recover.restore import RecoverResult, recover_file

__all__ = [
    "ALGORITHM",
    "ENV_KEY",
    "KEYFILE_NAME",
    "RecoverResult",
    "TOKEN_RE",
    "TokenCipher",
    "obtain_passphrase",
    "recover_file",
]
