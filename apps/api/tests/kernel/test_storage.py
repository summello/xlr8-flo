from __future__ import annotations

from collections.abc import Iterable, Mapping

import pytest

from flo.kernel.adapters import r2 as r2_module
from flo.kernel.adapters.minio import MinioStorage
from flo.kernel.config import Settings
from flo.kernel.storage import (
    R2Storage,
    Storage,
    create_storage,
    opaque_storage_key,
    verify_storage_round_trip,
)

OPAQUE_KEY = "00000000-0000-4000-8000-000000000001"


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        self.closed = True


class FakePaginator:
    def __init__(self, client: FakeS3Client) -> None:
        self._client = client

    def paginate(self, **kwargs: str) -> Iterable[Mapping[str, object]]:
        assert kwargs == {"Bucket": "flo-attachments"}
        yield {
            "Contents": [
                {"Key": key, "Size": len(data)}
                for key, data in sorted(self._client.objects.items())
            ]
        }


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.last_body: FakeBody | None = None
        self.now = 1_000
        self.presigned: dict[str, tuple[str, int]] = {}

    def put_object(self, **kwargs: object) -> object:
        assert kwargs["Bucket"] == "flo-attachments"
        assert kwargs["ContentType"] in {"application/pdf", "application/octet-stream"}
        key = kwargs["Key"]
        body = kwargs["Body"]
        assert isinstance(key, str)
        assert isinstance(body, bytes)
        self.objects[key] = body
        return {}

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        assert kwargs["Bucket"] == "flo-attachments"
        key = kwargs["Key"]
        assert isinstance(key, str)
        self.last_body = FakeBody(self.objects[key])
        return {"Body": self.last_body}

    def delete_object(self, **kwargs: object) -> object:
        key = kwargs["Key"]
        assert isinstance(key, str)
        del self.objects[key]
        return {}

    def generate_presigned_url(
        self,
        client_method: str,
        *,
        Params: Mapping[str, str],
        ExpiresIn: int,
    ) -> str:
        assert client_method == "get_object"
        assert Params == {"Bucket": "flo-attachments", "Key": OPAQUE_KEY}
        url = f"https://private.example/{OPAQUE_KEY}?signature=test"
        self.presigned[url] = (OPAQUE_KEY, self.now + ExpiresIn)
        return url

    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "list_objects_v2"
        return FakePaginator(self)

    def read_url(self, url: str) -> bytes:
        authorization = self.presigned.get(url)
        if authorization is None:
            raise PermissionError("object is not public")
        key, expires_at = authorization
        if self.now >= expires_at:
            raise PermissionError("presigned URL expired")
        return self.objects[key]


def test_object_round_trips_through_storage_port_and_remains_private() -> None:
    client = FakeS3Client()
    storage: Storage = R2Storage(client, "flo-attachments")
    payload = b"private attachment"

    storage.put(OPAQUE_KEY, payload, content_type="application/pdf")

    assert storage.get(OPAQUE_KEY) == payload
    assert client.last_body is not None and client.last_body.closed
    assert storage.usage_bytes() == len(payload)
    url = storage.presign_get(OPAQUE_KEY, 60)
    assert client.read_url(url) == payload
    with pytest.raises(PermissionError, match="not public"):
        client.read_url(f"https://private.example/{OPAQUE_KEY}")
    client.now += 60
    with pytest.raises(PermissionError, match="expired"):
        client.read_url(url)
    storage.delete(OPAQUE_KEY)
    assert storage.usage_bytes() == 0


def test_presigned_download_rejects_a_non_positive_ttl() -> None:
    storage = R2Storage(FakeS3Client(), "flo-attachments")

    with pytest.raises(ValueError, match="TTL must be positive"):
        storage.presign_get(OPAQUE_KEY, 0)


def test_user_filename_cannot_be_used_as_a_storage_key() -> None:
    storage = R2Storage(FakeS3Client(), "flo-attachments")

    with pytest.raises(ValueError, match="opaque UUID"):
        storage.put("quarterly-budget.xlsx", b"private", content_type="application/octet-stream")


def test_generated_storage_keys_are_canonical_opaque_uuids() -> None:
    first = opaque_storage_key()
    second = opaque_storage_key()

    assert first != second
    assert len(first) == 36
    assert len(second) == 36


def test_operator_smoke_round_trip_removes_its_private_object() -> None:
    client = FakeS3Client()
    storage = R2Storage(client, "flo-attachments")

    verify_storage_round_trip(storage)

    assert client.objects == {}


def test_storage_factory_passes_secret_values_only_to_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_client = FakeS3Client()

    def fake_boto_client(service: str, **kwargs: object) -> FakeS3Client:
        captured["service"] = service
        captured.update(kwargs)
        return fake_client

    monkeypatch.setattr(r2_module.boto3, "client", fake_boto_client)
    settings = Settings(
        storage_endpoint_url="https://account.r2.cloudflarestorage.com",
        storage_access_key_id="access-value",
        storage_secret_access_key="secret-value",
    )

    adapter = create_storage(settings)

    assert isinstance(adapter, R2Storage)
    assert captured["service"] == "s3"
    assert captured["endpoint_url"] == "https://account.r2.cloudflarestorage.com"
    assert captured["aws_access_key_id"] == "access-value"
    assert captured["aws_secret_access_key"] == "secret-value"
    assert captured["region_name"] == "auto"


def test_storage_provider_is_swapped_only_by_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(r2_module.boto3, "client", lambda *_args, **_kwargs: FakeS3Client())
    settings = Settings(
        storage_provider="minio",
        storage_endpoint_url="http://localhost:9000",
        storage_access_key_id="local-access",
        storage_secret_access_key="local-secret",
    )

    assert isinstance(create_storage(settings), MinioStorage)


def test_storage_factory_names_missing_configuration_without_echoing_values() -> None:
    secret = "do-not-disclose-this-value"
    settings = Settings(
        storage_endpoint_url="https://account.r2.cloudflarestorage.com",
        storage_access_key_id=secret,
    )

    with pytest.raises(RuntimeError) as failure:
        create_storage(settings)

    assert "S3_SECRET_ACCESS_KEY is not configured" in str(failure.value)
    assert secret not in str(failure.value)
