from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType

import boto3
from botocore.config import Config
from pydantic import ValidationError

from .errors import PackConfigurationError
from .models import Pack

MAX_PACK_BYTES = 4 * 1024 * 1024


def load_s3_packs(bucket: str, prefix: str = "packs/") -> Mapping[str, Pack]:
    client = boto3.client("s3", config=Config(retries={"total_max_attempts": 3, "mode": "adaptive"}, connect_timeout=5, read_timeout=15))
    loaded: dict[str, Pack] = {}
    paginator = client.get_paginator("list_objects_v2")
    keys = sorted(key for key in paginator.paginate(Bucket=bucket, Prefix=prefix).search("Contents[].Key") if key and key.endswith(".json"))
    for key in keys:
        response = client.get_object(Bucket=bucket, Key=key)
        if response.get("ContentLength", MAX_PACK_BYTES + 1) > MAX_PACK_BYTES:
            response["Body"].close()
            raise PackConfigurationError(f"s3://{bucket}/{key}: pack exceeds {MAX_PACK_BYTES} bytes")
        with response["Body"] as body:
            raw_bytes = body.read(MAX_PACK_BYTES + 1)
        if len(raw_bytes) > MAX_PACK_BYTES:
            raise PackConfigurationError(f"s3://{bucket}/{key}: pack exceeds {MAX_PACK_BYTES} bytes")
        try:
            pack = Pack.model_validate(json.loads(raw_bytes.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            raise PackConfigurationError(f"s3://{bucket}/{key}: invalid pack: {exc}") from exc
        if pack.metadata.id in loaded:
            raise PackConfigurationError(f"duplicate pack id {pack.metadata.id!r}: s3://{bucket}/{key}")
        loaded[pack.metadata.id] = pack
    return MappingProxyType(dict(sorted(loaded.items())))
