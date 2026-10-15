"""Bounded streaming multipart reader without a multipart dependency."""

from dataclasses import dataclass

from starlette.requests import Request

from flo.kernel.multipart import read_multipart

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_OVERHEAD = 64 * 1024


@dataclass(frozen=True)
class Upload:
    template: str
    file_name: str
    content_type: str
    data: bytes
    checksum: str
    external_key: str | None = None


async def read_upload(request: Request) -> Upload:
    upload = await read_multipart(
        request,
        text_fields={"template": 1024, "external_key": 1024},
        required=frozenset({"template"}),
        max_file_bytes=MAX_FILE_SIZE,
    )
    return Upload(
        upload.text["template"],
        upload.file_name,
        upload.content_type,
        upload.data,
        upload.checksum,
        upload.text.get("external_key"),
    )
