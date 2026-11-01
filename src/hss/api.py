"""HTTP API: /search, /answer and an admin-only /admin/not-found, all behind a bearer token.

    HSS_JWT_KEY=... uv run python -m hss.service

The caller's access mask is built from the token on every request and handed to the engine; a request without a valid
token gets 401 and never reaches search. Queries that found nothing (empty results, refused answers) are logged with who
asked, and only an admin token can read that log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from hss.acl import Principal
from hss.auth import AuthError, principal_from_token
from hss.cache import IndexChanged


class Engine(Protocol):
    modes: tuple[str, ...]

    def mask_for(self, who: Principal) -> np.ndarray: ...
    def search(self, mode: str, query: str, k: int, mask: np.ndarray) -> list[dict]: ...
    def answer(self, query: str, mask: np.ndarray) -> dict: ...


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    mode: str = "hybrid"
    k: int = Field(default=5, ge=1, le=20)


class AnswerRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


@dataclass
class NotFoundLog:
    rows: list[dict] = field(default_factory=list)
    limit: int = 1000

    def add(self, who: Principal, query: str, endpoint: str, reason: str) -> None:
        self.rows.append({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "user": who.name, "tenant": who.tenant,
                          "endpoint": endpoint, "query": query, "reason": reason})
        del self.rows[: -self.limit]


def create_app(engine: Engine, key: str, algorithms: tuple[str, ...] = ("HS256",)) -> FastAPI:
    app = FastAPI(title="Health Semantic Search")
    log = NotFoundLog()

    @app.exception_handler(IndexChanged)
    def index_changed(request, exc):  # noqa: ANN001 - FastAPI handler signature
        return JSONResponse({"detail": "the index was just updated; retry"}, status_code=503, headers={"Retry-After": "1"})

    def whoami(authorization: str | None = Header(default=None)) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        try:
            return principal_from_token(authorization[7:], key, algorithms)
        except AuthError:
            raise HTTPException(401, "invalid or expired token") from None

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.post("/search")
    def search(req: SearchRequest, who: Principal = Depends(whoami)) -> dict:
        if req.mode not in engine.modes:
            raise HTTPException(422, f"mode must be one of {list(engine.modes)}")
        results = engine.search(req.mode, req.query, req.k, engine.mask_for(who))
        if not results:
            log.add(who, req.query, "search", "no readable result")
        return {"results": results}

    @app.post("/answer")
    def answer(req: AnswerRequest, who: Principal = Depends(whoami)) -> dict:
        result = engine.answer(req.query, engine.mask_for(who))
        if not result["supported"]:
            log.add(who, req.query, "answer", f"refused (relevance {result['top_score']:.2f})")
        return result

    @app.get("/admin/not-found")
    def not_found(who: Principal = Depends(whoami)) -> dict:
        if not who.admin:
            raise HTTPException(403, "admin only")
        return {"rows": log.rows[::-1]}

    return app
