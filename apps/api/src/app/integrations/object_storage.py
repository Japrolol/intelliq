"""Private S3 upload storage shared by API and workers; never falls back to disk."""

import base64
import hashlib
from contextlib import closing
from pathlib import PurePosixPath

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from src.app.config import Settings


class ObjectStorageError(RuntimeError):
    """Sanitized storage failure suitable for the service boundary."""


def _validate_key(key: str) -> None:
    if not key.startswith("knowledge/") or ".." in PurePosixPath(key).parts or "\\" in key:
        raise ObjectStorageError("Invalid upload object key.")


def _client(settings: Settings):
    if not settings.s3_access_key_id or not settings.s3_secret_access_key:
        raise ObjectStorageError("Upload storage credentials are not configured.")
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=20,
            retries={"mode": "standard", "total_max_attempts": 3},
            request_checksum_calculation="when_required",
        ),
    )


def put_object(settings: Settings, key: str, content: bytes) -> None:
    """Persist original bytes under their content-addressed, organization-prefixed key."""
    _validate_key(key)
    try:
        with closing(_client(settings)) as client:
            if settings.s3_create_bucket:
                try:
                    client.head_bucket(Bucket=settings.s3_bucket)
                except ClientError as exc:
                    if str(exc.response.get("Error", {}).get("Code")) not in {
                        "404",
                        "NoSuchBucket",
                    }:
                        raise
                    try:
                        client.create_bucket(Bucket=settings.s3_bucket)
                    except ClientError as creation:
                        if (
                            creation.response.get("Error", {}).get("Code")
                            != "BucketAlreadyOwnedByYou"
                        ):
                            raise
            client.put_object(
                Bucket=settings.s3_bucket,
                Key=key,
                Body=content,
                ContentLength=len(content),
                ContentType="application/octet-stream",
                ContentMD5=base64.b64encode(
                    hashlib.md5(content, usedforsecurity=False).digest()
                ).decode(),
                Metadata={"sha256": hashlib.sha256(content).hexdigest()},
            )
    except (BotoCoreError, ClientError) as exc:
        raise ObjectStorageError("Upload storage is unavailable; please retry.") from exc


def get_object(settings: Settings, key: str, *, max_bytes: int) -> bytes:
    """Read bounded bytes; caller must validate organization ownership before calling."""
    _validate_key(key)
    try:
        with closing(_client(settings)) as client:
            response = client.get_object(Bucket=settings.s3_bucket, Key=key)
            body = response["Body"]
            try:
                if response["ContentLength"] > max_bytes:
                    raise ObjectStorageError("Uploaded file exceeds the allowed size.")
                content = body.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise ObjectStorageError("Uploaded file exceeds the allowed size.")
                expected = response.get("Metadata", {}).get("sha256")
                if expected and hashlib.sha256(content).hexdigest() != expected:
                    raise ObjectStorageError("Uploaded file integrity check failed.")
                return content
            finally:
                body.close()
    except (BotoCoreError, ClientError) as exc:
        raise ObjectStorageError("Uploaded file is unavailable; please retry.") from exc
