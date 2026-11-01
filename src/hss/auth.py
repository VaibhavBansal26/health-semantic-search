"""Identity from a signed token. The token is the only thing the search layer trusts about who is asking.

    principal = principal_from_token(bearer_token, key)

Claims read: `sub` (required), `exp` (required, so tokens expire), `groups` (list), `tenant` (string), `roles` (list; "admin"
grants every label). The signature and expiry are checked, and only the algorithms listed are accepted, so an unsigned
("alg: none") token or one signed with a different algorithm is rejected. Keys come from the deployment, not from the token.
"""

from __future__ import annotations

import jwt

from hss.acl import Principal


class AuthError(Exception):
    pass


def principal_from_token(token: str, key: str, algorithms: tuple[str, ...] = ("HS256",), audience: str | None = None) -> Principal:
    try:
        claims = jwt.decode(
            token, key, algorithms=list(algorithms), audience=audience,
            options={"require": ["exp", "sub"], "verify_aud": audience is not None},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    groups, roles = claims.get("groups", []), claims.get("roles", [])
    if not isinstance(groups, list) or not isinstance(roles, list):
        raise AuthError("groups and roles must be lists")
    return Principal(str(claims["sub"]), groups, admin="admin" in roles, tenant=claims.get("tenant"))
