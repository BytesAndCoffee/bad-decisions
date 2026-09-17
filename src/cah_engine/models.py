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


class BlackCard(FrozenModel):
    id: str = Field(pattern=ID_PATTERN)
    repr: str
    template: str
    slots: int = Field(strict=True, gt=0)
    pack: str = Field(pattern=ID_PATTERN)
    source_ref: str | None = None

    @field_validator("repr", "template")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def valid_template(self) -> BlackCard:
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


class WhiteCard(FrozenModel):
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
    black: tuple[BlackCard, ...]
    white: tuple[WhiteCard, ...]

    @field_validator("black", "white", mode="before")
    @classmethod
    def json_arrays_to_tuples(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def consistent(self) -> Pack:
        if self.schema_version != 1:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        if not self.black and not self.white:
            raise ValueError("pack must contain at least one card")
        seen: set[str] = set()
        for color, cards in (("black", self.black), ("white", self.white)):
            for card in cards:
                if card.pack != self.metadata.id:
                    raise ValueError(f"{color} card {card.id}: pack must equal {self.metadata.id}")
                if card.id in seen:
                    raise ValueError(f"duplicate card id: {card.id}")
                seen.add(card.id)
        return self


class Selection(FrozenModel):
    black_packs: tuple[str, ...]
    white_packs: tuple[str, ...]


class PackProvenance(FrozenModel):
    version: str
    attribution: str
    license_id: str
    license_url: str | None
    sources: tuple[Source, ...]


class Round(FrozenModel):
    black: BlackCard
    white: tuple[WhiteCard, ...]
    result: str
    selection: Selection
    provenance: dict[str, PackProvenance]

    @model_validator(mode="after")
    def answer_arity(self) -> Round:
        if len(self.white) != self.black.slots:
            raise ValueError("white answer count must equal black slots")
        return self


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()
