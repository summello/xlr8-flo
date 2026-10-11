"""Bounded streaming multipart reader without a multipart dependency."""

from dataclasses import dataclass
from email.message import Message
from email.parser import BytesHeaderParser
from hashlib import sha256

from starlette.requests import Request

from flo.kernel.errors import ErrorCode, ProblemError
from flo.modules.imports.parsers import invalid

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
        cap = MAX_FILE_SIZE if name == "file" else 1024
        if len(target) + len(data) > cap:
            if name == "file":
                raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE)
            raise invalid("template", "Template name is too long.")
        target.extend(data)
        if name == "file":
            digest.update(data)

    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_FILE_SIZE + MAX_OVERHEAD:
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
                    or name not in {"file", "template", "external_key"}
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
    if phase != "done" or not {"template", "file"}.issubset(parts):
        raise invalid("file", "Incomplete multipart upload.")
    headers, data = parts["file"]
    try:
        template = bytes(parts["template"][1]).decode("utf-8")
    except UnicodeDecodeError:
        raise invalid("template", "Template name must be UTF-8.") from None
    filename = headers.get_filename()
    if not filename:
        raise invalid("file", "A file name is required.")
    try:
        external_key = (
            bytes(parts["external_key"][1]).decode("utf-8") if "external_key" in parts else None
        )
    except UnicodeDecodeError:
        raise invalid("external_key", "Import key must be UTF-8.") from None
    return Upload(
        template,
        filename,
        headers.get_content_type(),
        bytes(data),
        digest.hexdigest(),
        external_key,
    )
