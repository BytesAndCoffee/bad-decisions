from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .engine import generate_from_resolved
from .errors import BadDecisionsError
from .models import Round
from .packs import Registry, load_registry, resolve_pools
from .settings import Settings

REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
WEB_ASSETS = {"index.html": "text/html; charset=utf-8", "app.js": "application/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8", "favicon.svg": "image/svg+xml"}


def envelope(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def create_app() -> FastAPI:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("bad_decisions.api")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.registry = load_registry(settings.pack_dir)
        app.state.ready = True
        yield
        app.state.ready = False

    app = FastAPI(title="Bad Decisions API", version=__version__, lifespan=lifespan, root_path=settings.root_path)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled request failure request_id=%s", request_id)
            response = JSONResponse(envelope("internal_error", "Internal server error", {"request_id": request_id}), status_code=500)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.monotonic() - started) * 1000,
            request_id,
        )
        return response

    @app.exception_handler(BadDecisionsError)
    async def domain_error(_request: Request, exc: BadDecisionsError):
        return JSONResponse(envelope(exc.code, exc.message, exc.details), status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        return JSONResponse(envelope("request_validation", "Request validation failed", {"errors": exc.errors()}), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return JSONResponse(envelope("path_not_found", "Path not found"), status_code=404)
        return JSONResponse(envelope("http_error", "HTTP error", {"status": exc.status_code}), status_code=exc.status_code)

    @app.get("/healthz")
    def health(request: Request):
        ready = bool(getattr(request.app.state, "ready", False))
        registry: Registry | None = getattr(request.app.state, "registry", None)
        return JSONResponse(
            {"status": "ok" if ready else "unavailable", "version": __version__, "pack_count": len(registry.packs) if registry else 0},
            status_code=200 if ready else 503,
        )

    @app.get("/", response_class=PlainTextResponse, include_in_schema=False)
    def howto():
        prefix = settings.root_path.rstrip("/")
        return f"""Bad Decisions API

Reusable service for fill-in-the-blank card games.

Deal a completed hand:
  GET {prefix}/v1/round
  GET {prefix}/v1/round?packs=example
  GET {prefix}/v1/round?black_packs=prompts&white_packs=responses

Inspect the loaded registry:
  GET {prefix}/v1/packs
  GET {prefix}/v1/packs/{{pack_id}}

Portable packs:
  bad-decisions pack validate example.carddeck
  Format documentation: CARDDECK.md

Service interfaces:
  Interactive API docs: {prefix}/docs
  OpenAPI schema: {prefix}/openapi.json
  Health: {prefix}/healthz
"""

    def web_asset(name: str):
        media_type = WEB_ASSETS.get(name)
        if media_type is None:
            return JSONResponse(envelope("path_not_found", "Path not found"), status_code=404)
        return FileResponse(str(files("bad_decisions").joinpath("web", name)), media_type=media_type)

    @app.get("/web", include_in_schema=False)
    @app.get("/web/", include_in_schema=False)
    def web_index(request: Request):
        root_path = request.scope.get("root_path", "").rstrip("/")
        asset_base = f"{root_path}/web/" or "/web/"
        document = files("bad_decisions").joinpath("web", "index.html").read_text(encoding="utf-8")
        return HTMLResponse(document.replace("__WEB_BASE__", asset_base))

    @app.get("/web/{asset:path}", include_in_schema=False)
    def web_file(asset: str):
        return web_asset(asset)

    def metadata(pack):
        result = pack.metadata.model_dump(mode="json")
        result["counts"] = {"black": len(pack.black), "white": len(pack.white)}
        return result

    @app.get("/v1/packs")
    def list_packs(request: Request):
        return [metadata(pack) for pack in request.app.state.registry.packs.values()]

    @app.get("/v1/packs/{pack_id}")
    def get_pack(pack_id: str, request: Request):
        pack = request.app.state.registry.packs.get(pack_id)
        if pack is None:
            return JSONResponse(envelope("pack_not_found", f"Pack not found: {pack_id}", {"available_packs": list(request.app.state.registry.ids)}), status_code=404)
        return metadata(pack)

    @app.get("/v1/round", response_model=Round)
    def round_endpoint(
        request: Request,
        packs: Annotated[str | None, Query()] = None,
        black_packs: Annotated[str | None, Query()] = None,
        white_packs: Annotated[str | None, Query()] = None,
    ):
        allowed = {"packs", "black_packs", "white_packs"}
        unknown = sorted(set(request.query_params) - allowed)
        repeated = sorted(key for key in allowed if len(request.query_params.getlist(key)) > 1)
        if unknown:
            return JSONResponse(envelope("unknown_query_parameter", "Unknown query parameter", {"parameters": unknown}), status_code=400)
        if repeated:
            return JSONResponse(envelope("repeated_query_parameter", "Selector parameters must occur once", {"parameters": repeated}), status_code=400)
        registry = request.app.state.registry
        resolved = resolve_pools(registry, packs=packs, black_packs=black_packs, white_packs=white_packs)
        response = JSONResponse(generate_from_resolved(resolved, registry).model_dump(mode="json"))
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
