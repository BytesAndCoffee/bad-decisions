from __future__ import annotations

import logging
import hmac
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from fastapi import FastAPI, Body, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .consequences import ConsequencesStore, valid_uuid
from .engine import generate_from_resolved
from .errors import BadDecisionsError
from .models import Round
from .packs import Registry, load_registry, resolve_pools
from .peer_pressure import PeerPressureError, PeerPressureService
from .settings import Settings

REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enjoyed: StrictBool


class PeerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RoomInput(PeerModel):
    room: str


class JoinInput(PeerModel):
    display_name: str
    player_id: str | None = None
    session_token: str | None = None
    create: StrictBool = True


class SessionInput(PeerModel):
    player_id: str


class HeartbeatInput(SessionInput):
    revision: StrictInt = Field(ge=0)


class MutationInput(HeartbeatInput):
    request_id: str


class SubmissionInput(MutationInput):
    card_instance_ids: list[str]


class JudgmentInput(MutationInput):
    submission_id: str


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
            app.state.registry,
            room_ttl_seconds=settings.peer_pressure_room_ttl_seconds,
            hand_size=settings.peer_pressure_hand_size,
            minimum_players=settings.peer_pressure_minimum_players,
            disconnect_timeout_seconds=settings.peer_pressure_disconnect_timeout_seconds,
        )
        app.state.peer_pressure.cleanup_expired()
        app.state.ready = True
        yield
        app.state.ready = False

    app = FastAPI(title="Bad Decisions API", version=__version__, lifespan=lifespan, root_path=settings.root_path)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        started = time.monotonic()
        request.state.request_id = request_id
        request.state.consequences_client_id = valid_uuid(request.headers.get("x-regret-client-id"))
        request.state.consequences_session_id = valid_uuid(request.headers.get("x-regret-session-id"))
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled request failure request_id=%s", request_id)
            response = JSONResponse(envelope("internal_error", "Internal server error", {"request_id": request_id}), status_code=500)
        response.headers["X-Request-ID"] = request_id
        consequences = getattr(request.app.state, "consequences", None)
        if consequences is not None and request.url.path != "/healthz":
            try:
                route = getattr(request.scope.get("route"), "path", "unmatched")
                consequences.record_request(request_id=request_id, route=route, method=request.method, status=response.status_code, duration_ms=(time.monotonic() - started) * 1000, client_id=request.state.consequences_client_id, session_id=request.state.consequences_session_id)
                if getattr(request.state, "consequences_round_id", None): consequences.link_request(request_id, request.state.consequences_round_id)
            except Exception:
                logger.warning("consequences request telemetry could not be stored")
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

    @app.exception_handler(PeerPressureError)
    async def peer_pressure_error(_request: Request, exc: PeerPressureError):
        return JSONResponse(exc.nack(), status_code=exc.status)

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

    @app.get("/v1/manage/status", include_in_schema=False)
    def management_status(request: Request):
        expected = settings.management_token
        supplied = request.headers.get("authorization", "")
        if not expected or not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:], expected):
            return JSONResponse(envelope("unauthorized", "Valid management credentials are required"), status_code=401)
        registry: Registry = request.app.state.registry
        return {"status": "ok", "version": __version__, "pack_count": len(registry.packs)}

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

Peer Pressure multiplayer:
  regret together ROOM_ID
  Protocol documentation: PEER_PRESSURE.md

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

    @app.get("/v1/round")
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
        round_ = generate_from_resolved(resolved, registry)
        payload = round_.model_dump(mode="json")
        consequences: ConsequencesStore | None = request.app.state.consequences
        if consequences is not None:
            try:
                issued = consequences.record_round(round_, request_id=request.state.request_id, client_id=request.state.consequences_client_id, session_id=request.state.consequences_session_id, feedback_enabled=settings.consequences_feedback)
                payload["round_id"] = issued.round_id
                request.state.consequences_round_id = issued.round_id
                payload["combination_hash"] = issued.combination_hash
                payload["feedback"] = {"available": bool(issued.feedback_token), "url": f"{settings.root_path}/v1/rounds/{issued.round_id}/feedback", "expires_at": issued.expires_at}
                if issued.feedback_token:
                    request.state.feedback_token = issued.feedback_token
            except Exception:
                logger.warning("consequences round could not be stored")
                payload["feedback"] = {"available": False}
        response = JSONResponse(payload)
        if getattr(request.state, "feedback_token", None):
            response.headers["X-Regret-Feedback-Token"] = request.state.feedback_token
        response.headers["Cache-Control"] = "no-store"
        return response

    def feedback_store(request: Request) -> ConsequencesStore | None:
        return getattr(request.app.state, "consequences", None)

    def feedback_response(state: str, changed: bool, enjoyed: bool | None, created: bool, round_id: str):
        if state == "missing":
            return JSONResponse(envelope("feedback_not_found", "Feedback draw not found"), status_code=404)
        if state == "expired":
            return JSONResponse(envelope("feedback_expired", "Feedback capability has expired"), status_code=410)
        if enjoyed is None:
            return JSONResponse(content=None, status_code=204)
        return JSONResponse({"round_id": round_id, "enjoyed": enjoyed, "changed": changed}, status_code=201 if created else 200)

    @app.put("/v1/rounds/{round_id}/feedback")
    def put_feedback(round_id: str, body: FeedbackInput, request: Request, x_regret_feedback_token: Annotated[str | None, Header()] = None):
        consequences = feedback_store(request)
        if consequences is None or not settings.consequences_feedback:
            return JSONResponse(envelope("feedback_unavailable", "Feedback is not available"), status_code=503)
        try:
            state, changed, enjoyed, created = consequences.feedback(round_id, x_regret_feedback_token or "", body.enjoyed)
        except Exception:
            return JSONResponse(envelope("feedback_unavailable", "Feedback is temporarily unavailable"), status_code=503)
        return feedback_response(state, changed, enjoyed, created, round_id)

    @app.delete("/v1/rounds/{round_id}/feedback", status_code=204)
    def delete_feedback(round_id: str, request: Request, x_regret_feedback_token: Annotated[str | None, Header()] = None):
        consequences = feedback_store(request)
        if consequences is None or not settings.consequences_feedback:
            return JSONResponse(envelope("feedback_unavailable", "Feedback is not available"), status_code=503)
        try:
            state, changed, enjoyed, created = consequences.feedback(round_id, x_regret_feedback_token or "", None)
        except Exception:
            return JSONResponse(envelope("feedback_unavailable", "Feedback is temporarily unavailable"), status_code=503)
        return feedback_response(state, changed, enjoyed, created, round_id)

    @app.get("/v1/combinations/{combination_hash}/stats")
    def combination_stats(combination_hash: str, request: Request):
        if not settings.consequences_public_stats:
            return JSONResponse(envelope("path_not_found", "Path not found"), status_code=404)
        consequences = feedback_store(request)
        if consequences is None:
            return JSONResponse(envelope("path_not_found", "Path not found"), status_code=404)
        value = consequences.stats(combination_hash)
        if value is None:
            return JSONResponse(envelope("combination_not_found", "Combination not found"), status_code=404)
        return value

    def peer_service(request: Request) -> PeerPressureService:
        return request.app.state.peer_pressure

    def peer_token(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer ") or not authorization[7:]:
            raise PeerPressureError("invalid_session", "Valid room credentials are required", status=401)
        return authorization[7:]

    @app.post("/v1/peer-pressure/rooms", status_code=201)
    def create_peer_room(body: RoomInput, request: Request):
        return peer_service(request).create_room(body.room)

    @app.post("/v1/peer-pressure/rooms/{room}/join")
    def join_peer_room(room: str, body: JoinInput, request: Request):
        return peer_service(request).join(
            room,
            body.display_name,
            player_id=body.player_id,
            session_token=body.session_token,
            create=body.create,
        )

    @app.post("/v1/peer-pressure/rooms/{room}/sync")
    def sync_peer_room(room: str, body: SessionInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_service(request).sync(room, body.player_id, peer_token(authorization))

    @app.post("/v1/peer-pressure/rooms/{room}/heartbeat")
    def heartbeat_peer_room(room: str, body: HeartbeatInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_service(request).heartbeat(room, body.player_id, peer_token(authorization), body.revision)

    def peer_mutation(body: MutationInput, request: Request, authorization: str | None, action: str, **values):
        service = peer_service(request)
        token = peer_token(authorization)
        method = getattr(service, action)
        return method(request.path_params["room"], body.player_id, token, body.request_id, body.revision, **values)

    @app.post("/v1/peer-pressure/rooms/{room}/leave")
    def leave_peer_room(room: str, body: MutationInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_mutation(body, request, authorization, "leave")

    @app.post("/v1/peer-pressure/rooms/{room}/start")
    def start_peer_room(room: str, body: MutationInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_mutation(body, request, authorization, "start")

    @app.post("/v1/peer-pressure/rooms/{room}/submit")
    def submit_peer_decision(room: str, body: SubmissionInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_mutation(body, request, authorization, "submit", cards=body.card_instance_ids)

    @app.post("/v1/peer-pressure/rooms/{room}/judge")
    def choose_peer_consequence(room: str, body: JudgmentInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_mutation(body, request, authorization, "judge", submission_id=body.submission_id)

    @app.post("/v1/peer-pressure/rooms/{room}/advance")
    def advance_peer_room(room: str, body: MutationInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_mutation(body, request, authorization, "advance")

    @app.get("/v1/peer-pressure/rooms/{room}/state")
    def peer_room_state(
        room: str,
        request: Request,
        x_peer_pressure_player: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ):
        return peer_service(request).project(room, x_peer_pressure_player or "", peer_token(authorization), touch=True)

    @app.delete("/v1/peer-pressure/rooms/{room}")
    def end_peer_room(room: str, body: MutationInput, request: Request, authorization: Annotated[str | None, Header()] = None):
        return peer_mutation(body, request, authorization, "end")

    return app
