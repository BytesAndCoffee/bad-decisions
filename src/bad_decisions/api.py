from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated, Any

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from . import __version__
from .consequences import ConsequencesStore, valid_uuid
from .engine import generate_from_resolved
from .errors import BadDecisionsError
from .models import PackMetadata, Round
from .packs import Registry, load_registry, resolve_pools
from .peer_pressure import PeerPressureError, PeerPressureService
from .settings import Settings

API = "/v2"
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Domain errors are about the request (422) unless the server itself is misconfigured.
ERROR_STATUS = {
    "invalid_selector": 422, "unknown_pack": 422, "empty_pool": 422, "insufficient_answer_capacity": 422,
    "pack_configuration": 500, "answer_arity": 500,
}
CLIENT_HEADERS = ["Authorization", "Content-Type", "If-None-Match", "X-Request-ID", "X-Client-ID", "X-Session-ID", "X-Feedback-Token"]
WEB_ASSETS = {
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "together.js": "application/javascript; charset=utf-8",
    "together.css": "text/css; charset=utf-8",
    "theme.css": "text/css; charset=utf-8",
    "theme.js": "application/javascript; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}
WEB_DOCUMENTS = ("index.html", "together.html")
PACK_CACHE = "public, max-age=300"
POOL_CACHE_SIZE = 256  # distinct selectors remembered; invalid ones are never cached


def sse_event(event: str, payload: dict[str, Any], *, event_id: int | None = None) -> str:
    """Encode one JSON Server-Sent Event without permitting line injection."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    identifier = f"id: {event_id}\n" if event_id is not None else ""
    return f"{identifier}event: {event}\ndata: {data}\n\n"


# --- request bodies -------------------------------------------------------------

class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FeedbackInput(StrictInput):
    enjoyed: StrictBool


class RoomInput(StrictInput):
    room: str
    packs: list[str] | None = None


class JoinInput(StrictInput):
    display_name: str
    create: StrictBool = True
    packs: list[str] | None = None


class HeartbeatInput(StrictInput):
    revision: StrictInt = Field(ge=0)


class MutationInput(HeartbeatInput):
    request_id: str


class SubmissionInput(MutationInput):
    card_instance_ids: list[str]


class JudgmentInput(MutationInput):
    submission_id: str


# --- response bodies (documented in OpenAPI) -------------------------------------

class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class Health(BaseModel):
    status: str
    version: str
    pack_count: int


class PackCounts(BaseModel):
    prompts: int
    answers: int


class PackSummary(PackMetadata):
    counts: PackCounts


class FeedbackLink(BaseModel):
    available: bool
    url: str | None = None
    expires_at: str | None = None


class RoundResponse(Round):
    round_id: str | None = None
    combination_hash: str | None = None
    feedback: FeedbackLink | None = None


class FeedbackResult(BaseModel):
    round_id: str
    enjoyed: bool
    changed: bool


ERRORS = {status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 410, 422, 429, 500, 503)}


class RateLimiter:
    """Sliding one-minute window per client and bucket, per worker process."""

    def __init__(self, per_minute: int, now=time.monotonic) -> None:
        self.per_minute = per_minute
        self.now = now
        self.hits: dict[tuple[str, str], deque[float]] = {}
        self.lock = threading.Lock()

    def retry_after(self, client: str, bucket: str) -> int | None:
        """Record a hit; return seconds to wait when over the limit, else None."""
        now = self.now()
        with self.lock:
            if len(self.hits) > 10_000:  # bound memory: forget clients idle for a minute
                self.hits = {key: times for key, times in self.hits.items() if times and now - times[-1] < 60}
            times = self.hits.setdefault((client, bucket), deque())
            while times and now - times[0] >= 60:
                times.popleft()
            if len(times) >= self.per_minute:
                return max(1, int(60 - (now - times[0])) + 1)
            times.append(now)
            return None


def create_app() -> FastAPI:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("bad_decisions.api")
    limiter = RateLimiter(settings.rate_limit_per_minute)
    pool_cache: dict[tuple[str | None, str | None, str | None], Any] = {}
    web_digest = hashlib.sha256()
    for name in sorted(set(WEB_ASSETS) | set(WEB_DOCUMENTS)):
        web_digest.update(name.encode("utf-8"))
        web_digest.update(files("bad_decisions").joinpath("web", name).read_bytes())
    web_asset_version = web_digest.hexdigest()[:16]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        registry = load_registry(settings.pack_dir)
        app.state.registry = registry
        summaries = {pack_id: summary(pack) for pack_id, pack in registry.packs.items()}
        app.state.pack_summaries = summaries
        # The registry is immutable at runtime, so pack responses get stable validators.
        app.state.pack_etags = {pack_id: etag(value) for pack_id, value in summaries.items()}
        app.state.packs_etag = etag(list(summaries.values()))
        if settings.consequences_dynamodb_table and settings.consequences_recording:
            from .aws_consequences import DynamoConsequencesStore
            app.state.consequences = DynamoConsequencesStore(
                settings.consequences_dynamodb_table,
                feedback_ttl_seconds=settings.consequences_feedback_ttl_seconds,
                retention_days=settings.consequences_retention_days,
            )
        elif settings.consequences_db and settings.consequences_recording:
            app.state.consequences = ConsequencesStore(
                settings.consequences_db,
                busy_timeout_ms=settings.consequences_busy_timeout_ms,
                feedback_ttl_seconds=settings.consequences_feedback_ttl_seconds,
            )
        else:
            app.state.consequences = None
        app.state.peer_pressure = PeerPressureService(
            settings.peer_pressure_dir,
            registry,
            room_ttl_seconds=settings.peer_pressure_room_ttl_seconds,
            hand_size=settings.peer_pressure_hand_size,
            minimum_players=settings.peer_pressure_minimum_players,
            disconnect_timeout_seconds=settings.peer_pressure_disconnect_timeout_seconds,
            max_rooms=settings.peer_pressure_max_rooms,
            max_players=settings.peer_pressure_max_players,
            min_free_mb=settings.peer_pressure_min_free_mb,
        )
        app.state.peer_pressure.cleanup_expired()
        app.state.ready = True
        yield
        app.state.ready = False

    app = FastAPI(title="Bad Decisions API", version=__version__, lifespan=lifespan, root_path=settings.root_path)
    # Starlette's own slash redirect drops root_path when the proxy strips the
    # prefix (it sent /bad-decisions/docs/ to /docs); http_error redirects instead.
    app.router.redirect_slashes = False
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=CLIENT_HEADERS, expose_headers=["ETag", "X-Request-ID", "X-Feedback-Token", "Retry-After"], max_age=600,
        )

    def summary(pack) -> dict[str, Any]:
        value = pack.metadata.model_dump(mode="json")
        value["counts"] = {"prompts": len(pack.prompts), "answers": len(pack.answers)}
        return value

    def etag(value: Any) -> str:
        return '"' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32] + '"'

    def error(request: Request, code: str, message: str, status: int, details: dict | None = None, headers: dict | None = None) -> JSONResponse:
        body = {"error": {"code": code, "message": message, "details": details or {}, "request_id": getattr(request.state, "request_id", None)}}
        return JSONResponse(body, status_code=status, headers=headers)

    def client_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def rate_limited(request: Request, bucket: str) -> JSONResponse | None:
        wait = limiter.retry_after(client_key(request), bucket)
        if wait is None:
            return None
        return error(request, "rate_limited", "Too many requests; slow down and try again", 429, {"bucket": bucket}, {"Retry-After": str(wait)})

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        started = time.monotonic()
        request.state.request_id = request_id
        request.state.client_id = valid_uuid(request.headers.get("x-client-id"))
        request.state.session_id = valid_uuid(request.headers.get("x-session-id"))
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled request failure request_id=%s", request_id)
            response = error(request, "internal_error", "Internal server error", 500)
        response.headers["X-Request-ID"] = request_id
        consequences = getattr(request.app.state, "consequences", None)
        if consequences is not None and request.url.path != "/healthz":
            try:
                route = getattr(request.scope.get("route"), "path", "unmatched")
                consequences.record_request(request_id=request_id, route=route, method=request.method, status=response.status_code, duration_ms=(time.monotonic() - started) * 1000, client_id=request.state.client_id, session_id=request.state.session_id)
                if getattr(request.state, "consequences_round_id", None):
                    consequences.link_request(request_id, request.state.consequences_round_id)
            except Exception:
                logger.warning("consequences request telemetry could not be stored")
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
            request.method, request.url.path, response.status_code, (time.monotonic() - started) * 1000, request_id,
        )
        return response

    @app.exception_handler(BadDecisionsError)
    async def domain_error(request: Request, exc: BadDecisionsError):
        return error(request, exc.code, exc.message, ERROR_STATUS.get(exc.code, 400), exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Location, message, and type only: never echo the submitted input back.
        errors = [{"loc": list(item.get("loc", ())), "msg": item.get("msg", ""), "type": item.get("type", "")} for item in exc.errors()]
        return error(request, "request_validation", "Request validation failed", 422, {"errors": errors})

    @app.exception_handler(PeerPressureError)
    async def peer_pressure_error(request: Request, exc: PeerPressureError):
        return error(request, exc.reason, exc.message, exc.status, exc.details())

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            path = request.scope["path"]
            stripped = path.rstrip("/")
            if stripped and stripped != path and not stripped.startswith("//") and any(
                route.matches({**request.scope, "path": stripped})[0] != Match.NONE for route in app.router.routes
            ):
                query = request.scope.get("query_string", b"").decode("latin-1")
                target = request.scope.get("root_path", "").rstrip("/") + stripped + (f"?{query}" if query else "")
                return RedirectResponse(target, status_code=307)
            return error(request, "path_not_found", "Path not found", 404)
        if exc.status_code == 405:
            return error(request, "method_not_allowed", "Method not allowed", 405)
        return error(request, "http_error", "HTTP error", exc.status_code, {"status": exc.status_code})

    # --- service ---------------------------------------------------------------

    @app.get("/healthz", response_model=Health, responses={503: {"model": Health}})
    def health(request: Request):
        """Unversioned on purpose: every client version uses it for update checks."""
        ready = bool(getattr(request.app.state, "ready", False))
        registry: Registry | None = getattr(request.app.state, "registry", None)
        return JSONResponse(
            {"status": "ok" if ready else "unavailable", "version": __version__, "pack_count": len(registry.packs) if registry else 0},
            status_code=200 if ready else 503,
        )

    @app.api_route("/v1", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"], include_in_schema=False)
    @app.api_route("/v1/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"], include_in_schema=False)
    def v1_removed(request: Request, rest: str = ""):
        return error(
            request, "api_version_removed",
            "The /v1 API was removed in Bad Decisions 2.0; use /v2. Upgrade regret with brew upgrade regret or pip install --upgrade bad-decisions-client.",
            410, {"current_api": API},
        )

    @app.get(f"{API}/manage/status", include_in_schema=False)
    def management_status(request: Request):
        expected = settings.management_token
        supplied = request.headers.get("authorization", "")
        if not expected or not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:], expected):
            return error(request, "unauthorized", "Valid management credentials are required", 401)
        registry: Registry = request.app.state.registry
        return {"status": "ok", "version": __version__, "pack_count": len(registry.packs)}

    # --- web client --------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def web_index(request: Request):
        root_path = request.scope.get("root_path", "").rstrip("/")
        document = files("bad_decisions").joinpath("web", "index.html").read_text(encoding="utf-8")
        document = document.replace("__WEB_BASE__", f"{root_path}/assets/").replace("__ASSET_VERSION__", web_asset_version)
        return HTMLResponse(document, headers={"Cache-Control": "no-cache"})

    @app.get("/peerpressure", include_in_schema=False)
    @app.get("/peerpressure/", include_in_schema=False)
    def web_together(request: Request):
        root_path = request.scope.get("root_path", "").rstrip("/")
        document = files("bad_decisions").joinpath("web", "together.html").read_text(encoding="utf-8")
        document = document.replace("__WEB_BASE__", f"{root_path}/assets/").replace("__ASSET_VERSION__", web_asset_version)
        return HTMLResponse(document, headers={"Cache-Control": "no-cache"})

    @app.get("/web", include_in_schema=False)
    @app.get("/web/", include_in_schema=False)
    def removed_web(request: Request):
        root_path = request.scope.get("root_path", "").rstrip("/")
        return error(request, "path_removed", "The web client moved to the service root", 410, {"current_path": f"{root_path}/"})

    @app.get("/web/together", include_in_schema=False)
    @app.get("/web/together/", include_in_schema=False)
    def removed_web_together(request: Request):
        root_path = request.scope.get("root_path", "").rstrip("/")
        return error(request, "path_removed", "The Peer Pressure client moved", 410, {"current_path": f"{root_path}/peerpressure"})

    @app.get("/assets/{asset:path}", include_in_schema=False)
    def web_file(asset: str, request: Request):
        media_type = WEB_ASSETS.get(asset)
        if media_type is None:
            return error(request, "path_not_found", "Path not found", 404)
        # Documents carry a digest of the complete web bundle, so a rootless
        # same-version review deploy cannot pair new HTML with stale immutable assets.
        versioned = request.query_params.get("v") == web_asset_version
        cache = "public, max-age=31536000, immutable" if versioned else "no-cache"
        return FileResponse(str(files("bad_decisions").joinpath("web", asset)), media_type=media_type, headers={"Cache-Control": cache})

    # --- packs and rounds --------------------------------------------------------

    def cached(request: Request, value: Any, tag: str):
        headers = {"ETag": tag, "Cache-Control": PACK_CACHE}
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(value, headers=headers)

    @app.get(f"{API}/packs", response_model=list[PackSummary], responses=ERRORS)
    def list_packs(request: Request):
        return cached(request, list(request.app.state.pack_summaries.values()), request.app.state.packs_etag)

    @app.get(f"{API}/packs/{{pack_id}}", response_model=PackSummary, responses=ERRORS)
    def get_pack(pack_id: str, request: Request):
        value = request.app.state.pack_summaries.get(pack_id)
        if value is None:
            return error(request, "pack_not_found", f"Pack not found: {pack_id}", 404, {"available_packs": list(request.app.state.registry.ids)})
        return cached(request, value, request.app.state.pack_etags[pack_id])

    @app.get(f"{API}/round", response_model=RoundResponse, responses=ERRORS)
    def round_endpoint(
        request: Request,
        packs: Annotated[str | None, Query()] = None,
        prompt_packs: Annotated[str | None, Query()] = None,
        answer_packs: Annotated[str | None, Query()] = None,
    ):
        allowed = {"packs", "prompt_packs", "answer_packs"}
        unknown = sorted(set(request.query_params) - allowed)
        repeated = sorted(key for key in allowed if len(request.query_params.getlist(key)) > 1)
        if unknown:
            return error(request, "unknown_query_parameter", "Unknown query parameter", 400, {"parameters": unknown})
        if repeated:
            return error(request, "repeated_query_parameter", "Selector parameters must occur once", 400, {"parameters": repeated})
        registry = request.app.state.registry
        # The registry never changes at runtime, so each selector resolves once.
        key = (packs, prompt_packs, answer_packs)
        resolved = pool_cache.get(key)
        if resolved is None:
            resolved = resolve_pools(registry, packs=packs, prompt_packs=prompt_packs, answer_packs=answer_packs)
            if len(pool_cache) < POOL_CACHE_SIZE:
                pool_cache[key] = resolved
        round_ = generate_from_resolved(resolved, registry)
        payload = round_.model_dump(mode="json")
        headers = {"Cache-Control": "no-store"}
        consequences: ConsequencesStore | None = request.app.state.consequences
        if consequences is not None:
            try:
                issued = consequences.record_round(round_, request_id=request.state.request_id, client_id=request.state.client_id, session_id=request.state.session_id, feedback_enabled=settings.consequences_feedback)
                payload["round_id"] = issued.round_id
                request.state.consequences_round_id = issued.round_id
                payload["combination_hash"] = issued.combination_hash
                payload["feedback"] = {"available": bool(issued.feedback_token), "url": f"{settings.root_path}{API}/rounds/{issued.round_id}/feedback", "expires_at": issued.expires_at}
                if issued.feedback_token:
                    headers["X-Feedback-Token"] = issued.feedback_token
            except Exception:
                logger.warning("consequences round could not be stored")
                payload["feedback"] = {"available": False}
        return JSONResponse(payload, headers=headers)

    # --- Consequences feedback ---------------------------------------------------

    def feedback_store(request: Request) -> ConsequencesStore | None:
        return getattr(request.app.state, "consequences", None)

    def feedback_response(request: Request, state: str, changed: bool, enjoyed: bool | None, created: bool, round_id: str):
        if state == "missing":
            return error(request, "feedback_not_found", "Feedback draw not found", 404)
        if state == "expired":
            return error(request, "feedback_expired", "Feedback capability has expired", 410)
        if enjoyed is None:
            return Response(status_code=204)
        return JSONResponse({"round_id": round_id, "enjoyed": enjoyed, "changed": changed}, status_code=201 if created else 200)

    def record_feedback(request: Request, round_id: str, token: str | None, enjoyed: bool | None):
        limited = rate_limited(request, "feedback")
        if limited is not None:
            return limited
        consequences = feedback_store(request)
        if consequences is None or not settings.consequences_feedback:
            return error(request, "feedback_unavailable", "Feedback is not available", 503)
        try:
            state, changed, value, created = consequences.feedback(round_id, token or "", enjoyed)
        except Exception:
            return error(request, "feedback_unavailable", "Feedback is temporarily unavailable", 503)
        return feedback_response(request, state, changed, value, created, round_id)

    @app.put(f"{API}/rounds/{{round_id}}/feedback", response_model=FeedbackResult, responses=ERRORS)
    def put_feedback(round_id: str, body: FeedbackInput, request: Request, x_feedback_token: Annotated[str | None, Header()] = None):
        return record_feedback(request, round_id, x_feedback_token, body.enjoyed)

    @app.delete(f"{API}/rounds/{{round_id}}/feedback", status_code=204, responses=ERRORS)
    def delete_feedback(round_id: str, request: Request, x_feedback_token: Annotated[str | None, Header()] = None):
        return record_feedback(request, round_id, x_feedback_token, None)

    @app.get(f"{API}/combinations/{{combination_hash}}/stats", responses=ERRORS)
    def combination_stats(combination_hash: str, request: Request):
        consequences = feedback_store(request)
        if not settings.consequences_public_stats or consequences is None:
            return error(request, "path_not_found", "Path not found", 404)
        value = consequences.stats(combination_hash)
        if value is None:
            return error(request, "combination_not_found", "Combination not found", 404)
        return value

    # --- Peer Pressure -----------------------------------------------------------
    # The bearer session token alone identifies the player.

    def peer_service(request: Request) -> PeerPressureService:
        return request.app.state.peer_pressure

    def peer_token(authorization: str | None, *, required: bool = True) -> str | None:
        if authorization and authorization.startswith("Bearer ") and authorization[7:]:
            return authorization[7:]
        if required:
            raise PeerPressureError("invalid_session", "Valid room credentials are required", status=401)
        return None

    rooms = f"{API}/peer-pressure/rooms"

    @app.post(rooms, status_code=201, responses=ERRORS)
    def create_peer_room(body: RoomInput, request: Request):
        return rate_limited(request, "rooms") or peer_service(request).create_room(body.room, body.packs)

    @app.post(f"{rooms}/{{room}}/join", responses=ERRORS)
    def join_peer_room(room: str, body: JoinInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        """Join by display name, or rejoin with the session token as a bearer credential."""
        limited = rate_limited(request, "join")
        if limited is not None:
            return limited
        return peer_service(request).join(
            room,
            body.display_name,
            session_token=peer_token(authorization, required=False),
            create=body.create,
            packs=body.packs,
        )

    @app.post(f"{rooms}/{{room}}/sync", responses=ERRORS)
    def sync_peer_room(room: str, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_service(request).sync(room, None, peer_token(authorization))

    @app.get(
        f"{rooms}/{{room}}/events",
        responses={200: {"content": {"text/event-stream": {}}}, **ERRORS},
    )
    async def peer_room_events(room: str, request: Request, authorization: Annotated[str | None, Header()] = None):
        """Stream this player's authoritative room projection whenever its revision changes."""
        service = peer_service(request)
        token = peer_token(authorization)
        initial = await asyncio.to_thread(service.project, room, None, token)

        async def stream():
            state = initial
            revision = -1
            keepalive_at = time.monotonic() + 15
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                current = int(state["room"]["revision"])
                if current != revision:
                    revision = current
                    yield sse_event("state", {"revision": revision, "state": state}, event_id=revision)
                    keepalive_at = time.monotonic() + 15
                await asyncio.sleep(0.5)
                try:
                    changed = await asyncio.to_thread(service.watch, room, None, token, revision)
                    if changed is not None:
                        state = changed
                except PeerPressureError as exc:
                    yield sse_event("error", {"code": exc.reason, "message": exc.message})
                    return
                if time.monotonic() >= keepalive_at:
                    yield ": keepalive\n\n"
                    keepalive_at = time.monotonic() + 15

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post(f"{rooms}/{{room}}/heartbeat", responses=ERRORS)
    def heartbeat_peer_room(room: str, body: HeartbeatInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_service(request).heartbeat(room, None, peer_token(authorization), body.revision)

    @app.get(f"{rooms}/{{room}}/state", responses=ERRORS)
    def peer_room_state(room: str, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_service(request).project(room, None, peer_token(authorization), touch=True)

    def peer_mutation(body: MutationInput, request: Request, authorization: str | None, action: str, **values):
        method = getattr(peer_service(request), action)
        return method(request.path_params["room"], None, peer_token(authorization), body.request_id, body.revision, **values)

    def mutation_route(action: str):
        # A closure, not a default argument: FastAPI would expose that as ?action=.
        def mutate(room: str, body: MutationInput, request: Request, authorization: Annotated[str | None, Header()] = None):
            return peer_mutation(body, request, authorization, action)
        return mutate

    for action in ("leave", "start", "advance", "end"):
        app.post(f"{rooms}/{{room}}/{action}", name=f"peer_{action}", responses=ERRORS)(mutation_route(action))

    @app.post(f"{rooms}/{{room}}/submit", responses=ERRORS)
    def submit_peer_answers(room: str, body: SubmissionInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_mutation(body, request, authorization, "submit", cards=body.card_instance_ids)

    @app.post(f"{rooms}/{{room}}/judge", responses=ERRORS)
    def choose_peer_consequence(room: str, body: JudgmentInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_mutation(body, request, authorization, "judge", submission_id=body.submission_id)

    return app
