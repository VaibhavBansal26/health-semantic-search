import time

import jwt
import pytest
from fastapi.testclient import TestClient
from test_api import StubEngine

from hss.accounts import AccountError, AccountStore, LoginLimiter, TokenIssuer, User, check_password, hash_password
from hss.webapp import build_app

PASSWORD = "a long enough passphrase"


@pytest.fixture(scope="module")
def issuer():
    return TokenIssuer()


@pytest.fixture()
def client(issuer, tmp_path):
    store = AccountStore(tmp_path / "users.db")
    store.upsert("admin@example.com", "admin passphrase 123", roles=("admin",))
    static = tmp_path / "web"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>app</title>")
    return TestClient(build_app(StubEngine(), store, issuer, static_dir=static))


def login(client, email="admin@example.com", password="admin passphrase 123"):
    return client.post("/auth/login", json={"email": email, "password": password})


def bearer(resp):
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_password_hash_is_salted_and_verifies_only_the_right_password():
    a, b = hash_password(PASSWORD), hash_password(PASSWORD)
    assert a != b and PASSWORD not in a
    assert check_password(PASSWORD, a) and not check_password(PASSWORD + "x", a)
    assert not check_password(PASSWORD, "garbage")


def test_store_rejects_short_passwords_bad_emails_and_duplicates():
    store = AccountStore(":memory:")
    for email, pw in (("not-an-email", PASSWORD), ("a@b.co", "short")):
        with pytest.raises(AccountError):
            store.register(email, pw)
    store.register("A@Example.com", PASSWORD)
    with pytest.raises(AccountError):
        store.register("a@example.com", PASSWORD)  # same address, different case
    assert store.authenticate("a@example.COM", PASSWORD).email == "a@example.com"


def test_registered_users_get_no_groups_and_no_roles(client):
    resp = client.post("/auth/register", json={"email": "new@example.com", "password": PASSWORD})
    assert resp.status_code == 200 and resp.json()["user"]["groups"] == [] and resp.json()["user"]["roles"] == []
    assert login(client, "new@example.com", PASSWORD).status_code == 200


def test_wrong_password_and_unknown_email_look_the_same(client):
    a = login(client, "admin@example.com", "wrong password here")
    b = login(client, "nobody@example.com", "wrong password here")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_login_is_blocked_after_repeated_failures_even_for_the_right_password(issuer, tmp_path):
    app = build_app(StubEngine(), AccountStore(":memory:"), issuer, limiter=LoginLimiter(attempts=3))
    c = TestClient(app)
    c.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    for _ in range(3):
        assert login(c, "a@example.com", "nope nope nope").status_code == 401
    assert login(c, "a@example.com", PASSWORD).status_code == 429


def test_limiter_window_expires():
    now = [0.0]
    lim = LoginLimiter(attempts=2, window=10, clock=lambda: now[0])
    lim.failed("ip", "a@b.co"), lim.failed("ip", "a@b.co")
    assert lim.blocked("ip", "A@b.co")
    now[0] = 11
    assert not lim.blocked("ip", "a@b.co")


def test_tokens_are_rs256_short_lived_and_carry_a_jti(client, issuer):
    resp = login(client)
    claims = jwt.decode(resp.json()["token"], issuer.public_pem, algorithms=["RS256"])
    assert claims["roles"] == ["admin"] and claims["jti"] and claims["exp"] - claims["iat"] == 3600
    assert jwt.get_unverified_header(resp.json()["token"])["alg"] == "RS256"


def test_a_token_signed_by_another_key_is_rejected(client):
    forged = TokenIssuer().issue(User("admin@example.com", roles=("admin",)))[0]
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert client.post("/search", json={"query": "x"}, headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_logout_revokes_the_token(client):
    resp = login(client)
    assert client.get("/auth/me", headers=bearer(resp)).status_code == 200
    assert client.post("/auth/logout", headers=bearer(resp)).status_code == 200
    assert client.get("/auth/me", headers=bearer(resp)).status_code == 401


def test_demo_personas_never_carry_admin_and_unknown_ones_are_refused(client):
    resp = client.post("/auth/demo", json={"persona": "multi"})
    assert resp.json()["user"]["groups"] == ["oncology", "cardiology"] and resp.json()["user"]["roles"] == []
    assert client.get("/admin/not-found", headers=bearer(resp)).status_code == 403
    assert client.post("/auth/demo", json={"persona": "admin"}).status_code == 422


def test_admin_can_read_the_log_and_a_plain_user_cannot(client):
    admin = login(client)
    user = client.post("/auth/register", json={"email": "u@example.com", "password": PASSWORD})
    client.post("/search", json={"query": "zzzqqq"}, headers=bearer(user))
    assert client.get("/admin/not-found", headers=bearer(user)).status_code == 403
    assert client.get("/admin/not-found", headers=bearer(admin)).status_code == 200


def test_config_and_static_front_end(client):
    config = client.get("/config").json()
    assert config["answers"] is True and "hybrid" in config["modes"] and config["registration"] is True
    assert "<title>app</title>" in client.get("/").text


def test_security_headers_and_no_store_on_api_responses(client):
    resp = client.get("/config")
    assert "default-src 'self'" in resp.headers["content-security-policy"] and resp.headers["x-content-type-options"] == "nosniff"
    assert client.post("/auth/demo", json={"persona": "anon"}).headers["cache-control"] == "no-store"


def test_registration_can_be_turned_off(issuer):
    c = TestClient(build_app(StubEngine(), AccountStore(":memory:"), issuer, registration=False))
    assert c.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD}).status_code == 403


def test_engine_without_answers_returns_501(issuer):
    class NoAnswers(StubEngine):
        answers = False

    c = TestClient(build_app(NoAnswers(), AccountStore(":memory:"), issuer))
    token = c.post("/auth/demo", json={"persona": "anon"}).json()["token"]
    assert c.post("/answer", json={"query": "hypertension"}, headers={"Authorization": f"Bearer {token}"}).status_code == 501
    assert c.get("/config").json()["answers"] is False


def test_key_generated_when_none_given_and_expiry_is_enforced():
    issuer = TokenIssuer()
    assert issuer.generated
    token, _ = issuer.issue(User("a@example.com"), lifetime=60, now=time.time() - 3600)
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, issuer.public_pem, algorithms=["RS256"])
    assert not TokenIssuer(issuer._private).generated
