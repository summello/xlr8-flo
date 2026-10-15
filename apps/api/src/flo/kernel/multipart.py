"""Bounded streaming multipart reader without a multipart dependency."""

from collections.abc import Mapping
from dataclasses import dataclass
from email.message import Message
from email.parser import BytesHeaderParser
from hashlib import sha256

from starlette.requests import Request

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError


def invalid(field: str, message: str) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED, errors=(ProblemFieldError(field=field, message=message),)
    )


MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_OVERHEAD = 64 * 1024


@dataclass(frozen=True)
class MultipartUpload:
    file_name: str
    content_type: str
    data: bytes
    checksum: str
    text: dict[str, str]


async def read_multipart(
    request: Request,
    *,
    file_field: str = "file",
    text_fields: Mapping[str, int],
    required: frozenset[str],
    max_file_bytes: int,
) -> MultipartUpload:
    content = Message()
    content["content-type"] = request.headers.get("content-type", "")
    boundary = content.get_boundary()
    if content.get_content_type() != "multipart/form-data" or not boundary:
        raise ProblemError(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
    try:
        marker = b"--" + boundary.encode("ascii")
    except UnicodeEncodeError:
        raise invalid("file", "Invalid multipart boundary.") from None
    if len(marker) > 202 or b"\r" in marker or b"\n" in marker:
        raise invalid("file", "Invalid multipart boundary.")
    delimiter = b"\r\n" + marker
    buffer = bytearray()
    phase = "start"
    parts: dict[str, tuple[Message, bytearray]] = {}
    name = ""
    total = 0
    digest = sha256()

    def consume(data: bytes) -> None:
        target = parts[name][1]
        cap = max_file_bytes if name == file_field else text_fields[name]
        if len(target) + len(data) > cap:
            if name == file_field:
                raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE)
            raise invalid("template", "Template name is too long.")
        target.extend(data)
        if name == file_field:
            digest.update(data)

    async for chunk in request.stream():
        total += len(chunk)
        if total > max_file_bytes + MAX_OVERHEAD:
            raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE)
        buffer.extend(chunk)
        while True:
            if phase == "start":
                if len(buffer) < len(marker) + 2:
                    break
                if not buffer.startswith(marker + b"\r\n"):
                    raise invalid("file", "Invalid multipart structure.")
                del buffer[: len(marker) + 2]
                phase = "headers"
            elif phase == "headers":
                end = buffer.find(b"\r\n\r\n")
                if end < 0:
                    if len(buffer) > 16384:
                        raise invalid("file", "Multipart headers are too large.")
                    break
                if end > 16384:
                    raise invalid("file", "Multipart headers are too large.")
                headers = BytesHeaderParser().parsebytes(bytes(buffer[:end]))
                name = str(headers.get_param("name", header="content-disposition") or "")
                if (
                    headers.get_content_disposition() != "form-data"
                    or name not in {file_field, *text_fields}
                    or name in parts
                ):
                    raise invalid("file", "Send exactly one template and one file part.")
                parts[name] = (headers, bytearray())
                del buffer[: end + 4]
                phase = "data"
            elif phase == "data":
                end = buffer.find(delimiter)
                if end < 0:
                    safe = max(0, len(buffer) - len(delimiter) - 2)
                    consume(bytes(buffer[:safe]))
                    del buffer[:safe]
                    break
                if len(buffer) < end + len(delimiter) + 2:
                    break
                suffix = bytes(buffer[end + len(delimiter) : end + len(delimiter) + 2])
                if suffix not in {b"--", b"\r\n"}:
                    consume(bytes(buffer[: end + 2]))
                    del buffer[: end + 2]
                    continue
                consume(bytes(buffer[:end]))
                del buffer[: end + len(delimiter) + 2]
                phase = "done" if suffix == b"--" else "headers"
            else:
                if bytes(buffer) not in {b"", b"\r", b"\r\n"}:
                    raise invalid("file", "Unexpected multipart trailing data.")
                break
    if phase != "done" or not {file_field, *required}.issubset(parts):
        raise invalid("file", "Incomplete multipart upload.")
    headers, data = parts[file_field]
    text: dict[str, str] = {}
    for field in text_fields:
        if field not in parts:
            continue
        try:
            text[field] = bytes(parts[field][1]).decode("utf-8")
        except UnicodeDecodeError:
            message = (
                "Template name must be UTF-8."
                if field == "template"
                else "Import key must be UTF-8."
            )
            raise invalid(field, message) from None
    filename = headers.get_filename()
    if not filename:
        raise invalid("file", "A file name is required.")
    return MultipartUpload(
        filename, headers.get_content_type(), bytes(data), digest.hexdigest(), text
    )
