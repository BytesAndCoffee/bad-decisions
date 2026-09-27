from __future__ import annotations

from string import Formatter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Source(FrozenModel):
    origin: str
    edition: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    retrieved: str | None = None
    license_evidence: str | None = None

    @field_validator("origin")
    @classmethod
    def nonempty_origin(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class PackMetadata(FrozenModel):
    id: str = Field(pattern=ID_PATTERN)
    name: str
    description: str
    version: str
    language: str
    custom: bool
    authors: tuple[str, ...]
    attribution: str
    license_id: str
    license_url: str | None
    license_notice: str
    sources: tuple[Source, ...]
    modifications: tuple[str, ...]

    @field_validator("authors", "sources", "modifications", mode="before")
    @classmethod
    def json_arrays_to_tuples(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator(
        "name", "description", "version", "language", "attribution", "license_id", "license_notice"
    )
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("id")
    @classmethod
    def reserved_id(cls, value: str) -> str:
        if value == "all":
            raise ValueError("'all' is reserved")
        return value


PACK_SCHEMA_VERSION = 2
# Schema 1 named prompts "black" cards (display text "repr") and answers "white"
# cards. It is still read, translated on load, so existing registries and
# archives keep working; everything written is schema 2.
_V1_KEYS = {"black": "prompts", "white": "answers"}


def upgrade_pack_v1(raw: Any) -> Any:
    """Translate a schema-1 pack document to schema 2; anything else is returned unchanged."""
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return raw
    upgraded = {key: value for key, value in raw.items() if key not in _V1_KEYS}
    upgraded["schema_version"] = PACK_SCHEMA_VERSION
    for old, new in _V1_KEYS.items():
        if old in raw:
            upgraded[new] = raw[old]
    prompts = upgraded.get("prompts")
    if isinstance(prompts, list):
        upgraded["prompts"] = [
            {("text" if key == "repr" else key): value for key, value in card.items()} if isinstance(card, dict) else card
            for card in prompts
        ]
    return upgraded


class Prompt(FrozenModel):
    """A card with one or more blanks, filled by ``slots`` answers."""

    id: str = Field(pattern=ID_PATTERN)
    text: str
    template: str
    slots: int = Field(strict=True, gt=0)
    pack: str = Field(pattern=ID_PATTERN)
    source_ref: str | None = None

    @field_validator("text", "template")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def valid_template(self) -> Prompt:
        try:
            parsed = tuple(Formatter().parse(self.template))
        except ValueError as exc:
            raise ValueError(f"invalid template braces: {exc}") from exc
        fields = 0
        for _, field, spec, conversion in parsed:
            if field is None:
                continue
            if field != "":
                raise ValueError("template fields must be anonymous {} placeholders")
            if spec:
                raise ValueError("template format specifications are forbidden")
            if conversion is not None:
                raise ValueError("template conversions are forbidden")
            fields += 1
        if fields != self.slots:
            raise ValueError(f"template has {fields} fields but slots is {self.slots}")
        return self


class Answer(FrozenModel):
    """A card that fills one blank in a prompt."""

    id: str = Field(pattern=ID_PATTERN)
    text: str
    pack: str = Field(pattern=ID_PATTERN)
    source_ref: str | None = None

    @field_validator("text")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class Pack(FrozenModel):
    schema_version: int
    metadata: PackMetadata
    prompts: tuple[Prompt, ...]
    answers: tuple[Answer, ...]

    @model_validator(mode="before")
    @classmethod
    def read_schema_1(cls, value: Any) -> Any:
        return upgrade_pack_v1(value)

    @field_validator("prompts", "answers", mode="before")
    @classmethod
    def json_arrays_to_tuples(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def consistent(self) -> Pack:
        if self.schema_version != PACK_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        if not self.prompts and not self.answers:
            raise ValueError("pack must contain at least one card")
        seen: set[str] = set()
        for kind, cards in (("prompt", self.prompts), ("answer", self.answers)):
            for card in cards:
                if card.pack != self.metadata.id:
                    raise ValueError(f"{kind} {card.id}: pack must equal {self.metadata.id}")
                if card.id in seen:
                    raise ValueError(f"duplicate card id: {card.id}")
                seen.add(card.id)
        return self


class Selection(FrozenModel):
    prompt_packs: tuple[str, ...]
    answer_packs: tuple[str, ...]


class PackProvenance(FrozenModel):
    version: str
    attribution: str
    license_id: str
    license_url: str | None
    sources: tuple[Source, ...]


class Round(FrozenModel):
    prompt: Prompt
    answers: tuple[Answer, ...]
    result: str
    selection: Selection
    provenance: dict[str, PackProvenance]

    @model_validator(mode="after")
    def answer_arity(self) -> Round:
        if len(self.answers) != self.prompt.slots:
            raise ValueError("answer count must equal the prompt's slots")
        return self


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()
