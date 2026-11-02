"""The web application: search API, accounts and login, and the React front end, served from one process.

    HSS_ADMIN_EMAIL=you@example.com HSS_ADMIN_PASSWORD=... uv run python -m hss.webapp

Environment:
  HSS_ENGINE            lite (default; fits 512 MB, no cited answers) or full (reranker and cited answers, about 2 GB)
  HSS_LITE_DIR          artifacts from `python -m hss.lite build` (default data/lite)
  HSS_CORPUS, HSS_EMBEDDINGS   corpus and vectors for the full engine
  HSS_JWT_PRIVATE_KEY   PEM private key that signs tokens (HSS_JWT_PRIVATE_KEY_FILE for a path); without one a key is
                        generated at start, which logs everyone out on every restart
  HSS_ACCOUNTS_DB       SQLite file for accounts (default data/accounts.db)
  HSS_ADMIN_EMAIL, HSS_ADMIN_PASSWORD   an admin account created or reset at every start
  HSS_REGISTRATION      "0" turns off the sign-up form
  HSS_STATIC_DIR        built front end (default web/dist)
  HSS_QUERY_LOG         append queries (no user) here for later labeling
  HOST, PORT            where to listen (default 127.0.0.1:8000)

Self-registered accounts see public labels only. The three demo personas get preset synthetic groups so the access
control can be tried without an account; they never carry the admin role.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from hss.accounts import AccountError, AccountStore, LoginLimiter, TokenIssuer, User
from hss.api import Engine, create_app
from hss.auth import AuthError, Revocations, claims_from_token
from hss.querylog import QueryLog

log = logging.getLogger("hss.webapp")

PERSONAS = {
    "anon": ("Public only", "No groups: sees only labels marked public.", ()),
    "oncology": ("Oncology member", "Sees public labels plus those restricted to oncology.", ("oncology",)),
    "multi": ("Oncology + cardiology", "Sees public labels plus oncology and cardiology ones.", ("oncology", "cardiology")),
}
NOTICE = (
    "The drug-label text is public. Access groups are synthetic, assigned from a hash of each label's id, to show how "
    "permission-aware search works. Accounts are stored on temporary disk and may be reset when the service restarts."
)
CSP = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class DemoRequest(BaseModel):
    persona: str


class Headers(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith(("/auth", "/search", "/answer", "/admin")):
            response.headers["Cache-Control"] = "no-store"
        return response


def client_ip(request: Request) -> str:
    """The address the platform's proxy saw: the last entry it appended, since earlier ones can be forged by the caller."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def build_app(
    engine: Engine,
    store: AccountStore,
    issuer: TokenIssuer,
    revocations: Revocations | None = None,
    static_dir: Path | None = None,
    registration: bool = True,
    query_log: QueryLog | None = None,
    limiter: LoginLimiter | None = None,
) -> FastAPI:
    revoked = revocations if revocations is not None else Revocations()
    app = create_app(engine, issuer.public_pem, ("RS256",), revoked, query_log)
    app.add_middleware(Headers)
    limiter = limiter or LoginLimiter()

    def session(user: User) -> dict:
        token, claims = issuer.issue(user)
        return {"token": token, "user": {"email": user.email, "groups": list(user.groups), "roles": list(user.roles),
                                         "tenant": user.tenant, "expires": claims["exp"]}}

    def claims_of(authorization: str | None = Header(default=None)) -> dict:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        try:
            claims = claims_from_token(authorization[7:], issuer.public_pem, ("RS256",))
            revoked.check(claims)
        except AuthError:
            raise HTTPException(401, "invalid or expired token") from None
        return claims

    @app.get("/config")
    def config() -> dict:
        return {
            "engine": getattr(engine, "name", "full"),
            "modes": list(engine.modes),
            "answers": bool(getattr(engine, "answers", True)),
            "registration": registration,
            "demo_personas": [{"id": k, "label": v[0], "description": v[1]} for k, v in PERSONAS.items()],
            "notice": NOTICE,
        }

    @app.post("/auth/register")
    def register(body: Credentials, request: Request) -> dict:
        if not registration:
            raise HTTPException(403, "sign-up is turned off")
        try:
            return session(store.register(body.email, body.password))
        except AccountError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/auth/login")
    def login(body: Credentials, request: Request) -> dict:
        ip = client_ip(request)
        if limiter.blocked(ip, body.email):
            raise HTTPException(429, "too many failed attempts; try again in a few minutes")
        try:
            user = store.authenticate(body.email, body.password)
        except AccountError as exc:
            limiter.failed(ip, body.email)
            raise HTTPException(401, str(exc)) from None
        limiter.succeeded(ip, body.email)
        return session(user)

    @app.post("/auth/demo")
    def demo(body: DemoRequest) -> dict:
        if body.persona not in PERSONAS:
            raise HTTPException(422, f"persona must be one of {list(PERSONAS)}")
        return session(User(f"demo-{body.persona}@demo.invalid", PERSONAS[body.persona][2]))

    @app.get("/auth/me")
    def me(claims: dict = Depends(claims_of)) -> dict:
        return {"email": claims["sub"], "groups": claims.get("groups", []), "roles": claims.get("roles", []),
                "tenant": claims.get("tenant"), "expires": claims["exp"]}

    @app.post("/auth/logout")
    def logout(claims: dict = Depends(claims_of)) -> dict:
        if claims.get("jti"):
            revoked.revoke_token(claims["jti"], float(claims["exp"]) + 60)
        return {"ok": True}

    if static_dir and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


def make_engine(kind: str):
    if kind == "full":
        from hss.service import HssEngine

        return HssEngine(Path(os.environ.get("HSS_CORPUS", "deploy/space/data/corpus.jsonl.gz")),
                         Path(os.environ.get("HSS_EMBEDDINGS", "deploy/space/data/embeddings_fp16.npy")))
    from hss.lite import LiteEngine

    return LiteEngine(Path(os.environ.get("HSS_LITE_DIR", "data/lite")))


def issuer_from_env() -> TokenIssuer:
    pem = os.environ.get("HSS_JWT_PRIVATE_KEY")
    if not pem and os.environ.get("HSS_JWT_PRIVATE_KEY_FILE"):
        pem = Path(os.environ["HSS_JWT_PRIVATE_KEY_FILE"]).read_text()
    if pem:
        pem = pem.replace("\\n", "\n")  # platforms that store secrets on one line
    issuer = TokenIssuer(pem or None)
    if issuer.generated:
        log.warning("no HSS_JWT_PRIVATE_KEY set: generated a key, every restart signs everyone out")
    return issuer


def app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    store = AccountStore(Path(os.environ.get("HSS_ACCOUNTS_DB", "data/accounts.db")))
    admin_email, admin_password = os.environ.get("HSS_ADMIN_EMAIL"), os.environ.get("HSS_ADMIN_PASSWORD")
    if admin_email and admin_password:
        store.upsert(admin_email, admin_password, roles=("admin",))
        log.info("admin account ready for %s", admin_email)
    else:
        log.warning("HSS_ADMIN_EMAIL / HSS_ADMIN_PASSWORD not set: there is no admin account")
    query_log = QueryLog(Path(os.environ["HSS_QUERY_LOG"])) if os.environ.get("HSS_QUERY_LOG") else None
    started = time.time()
    engine = make_engine(os.environ.get("HSS_ENGINE", "lite"))
    log.info("engine ready in %.1f s", time.time() - started)
    return build_app(
        engine, store, issuer_from_env(), Revocations(Path(os.environ.get("HSS_REVOCATIONS", "data/revocations.json"))),
        Path(os.environ.get("HSS_STATIC_DIR", "web/dist")), os.environ.get("HSS_REGISTRATION", "1") != "0", query_log,
    )


def main() -> None:
    import uvicorn

    uvicorn.run(app_from_env(), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
