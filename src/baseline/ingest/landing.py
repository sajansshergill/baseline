"""Step 1 of every upload: save the original bytes somewhere they'll never change.

Files are stored by their SHA-256 fingerprint:

    raw/<tenant>/<sha[:2]>/<sha>/<filename>
    quarantine/<tenant>/<sha[:2]>/<sha>/<filename>

Two backends with the same interface:
- LocalStore: a folder on disk (default for development and tests)
- S3Store: AWS S3, or LocalStack when BASELINE_S3_ENDPOINT_URL is set
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from baseline.config import Settings, get_settings


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def copy(self, src: str, dst: str) -> None: ...
    def list(self, prefix: str) -> list[str]: ...


class LocalStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"key escapes storage root: {key!r}")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic rename: readers never see half a file

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def copy(self, src: str, dst: str) -> None:
        self.put(dst, self.get(src))

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix) if prefix else self.root
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()
        )


class S3Store:
    def __init__(self, bucket: str, endpoint_url: str | None = None, region: str = "us-east-1"):
        import boto3  # imported here so local runs don't need AWS set up

        self.bucket = bucket
        self.s3 = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def put(self, key: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def copy(self, src: str, dst: str) -> None:
        self.s3.copy_object(
            Bucket=self.bucket, Key=dst, CopySource={"Bucket": self.bucket, "Key": src}
        )

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        ):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)


def get_store(settings: Settings | None = None) -> ObjectStore:
    settings = settings or get_settings()
    if settings.storage == "s3":
        return S3Store(settings.s3_bucket, settings.s3_endpoint_url, settings.aws_region)
    return LocalStore(settings.local_storage_dir)


@dataclass(frozen=True)
class LandedFile:
    filename: str
    sha256: str
    raw_key: str
    size_bytes: int


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(filename: str) -> str:
    """Keep only the base name and harmless characters (no ../ tricks)."""
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "upload"


def _key(area: str, tenant_id: str, sha: str, filename: str) -> str:
    return f"{area}/{tenant_id}/{sha[:2]}/{sha}/{safe_filename(filename)}"


def quarantine_key(tenant_id: str, landed: LandedFile) -> str:
    return _key("quarantine", tenant_id, landed.sha256, landed.filename)


def land_file(store: ObjectStore, tenant_id: str, filename: str, data: bytes) -> LandedFile:
    """Save the original file. If these exact bytes are already stored, don't write again."""
    sha = sha256_of(data)
    key = _key("raw", tenant_id, sha, filename)
    if not store.exists(key):
        store.put(key, data)
    return LandedFile(
        filename=safe_filename(filename), sha256=sha, raw_key=key, size_bytes=len(data)
    )