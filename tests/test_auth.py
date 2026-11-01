import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from hss.api import create_app
from hss.auth import AuthError, Revocations, check_key_matches_algorithms, claims_from_token, verification_from_env
from test_api import StubEngine, auth


def pem_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
        private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
    )


PRIVATE, PUBLIC = pem_pair()
OTHER_PRIVATE, OTHER_PUBLIC = pem_pair()


def rs_token(key=PRIVATE, kid=None, **claims):
    body = {"sub": "u", "exp": int(time.time()) + 600, "iat": int(time.time()), **claims}
    return jwt.encode(body, key, algorithm="RS256", headers={"kid": kid} if kid else None)


def rs_client(**kw):
    return TestClient(create_app(StubEngine(), PUBLIC, ("RS256",), **kw))


def search(client, t):
    return client.post("/search", json={"query": "hypertension edema"}, headers=auth(t))


def test_rs256_token_signed_by_the_provider_is_accepted_and_others_are_not():
    c = rs_client()
    assert search(c, rs_token()).status_code == 200
    assert search(c, rs_token(key=OTHER_PRIVATE)).status_code == 401  # signed by a key this service does not trust


def test_public_key_cannot_be_used_as_an_hmac_secret():
    import base64, hashlib, hmac, json

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    # the algorithm-confusion attack: an HS256 token whose HMAC secret is the (public) PEM text, built by hand because PyJWT refuses to
    signing = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps({"sub": "root", "roles": ["admin"], "exp": int(time.time()) + 600}).encode())
    forged = signing + "." + b64(hmac.new(PUBLIC.encode(), signing.encode(), hashlib.sha256).digest())
    assert search(rs_client(), forged).status_code == 401
    with pytest.raises(AuthError):  # a second line of defence: the library itself will not use a PEM key as an HMAC secret
        claims_from_token(forged, PUBLIC, ("HS256",))
    with pytest.raises(ValueError):
        check_key_matches_algorithms(PUBLIC, ("RS256", "HS256"))  # the setup that makes the attack work is refused outright
    with pytest.raises(ValueError):
        create_app(StubEngine(), PUBLIC, ("HS256",))
    with pytest.raises(ValueError):
        check_key_matches_algorithms("shared-secret", ("RS256",))


def test_key_set_selects_by_kid_and_rotation_keeps_old_tokens_valid_until_the_key_is_removed():
    keys = {"2026-10": PUBLIC, "2026-11": OTHER_PUBLIC}
    assert claims_from_token(rs_token(kid="2026-10"), keys, ("RS256",))["sub"] == "u"
    assert claims_from_token(rs_token(key=OTHER_PRIVATE, kid="2026-11"), keys, ("RS256",))["sub"] == "u"
    with pytest.raises(AuthError):
        claims_from_token(rs_token(kid="unknown"), keys, ("RS256",))
    with pytest.raises(AuthError):
        claims_from_token(rs_token(), keys, ("RS256",))  # no kid
    with pytest.raises(AuthError):  # kid names one key but the signature is from the other
        claims_from_token(rs_token(key=OTHER_PRIVATE, kid="2026-10"), keys, ("RS256",))
    with pytest.raises(AuthError):
        claims_from_token(rs_token(kid="2026-10"), {"2026-11": OTHER_PUBLIC}, ("RS256",))  # rotated out


def test_a_token_that_claims_a_very_long_life_is_refused():
    with pytest.raises(AuthError):
        claims_from_token(rs_token(exp=int(time.time()) + 30 * 24 * 3600), PUBLIC, ("RS256",))


def test_environment_picks_one_scheme(tmp_path):
    pub = tmp_path / "pub.pem"
    pub.write_text(PUBLIC)
    assert verification_from_env({"HSS_JWT_PUBLIC_KEY_FILE": str(pub)}) == (PUBLIC, ("RS256",))
    assert verification_from_env({"HSS_JWT_KEY": "s"}) == ("s", ("HS256",))
    for env in ({}, {"HSS_JWT_KEY": "s", "HSS_JWT_PUBLIC_KEY_FILE": str(pub)}):
        with pytest.raises(SystemExit):
            verification_from_env(env)


def test_admin_revokes_one_token_and_only_that_token_stops_working():
    c = rs_client()
    stolen, other = rs_token(jti="a1"), rs_token(jti="a2")
    assert search(c, stolen).status_code == 200
    admin = rs_token(sub="root", roles=["admin"], jti="adm")
    assert c.post("/admin/revoke", json={"jti": "a1"}, headers=auth(admin)).status_code == 200
    assert search(c, stolen).status_code == 401
    assert search(c, other).status_code == 200


def test_revoking_a_user_cancels_older_tokens_but_not_a_fresh_login():
    now = [1_000_000.0]
    rev = Revocations(clock=lambda: now[0])
    c = rs_client(revocations=rev)
    old = rs_token(sub="alice", iat=int(now[0]) - 100, exp=int(time.time()) + 600)
    no_iat = jwt.encode({"sub": "alice", "exp": int(time.time()) + 600}, PRIVATE, algorithm="RS256")
    admin = rs_token(sub="root", roles=["admin"])
    assert c.post("/admin/revoke", json={"sub": "alice"}, headers=auth(admin)).status_code == 200
    fresh = rs_token(sub="alice", iat=int(now[0]) + 5, exp=int(time.time()) + 600)
    assert search(c, old).status_code == 401
    assert search(c, no_iat).status_code == 401  # cannot prove it was issued after the cutoff
    assert search(c, fresh).status_code == 200
    assert search(c, rs_token(sub="bob", iat=int(now[0]) - 100)).status_code == 200  # other users unaffected


def test_revoke_endpoint_is_admin_only_and_validates_input():
    c = rs_client()
    assert c.post("/admin/revoke", json={"sub": "x"}, headers=auth(rs_token())).status_code == 403
    assert c.post("/admin/revoke", json={"sub": "x"}).status_code == 401
    admin = auth(rs_token(roles=["admin"]))
    assert c.post("/admin/revoke", json={}, headers=admin).status_code == 422
    assert c.post("/admin/revoke", json={"sub": "x", "jti": "y"}, headers=admin).status_code == 422


def test_revocations_survive_a_restart_and_expired_entries_are_dropped(tmp_path):
    path = tmp_path / "rev.json"
    now = [1000.0]
    rev = Revocations(path, clock=lambda: now[0])
    rev.revoke_token("t1", expires=1500.0)
    rev.revoke_subject("alice")
    again = Revocations(path, clock=lambda: now[0])
    with pytest.raises(AuthError):
        again.check({"sub": "u", "jti": "t1"})
    with pytest.raises(AuthError):
        again.check({"sub": "alice", "iat": 900})
    now[0] = 2000.0  # the token would have expired anyway; the entry no longer needs to be kept
    again.revoke_token("t2", expires=3000.0)
    assert "t1" not in again.jti
