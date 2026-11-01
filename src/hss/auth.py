"""Identity from a signed token. The token is the only thing the search layer trusts about who is asking.

    principal = principal_from_token(bearer_token, key)

Claims read: `sub` (required), `exp` (required, so tokens expire), `groups` (list), `tenant` (string), `roles` (list; "admin"
grants every label), `jti` (token id) and `iat` (issue time), which revocation uses. The signature and expiry are checked,
and only the algorithms listed are accepted, so an unsigned ("alg: none") token or one signed with a different algorithm
is rejected. Keys come from the deployment, not from the token.

Two ways to verify:
  HS256  one shared secret signs and verifies. Anything that can verify can also mint any identity.
  RS256  an identity provider signs with a private key it alone holds; this service holds only public keys, so a leaked
         service key cannot mint tokens. Several keys may be loaded by `kid` so a key can be rotated without downtime.

Revocation: tokens are short-lived, but a stolen one stays valid until it expires. `Revocations` lets an admin cancel one
token (by `jti`) or everything issued to a user before now (by `sub`). Entries are kept in a file so a restart does not
un-revoke anyone, and are dropped once the token they cover would have expired anyway.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import jwt

from hss.acl import Principal

ASYMMETRIC = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")
SYMMETRIC = ("HS256", "HS384", "HS512")
MAX_LIFETIME = 24 * 3600  # a token that claims to live longer than this is refused; short lifetimes bound what revocation must remember


class AuthError(Exception):
    pass


def check_key_matches_algorithms(key, algorithms: tuple[str, ...]) -> None:
    """Refuse the classic confusion setup: a public key (or key set) that also accepts an HMAC algorithm.

    With that setup an attacker signs a token with HS256 using the public key as the secret, and it verifies.
    """
    keys = key.values() if isinstance(key, dict) else [key]
    looks_public = any(isinstance(k, (bytes, str)) and "BEGIN" in (k.decode() if isinstance(k, bytes) else k) for k in keys)
    if looks_public and any(a in SYMMETRIC for a in algorithms):
        raise ValueError("a PEM key cannot be combined with an HMAC algorithm")
    if not looks_public and any(a in ASYMMETRIC for a in algorithms):
        raise ValueError("an asymmetric algorithm needs a PEM public key")


def verification_from_env(env: dict[str, str] | None = None) -> tuple[object, tuple[str, ...]]:
    """(key, algorithms) from the deployment: HSS_JWT_PUBLIC_KEY_FILE (RS256) or HSS_JWT_KEY (HS256), never both."""
    env = os.environ if env is None else env
    public, secret = env.get("HSS_JWT_PUBLIC_KEY_FILE"), env.get("HSS_JWT_KEY")
    if public and secret:
        raise SystemExit("set HSS_JWT_PUBLIC_KEY_FILE or HSS_JWT_KEY, not both")
    if public:
        return Path(public).read_text(), ("RS256",)
    if secret:
        return secret, ("HS256",)
    raise SystemExit("set HSS_JWT_PUBLIC_KEY_FILE (RS256) or HSS_JWT_KEY (HS256)")


def _select_key(token: str, key):
    if not isinstance(key, dict):
        return key
    kid = jwt.get_unverified_header(token).get("kid")
    if kid not in key:
        raise AuthError("unknown key id")
    return key[kid]


def claims_from_token(token: str, key, algorithms: tuple[str, ...] = ("HS256",), audience: str | None = None) -> dict:
    try:
        claims = jwt.decode(
            token, _select_key(token, key), algorithms=list(algorithms), audience=audience,
            options={"require": ["exp", "sub"], "verify_aud": audience is not None},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    if claims["exp"] - time.time() > MAX_LIFETIME + 60:
        raise AuthError("token lifetime too long")
    return claims


def principal_from_claims(claims: dict) -> Principal:
    groups, roles = claims.get("groups", []), claims.get("roles", [])
    if not isinstance(groups, list) or not isinstance(roles, list):
        raise AuthError("groups and roles must be lists")
    return Principal(str(claims["sub"]), groups, admin="admin" in roles, tenant=claims.get("tenant"))


def principal_from_token(token: str, key, algorithms: tuple[str, ...] = ("HS256",), audience: str | None = None) -> Principal:
    return principal_from_claims(claims_from_token(token, key, algorithms, audience))


class Revocations:
    """Revoked token ids and per-user cutoffs. A token is refused if its `jti` is listed, or if its `sub` has a cutoff and
    the token was issued before it. A token with no `iat` cannot prove it was issued after a cutoff, so it is refused too.
    """

    def __init__(self, path: Path | None = None, clock=time.time):
        self.path, self.clock = path, clock
        self.jti: dict[str, float] = {}  # token id -> when the token would have expired anyway
        self.since: dict[str, float] = {}  # subject -> cutoff
        self._lock = threading.Lock()
        if path and path.exists():
            data = json.loads(path.read_text())
            self.jti, self.since = data["jti"], data["since"]

    def _save(self) -> None:
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"jti": self.jti, "since": self.since}))
            os.replace(tmp, self.path)

    def revoke_token(self, jti: str, expires: float) -> None:
        with self._lock:
            self.jti[jti] = expires
            self._prune()
            self._save()

    def revoke_subject(self, sub: str) -> float:
        """Cancel everything issued to `sub` up to now. A token issued after this moment (a fresh login) works."""
        with self._lock:
            self.since[sub] = self.clock()
            self._prune()
            self._save()
            return self.since[sub]

    def _prune(self) -> None:
        now = self.clock()
        self.jti = {j: e for j, e in self.jti.items() if e > now}
        self.since = {s: t for s, t in self.since.items() if t > now - MAX_LIFETIME - 60}  # older tokens have expired

    def check(self, claims: dict) -> None:
        if claims.get("jti") in self.jti:
            raise AuthError("token revoked")
        cutoff = self.since.get(str(claims["sub"]))
        if cutoff is not None and not claims.get("iat", float("-inf")) > cutoff:
            raise AuthError("token revoked")
