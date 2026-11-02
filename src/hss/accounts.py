"""User accounts for the web app: email + password, stored in SQLite, and the tokens the app issues itself.

    store = AccountStore(Path("data/accounts.db"))
    store.register("a@example.com", "a long passphrase")
    user = store.authenticate("a@example.com", "a long passphrase")
    token = TokenIssuer(private_pem).issue(user)

Passwords are hashed with scrypt (standard library) and a random salt per user; only the hash is stored. A new account
has no groups, so it sees public labels only: access groups are granted by an operator (seeded accounts), never by
self-registration. Tokens are RS256, short lived, carry a `jti` so one can be revoked, and are verified by the same
`hss.api` code that checks tokens from any other identity provider.

The SQLite file lives wherever the deployment puts it. On a host with an ephemeral disk the accounts are lost on a
restart; the operator's admin account is re-created from the environment at every start for that reason.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from hss.acl import Principal

MIN_PASSWORD = 10
TOKEN_LIFETIME = 3600
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024}


class AccountError(Exception):
    """A request that cannot be honoured; the message is safe to show to the caller."""


@dataclass(frozen=True)
class User:
    email: str
    groups: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    tenant: str | None = None

    def principal(self) -> Principal:
        return Principal(self.email, self.groups, admin="admin" in self.roles, tenant=self.tenant)


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL.match(email):
        raise AccountError("enter a valid email address")
    return email


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, bytes.fromhex(digest))


# A hash to check against when the email is unknown, so a wrong email and a wrong password cost the same time.
_DUMMY = hash_password("not a real password")


class AccountStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, password TEXT NOT NULL, groups TEXT NOT NULL DEFAULT '',"
                " roles TEXT NOT NULL DEFAULT '', tenant TEXT, created REAL NOT NULL)"
            )
            self._db.commit()

    def register(self, email: str, password: str) -> User:
        email = normalize_email(email)
        if len(password) < MIN_PASSWORD:
            raise AccountError(f"use a password of at least {MIN_PASSWORD} characters")
        stored = hash_password(password)
        with self._lock:
            try:
                self._db.execute("INSERT INTO users (email, password, created) VALUES (?, ?, ?)", (email, stored, time.time()))
                self._db.commit()
            except sqlite3.IntegrityError:
                raise AccountError("an account with this email already exists") from None
        return User(email)

    def upsert(self, email: str, password: str, groups=(), roles=(), tenant: str | None = None) -> User:
        """Create or overwrite an account with the given rights. For operator-seeded accounts, not for the sign-up form."""
        email = normalize_email(email)
        with self._lock:
            self._db.execute(
                "INSERT INTO users (email, password, groups, roles, tenant, created) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(email) DO UPDATE SET password=excluded.password, groups=excluded.groups, roles=excluded.roles, tenant=excluded.tenant",
                (email, hash_password(password), ",".join(groups), ",".join(roles), tenant, time.time()),
            )
            self._db.commit()
        return User(email, tuple(groups), tuple(roles), tenant)

    def authenticate(self, email: str, password: str) -> User:
        email = email.strip().lower()
        with self._lock:
            row = self._db.execute("SELECT password, groups, roles, tenant FROM users WHERE email = ?", (email,)).fetchone()
        ok = check_password(password, row[0] if row else _DUMMY)
        if not row or not ok:
            raise AccountError("wrong email or password")
        return User(email, tuple(filter(None, row[1].split(","))), tuple(filter(None, row[2].split(","))), row[3])

    def get(self, email: str) -> User | None:
        with self._lock:
            row = self._db.execute("SELECT groups, roles, tenant FROM users WHERE email = ?", (email,)).fetchone()
        return User(email, tuple(filter(None, row[0].split(","))), tuple(filter(None, row[1].split(","))), row[2]) if row else None

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]


class TokenIssuer:
    """Signs RS256 tokens. Verification needs only `public_pem`, which is what `hss.api.create_app` is given."""

    def __init__(self, private_pem: str | None = None, kid: str = "app-1"):
        self.generated = private_pem is None
        if private_pem is None:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        self._private, self.kid = private_pem, kid
        self.public_pem = (
            serialization.load_pem_private_key(private_pem.encode(), password=None)
            .public_key()
            .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            .decode()
        )

    def issue(self, user: User, lifetime: int = TOKEN_LIFETIME, now: float | None = None) -> tuple[str, dict]:
        now = int(now if now is not None else time.time())
        claims = {"sub": user.email, "groups": list(user.groups), "roles": list(user.roles), "jti": secrets.token_urlsafe(12),
                  "iat": now, "exp": now + lifetime}
        if user.tenant:
            claims["tenant"] = user.tenant
        return jwt.encode(claims, self._private, algorithm="RS256", headers={"kid": self.kid}), claims


@dataclass
class LoginLimiter:
    """Refuse further attempts after repeated failures for the same (client, email) in a time window."""

    attempts: int = 8
    window: float = 900.0
    clock: object = time.time
    failures: dict = field(default_factory=dict)

    def _recent(self, key: tuple) -> list[float]:
        now = self.clock()
        if len(self.failures) > 10_000:  # bound memory against a flood of made-up emails
            self.failures = {k: v for k, v in self.failures.items() if v and v[-1] > now - self.window}
        recent = [t for t in self.failures.get(key, []) if t > now - self.window]
        self.failures[key] = recent
        return recent

    def blocked(self, client: str, email: str) -> bool:
        return len(self._recent((client, email.strip().lower()))) >= self.attempts

    def failed(self, client: str, email: str) -> None:
        self._recent((client, email.strip().lower())).append(self.clock())

    def succeeded(self, client: str, email: str) -> None:
        self.failures.pop((client, email.strip().lower()), None)
