from __future__ import annotations

from collections.abc import Iterable, Mapping

import pytest

from flo.kernel import storage as storage_module
from flo.kernel.config import Settings
from flo.kernel.storage import R2Storage, Storage, create_storage, verify_storage_round_trip


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
        assert Params == {"Bucket": "flo-attachments", "Key": "opaque-id"}
        return f"https://private.example/opaque-id?ttl={ExpiresIn}"

    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "list_objects_v2"
        return FakePaginator(self)


def test_object_round_trips_through_storage_port_and_remains_private() -> None:
    client = FakeS3Client()
    storage: Storage = R2Storage(client, "flo-attachments")
    payload = b"private attachment"

    storage.put("opaque-id", payload, content_type="application/pdf")

    assert storage.get("opaque-id") == payload
    assert client.last_body is not None and client.last_body.closed
    assert storage.usage_bytes() == len(payload)
    assert storage.presign_get("opaque-id", 60) == "https://private.example/opaque-id?ttl=60"
    storage.delete("opaque-id")
    assert storage.usage_bytes() == 0


def test_presigned_download_rejects_a_non_positive_ttl() -> None:
    storage = R2Storage(FakeS3Client(), "flo-attachments")

    with pytest.raises(ValueError, match="TTL must be positive"):
        storage.presign_get("opaque-id", 0)


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

    monkeypatch.setattr(storage_module.boto3, "client", fake_boto_client)
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
