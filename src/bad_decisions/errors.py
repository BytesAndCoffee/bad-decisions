from __future__ import annotations

from typing import Any


class BadDecisionsError(Exception):
    code = "bad_decisions_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class PackConfigurationError(BadDecisionsError):
    code = "pack_configuration"


class SelectorError(BadDecisionsError):
    code = "invalid_selector"


class UnknownPackError(SelectorError):
    code = "unknown_pack"


class EmptyPoolError(SelectorError):
    code = "empty_pool"


class InsufficientCapacityError(SelectorError):
    code = "insufficient_white_capacity"


class RenderError(BadDecisionsError):
    code = "answer_arity"
