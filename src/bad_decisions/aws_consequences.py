"""DynamoDB implementation of Consequences for horizontally scaled AWS services."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError

from .consequences import IssuedRound, round_identity
from .models import Round

_RETRY = Config(retries={"total_max_attempts": 4, "mode": "adaptive"})
_SERIALIZER = TypeSerializer()

def _item(value: dict[str, object]) -> dict[str, dict[str, object]]:
    return {key: _SERIALIZER.serialize(item) for key, item in value.items()}

class DynamoConsequencesStore:
    """Consequences backend using atomic DynamoDB transactions and TTL retention."""

    def __init__(self, table_name: str, *, feedback_ttl_seconds: int = 604800, retention_days: int = 90) -> None:
        self.table_name = table_name
        self.feedback_ttl_seconds = feedback_ttl_seconds
        self.retention_seconds = retention_days * 86400
        self.client = boto3.client("dynamodb", config=_RETRY)

    @staticmethod
    def _now() -> int:
        return int(time.time())

    def record_round(self, round_: Round, *, request_id: str | None, client_id: str | None, session_id: str | None, feedback_enabled: bool) -> IssuedRound:
        now = self._now()
        round_id = str(uuid.uuid4())
        prompt, answers, combo = round_identity(round_)
        token = secrets.token_urlsafe(32) if feedback_enabled else None
        expires = now + self.feedback_ttl_seconds
        packs = round_.provenance
        elements = [{
            "role": role, "slot": slot, "hash": digest, "text": text,
            "pack": card.pack, "pack_version": packs[card.pack].version,
            "card": card.id, "source": card.source_ref or "",
            "license": packs[card.pack].license_id, "attribution": packs[card.pack].attribution,
        } for role, slot, digest, text, card in (
            [("prompt", 0, prompt, round_.black.repr, round_.black)] +
            [("answer", index, digest, card.text, card) for index, (digest, card) in enumerate(zip(answers, round_.white))]
        )]
        round_item: dict[str, object] = {
            "pk": f"ROUND#{round_id}", "sk": "META", "kind": "round",
            "round_id": round_id, "occurred_at": now, "combination_hash": combo,
            "prompt_hash": prompt, "answer_hashes": answers,
            "template": round_.black.template, "elements": elements,
            "feedback_expires_at": expires,
            "token_hash": hashlib.sha256(token.encode()).hexdigest() if token else "",
            "expires_at": now + self.retention_seconds,
        }
        if request_id: round_item["request_id"] = request_id
        if client_id: round_item["client_id"] = client_id
        if session_id: round_item["session_id"] = session_id
        self.client.transact_write_items(TransactItems=[
            {"Put": {"TableName": self.table_name, "Item": _item(round_item), "ConditionExpression": "attribute_not_exists(pk)"}},
            {"Update": {
                "TableName": self.table_name,
                "Key": _item({"pk": f"COMBO#{combo}", "sk": "STATS"}),
                "UpdateExpression": "ADD draw_count :one SET kind=:kind, updated_at=:now, expires_at=:ttl",
                "ExpressionAttributeValues": _item({":one": 1, ":kind": "stats", ":now": now, ":ttl": now + self.retention_seconds}),
            }},
        ])
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires)) if token else None
        return IssuedRound(round_id, combo, token, iso)

    def record_request(self, *, request_id: str, route: str, method: str, status: int, duration_ms: float, client_id: str | None, session_id: str | None) -> None:
        now = self._now()
        value: dict[str, object] = {
            "pk": f"REQUEST#{request_id}", "sk": "META", "kind": "request",
            "request_id": request_id, "occurred_at": now, "route": route[:160],
            "method": method[:12], "status": status,
            "duration_ms": Decimal(str(round(min(duration_ms, 600000), 3))),
            "expires_at": now + self.retention_seconds,
        }
        if client_id: value["client_id"] = client_id
        if session_id: value["session_id"] = session_id
        self.client.put_item(TableName=self.table_name, Item=_item(value), ConditionExpression="attribute_not_exists(pk)")

    def link_request(self, request_id: str, round_id: str) -> None:
        self.client.update_item(
            TableName=self.table_name, Key=_item({"pk": f"REQUEST#{request_id}", "sk": "META"}),
            UpdateExpression="SET round_id=:round", ExpressionAttributeValues=_item({":round": round_id}),
        )

    def feedback(self, round_id: str, token: str, enjoyed: bool | None) -> tuple[str, bool, bool | None, bool]:
        if not token or len(token) > 512:
            return "missing", False, None, False
        key = {"pk": f"ROUND#{round_id}", "sk": "META"}
        expected = hashlib.sha256(token.encode()).hexdigest()
        for _attempt in range(3):
            response = self.client.get_item(TableName=self.table_name, Key=_item(key), ConsistentRead=True)
            raw = response.get("Item")
            if not raw:
                return "missing", False, None, False
            from boto3.dynamodb.types import TypeDeserializer
            decoder = TypeDeserializer()
            item = {name: decoder.deserialize(value) for name, value in raw.items()}
            if not hmac.compare_digest(str(item.get("token_hash", "")), expected):
                return "missing", False, None, False
            now = self._now()
            if int(item["feedback_expires_at"]) < now:
                return "expired", False, None, False
            old = item.get("vote")
            if old == enjoyed:
                return "ok", False, enjoyed, False
            combo = str(item["combination_hash"])
            condition = "attribute_not_exists(vote)" if old is None else "vote=:old"
            values: dict[str, object] = {":now": now}
            if old is not None: values[":old"] = bool(old)
            if enjoyed is None:
                update = "REMOVE vote SET vote_updated_at=:now"
            else:
                update = "SET vote=:new, vote_updated_at=:now"
                values[":new"] = enjoyed
            enjoy_delta = int(enjoyed is True) - int(old is True)
            regret_delta = int(enjoyed is False) - int(old is False)
            try:
                self.client.transact_write_items(TransactItems=[
                    {"Update": {
                        "TableName": self.table_name, "Key": _item(key),
                        "ConditionExpression": condition, "UpdateExpression": update,
                        "ExpressionAttributeValues": _item(values),
                    }},
                    {"Update": {
                        "TableName": self.table_name,
                        "Key": _item({"pk": f"COMBO#{combo}", "sk": "STATS"}),
                        "UpdateExpression": "ADD enjoy_count :enjoy, regret_count :regret SET updated_at=:now",
                        "ExpressionAttributeValues": _item({":enjoy": enjoy_delta, ":regret": regret_delta, ":now": now}),
                    }},
                ])
                return "ok", True, enjoyed, old is None and enjoyed is not None
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                    raise
        raise RuntimeError("feedback changed concurrently; retry")

    def stats(self, combo: str) -> dict[str, Any] | None:
        response = self.client.get_item(TableName=self.table_name, Key=_item({"pk": f"COMBO#{combo}", "sk": "STATS"}), ConsistentRead=False)
        if not response.get("Item"):
            return None
        from boto3.dynamodb.types import TypeDeserializer
        decoder = TypeDeserializer()
        item = {name: decoder.deserialize(value) for name, value in response["Item"].items()}
        draws, enjoy, regret = (int(item.get(name, 0)) for name in ("draw_count", "enjoy_count", "regret_count"))
        votes = enjoy + regret
        return {"combination_hash": combo, "draw_count": draws, "enjoy_count": enjoy, "regret_count": regret, "vote_count": votes, "score": None if not votes else (enjoy-regret)/votes}

    def report(self) -> dict[str, Any]:
        counts = {"request": 0, "round": 0, "stats": 0}
        errors = votes = enjoy = 0
        durations: list[float] = []
        paginator = self.client.get_paginator("scan")
        from boto3.dynamodb.types import TypeDeserializer
        decoder = TypeDeserializer()
        for page in paginator.paginate(TableName=self.table_name, ProjectionExpression="kind,#status,duration_ms,vote", ExpressionAttributeNames={"#status": "status"}):
            for raw in page.get("Items", []):
                item = {name: decoder.deserialize(value) for name, value in raw.items()}
                kind = str(item.get("kind", ""))
                counts[kind] = counts.get(kind, 0) + 1
                if kind == "request":
                    errors += int(item.get("status", 0) >= 400)
                    durations.append(float(item.get("duration_ms", 0)))
                if kind == "round" and "vote" in item:
                    votes += 1
                    enjoy += int(bool(item["vote"]))
        return {
            "schema_version": 1,
            "requests": {"count": counts["request"], "average_duration_ms": round(sum(durations)/len(durations), 2) if durations else 0, "errors": errors},
            "draws": {"recorded": counts["round"], "combinations": counts["stats"]},
            "feedback": {"votes": votes, "enjoy": enjoy, "regret": votes-enjoy},
        }
