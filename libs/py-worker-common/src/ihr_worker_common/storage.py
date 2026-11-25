"""Object storage access (S3 API only, S-03). Writes use ContentMD5 (§3.2 step 7)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Protocol

from .errors import ProcessingError

STORAGE_TRIES = 3  # A.3 STORAGE_UNAVAILABLE: "S3 read/write failed after 3 tries"


class Storage(Protocol):
    def get_bytes(self, bucket: str, key: str) -> bytes: ...
    def put_bytes(self, bucket: str, key: str, data: bytes, mime_type: str) -> dict: ...
    def exists(self, bucket: str, key: str) -> bool: ...


def object_ref(bucket: str, key: str, data: bytes, mime_type: str) -> dict:
    return {
        "bucket": bucket,
        "key": key,
        "sha256": hashlib.sha256(data).hexdigest(),
        "sizeBytes": len(data),
        "mimeType": mime_type,
    }


def read_verified(storage: Storage, ref: dict) -> bytes:
    """Download an ObjectRef and verify its sha256 (INPUT_CHECKSUM_MISMATCH)."""
    data = storage.get_bytes(ref["bucket"], ref["key"])
    if hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise ProcessingError("INPUT_CHECKSUM_MISMATCH", "Input object checksum does not match.")
    return data


def put_json(storage: Storage, bucket: str, key: str, obj: object) -> dict:
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return storage.put_bytes(bucket, key, data, "application/json")


class InMemoryStorage:
    """Test/compose double with the same semantics."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    def get_bytes(self, bucket: str, key: str) -> bytes:
        try:
            return self.objects[(bucket, key)][0]
        except KeyError:
            raise ProcessingError("INPUT_NOT_FOUND", "Referenced object is missing.") from None

    def put_bytes(self, bucket: str, key: str, data: bytes, mime_type: str) -> dict:
        self.objects[(bucket, key)] = (data, mime_type)
        return object_ref(bucket, key, data, mime_type)

    def exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.objects


class S3Storage:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "s3",
            endpoint_url=os.environ["S3_INTERNAL_ENDPOINT"],
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
            region_name=os.environ.get("S3_REGION", "us-east-1"),
            config=Config(retries={"max_attempts": 1}, s3={"addressing_style": "path"}),
        )

    def _retry(self, fn):
        from botocore.exceptions import ClientError

        last: Exception | None = None
        for i in range(STORAGE_TRIES):
            try:
                return fn()
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404", "NotFound"):
                    raise ProcessingError("INPUT_NOT_FOUND", "Referenced object is missing.") from None
                last = e
            except Exception as e:  # connection errors
                last = e
            time.sleep(0.2 * (2 ** i))
        raise ProcessingError("STORAGE_UNAVAILABLE", "Object storage request failed after 3 tries.") from last

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return self._retry(lambda: self._client.get_object(Bucket=bucket, Key=key)["Body"].read())

    def put_bytes(self, bucket: str, key: str, data: bytes, mime_type: str) -> dict:
        md5 = base64.b64encode(hashlib.md5(data).digest()).decode()
        self._retry(lambda: self._client.put_object(
            Bucket=bucket, Key=key, Body=data, ContentType=mime_type, ContentMD5=md5))
        return object_ref(bucket, key, data, mime_type)

    def exists(self, bucket: str, key: str) -> bool:
        from botocore.exceptions import ClientError

        def head():
            try:
                self._client.head_object(Bucket=bucket, Key=key)
                return True
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise

        return self._retry(head)
