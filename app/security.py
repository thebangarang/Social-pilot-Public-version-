"""Security utilities: password hashing (scrypt) + TOTP secret encryption (Fernet)."""
import base64
import hashlib
import hmac
import os

from cryptography.fernet import Fernet

# --- password hashing (scrypt — memory-hard, OWASP-accepted, stdlib) ----------

# Password hashing cost: OWASP-recommended (128 MiB) for real use, fast (16 MiB)
# in the test suite where only dummy passwords are hashed.
_SCRYPT_N = 2**14 if os.environ.get("SP_ENV") == "test" else 2**17
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 256 * 1024 * 1024  # 256 MiB — headroom over the 128 MiB working set


def hash_password(password: str) -> str:
    """Hash a password with scrypt. Format: scrypt$N$r$p$salt_hex$dk_hex."""
    salt = os.urandom(16)
    dk = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=32, maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of a password against a stored scrypt hash."""
    try:
        parts = stored.split("$")
        if parts[0] != "scrypt" or len(parts) != 6:
            return False
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        # Clamp to sane bounds so a tampered hash string can't force a
        # resource-exhausting scrypt run (bounds match the hashing defaults).
        n = max(2**10, min(n, _SCRYPT_N))
        r = max(1, min(r, _SCRYPT_R))
        p = max(1, min(p, 2))
        salt = bytes.fromhex(parts[4])
        expected = bytes.fromhex(parts[5])
        if len(expected) != 32:
            return False
        dk = hashlib.scrypt(
            password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(dk, expected)
    except (ValueError, IndexError):
        return False


# --- TOTP secret encryption at rest (Fernet, key derived from SP_SECRET_KEY) ---


def _fernet() -> Fernet:
    from app.config import settings  # local import avoids config cycle

    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(cipher: str) -> str:
    return _fernet().decrypt(cipher.encode()).decode()


# --- Signed public media URLs (capability URLs for platform fetchers) ---------
# Meta/TikTok fetch media by HTTPS URL without auth; a random media_id alone is
# guessable only at 128 bits, but we also HMAC-sign so a leaked internal id
# cannot be forged into a working public URL without the server secret.


def _media_key() -> bytes:
    from app.config import settings  # local import avoids config cycle

    return hashlib.sha256(("media-url:" + settings.secret_key).encode()).digest()


def media_signature(media_id: str) -> str:
    return hmac.new(_media_key(), str(media_id).encode(), hashlib.sha256).hexdigest()


def verify_media_signature(media_id: str, sig: str) -> bool:
    return hmac.compare_digest(media_signature(media_id), sig or "")
