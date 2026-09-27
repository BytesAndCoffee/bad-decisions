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
    code = "insufficient_answer_capacity"


class RenderError(BadDecisionsError):
    code = "answer_arity"


class MissingExtraError(BadDecisionsError):
    """An optional feature's dependencies are not installed."""

    code = "missing_extra"
    FEATURES = {
        "aws": "AWS support",
        "aws-deploy": "AWS deployment (setup aws, deploy aws)",
        "tui": "the Consequences dashboard",
    }

    def __init__(self, extra: str):
        feature = self.FEATURES.get(extra, extra)
        super().__init__(
            f"{feature} needs optional dependencies: pip install 'bad-decisions[{extra}]' "
            f"(with pipx: pipx install --force 'bad-decisions[{extra}]')",
            {"extra": extra},
        )
        self.extra = extra
