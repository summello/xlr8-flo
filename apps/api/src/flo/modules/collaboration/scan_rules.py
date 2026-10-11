"""Allow-list and signature checks, never trusting a client media type."""

import io
import re
import unicodedata
import zipfile
import zlib
from pathlib import PurePosixPath
from xml.etree import ElementTree

from flo.kernel.ports.scan import ScanResult

MAX_FILE_BYTES = 10 * 1024 * 1024
OFFICE = {
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "/word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    ),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "/xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    ),
}


def safe_name(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    name = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    name = re.sub(r"\s+", " ", name).strip()
    suffix = PurePosixPath(name).suffix
    return (name[: 120 - len(suffix)] + suffix) if len(name) > 120 else name


class BasicChecksScanner:
    def scan(self, data: bytes, *, filename: str) -> ScanResult:
        extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
        media: str | None = None
        if not 1 <= len(data) <= MAX_FILE_BYTES:
            return ScanResult("rejected", None, "Choose a non-empty file of at most 10 MiB.")
        signatures = {
            ".pdf": (b"%PDF-", "application/pdf"),
            ".png": (b"\x89PNG\r\n\x1a\n", "image/png"),
            ".jpg": (b"\xff\xd8\xff", "image/jpeg"),
            ".jpeg": (b"\xff\xd8\xff", "image/jpeg"),
        }
        if extension in signatures:
            signature, kind = signatures[extension]
            if data.startswith(signature):
                media = kind
        elif extension in {".csv", ".txt"}:
            try:
                data.decode("utf-8")
                if b"\0" not in data:
                    media = "text/csv" if extension == ".csv" else "text/plain"
            except UnicodeDecodeError:
                pass
        elif extension in OFFICE and data.startswith(b"PK\x03\x04"):
            kind, main, content = OFFICE[extension]
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    entries = archive.infolist()
                    # ponytail: cap expanded metadata at 1 MiB; revisit for larger Office metadata.
                    if any(
                        e.filename.replace("\\", "/").split("/")[-1].lower() == "vbaproject.bin"
                        for e in entries
                    ):
                        return ScanResult("rejected", None, "Macro-enabled files are not allowed.")
                    info = archive.getinfo("[Content_Types].xml")
                    if info.file_size <= 1024 * 1024 and main.lstrip("/") in archive.namelist():
                        root = ElementTree.fromstring(archive.read(info))
                        if any(
                            e.tag
                            == "{http://schemas.openxmlformats.org/package/2006/content-types}Override"
                            and e.get("PartName") == main
                            and e.get("ContentType") == content
                            for e in root
                        ):
                            media = kind
            except (
                zipfile.BadZipFile,
                KeyError,
                ElementTree.ParseError,
                RuntimeError,
                OSError,
                NotImplementedError,
                ValueError,
                zlib.error,
            ):
                pass
        if media is None:
            return ScanResult(
                "rejected", None, "The extension and content must match an allowed file type."
            )
        return ScanResult("basic_checks_passed", media, None)
