"""Bridge AWS CLI login sessions into boto3 without persisting credentials."""
from __future__ import annotations

import json
import os
import subprocess

import boto3

from .errors import PackConfigurationError


def local_session(profile: str | None, region: str | None) -> boto3.Session:
    """Return a boto3 session, exporting short-lived AWS CLI credentials when profiled.

    ``aws login`` credentials are understood by AWS CLI v2 but not necessarily by
    the separately installed botocore used by this package.  The CLI's supported
    process-format export bridges that gap in memory; values are never printed or
    written by Bad Decisions.
    """
    if not profile:
        return boto3.Session(region_name=region or None)
    environment = os.environ.copy()
    if region:
        environment["AWS_DEFAULT_REGION"] = region
    completed = subprocess.run(
        ["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"],
        env=environment, check=False, text=True, capture_output=True,
    )
    if completed.returncode:
        message = completed.stderr.strip() or "AWS CLI could not export credentials"
        raise PackConfigurationError(f"AWS profile {profile!r}: {message}")
    try:
        payload = json.loads(completed.stdout)
        access_key = payload["AccessKeyId"]
        secret_key = payload["SecretAccessKey"]
        session_token = payload.get("SessionToken")
        if not isinstance(access_key, str) or not isinstance(secret_key, str):
            raise TypeError("credential fields must be strings")
        if session_token is not None and not isinstance(session_token, str):
            raise TypeError("session token must be a string")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise PackConfigurationError(f"AWS profile {profile!r}: invalid exported credentials") from exc
    return boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token,
        region_name=region or None,
    )
