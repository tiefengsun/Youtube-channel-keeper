"""Password hashing for the single administrator account."""
import hashlib
import secrets


ITERATIONS = 210_000


def password_digest(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)


def new_password_record(password):
    salt = secrets.token_bytes(16)
    return salt, password_digest(password, salt)


def verify_password(password, salt, expected):
    return secrets.compare_digest(password_digest(password, salt), expected)
