"""
Password hashing for real owner/vet account credentials (see
app/routers/auth.py, app/routers/owners.py, app/routers/vets.py).

PBKDF2-HMAC-SHA256 via the standard library, not bcrypt/passlib — this
avoids adding a dependency with a native extension to compile (a real
concern for a demo project meant to run anywhere with zero setup
friction), while still being a credible, non-homegrown KDF: a random
per-password salt, 200k iterations (a reasonable modern cost factor),
and constant-time comparison on verify. Never log, return, or persist a
raw password anywhere past this module.
"""
import hashlib
import hmac
import secrets

_ALGORITHM = "sha256"
_ITERATIONS = 200_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """False (not an exception) for any malformed/missing stored hash —
    callers treat a failed lookup and a failed verify identically, so
    there's no behavioral difference to leak to a caller either way."""
    try:
        algorithm, iterations_str, salt, expected_hex = stored.split("$")
        iterations = int(iterations_str)
    except (ValueError, AttributeError):
        return False
    digest = hashlib.pbkdf2_hmac(algorithm, password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return hmac.compare_digest(digest.hex(), expected_hex)
