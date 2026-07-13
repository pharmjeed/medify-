from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet() -> Fernet:
    if not settings.field_encryption_key:
        raise RuntimeError("FIELD_ENCRYPTION_KEY is required before storing integration secrets")
    return Fernet(settings.field_encryption_key.encode())


def encrypt_value(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Unable to decrypt integration secret") from exc
