"""Signature table including renamed executables and macro-bearing packages."""

import io
import zipfile

import pytest

from flo.modules.collaboration.scan_rules import OFFICE, BasicChecksScanner, safe_name


def office(extension, macro=False):
    output = io.BytesIO()
    _, main, content = OFFICE[extension]
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Override PartName="{main}" ContentType="{content}"/></Types>',
        )
        archive.writestr(main.lstrip("/"), "<document/>")
        if macro:
            archive.writestr("word/vbaProject.bin", b"macro")
    return output.getvalue()


ALLOWED = [
    ("sample.pdf", b"%PDF-1.7\n", "application/pdf"),
    ("sample.png", b"\x89PNG\r\n\x1a\n", "image/png"),
    ("sample.jpg", b"\xff\xd8\xff", "image/jpeg"),
    ("sample.jpeg", b"\xff\xd8\xff", "image/jpeg"),
    ("sample.csv", b"a,b\n1,2", "text/csv"),
    ("sample.txt", b"hello", "text/plain"),
    ("sample.docx", office(".docx"), OFFICE[".docx"][0]),
    ("sample.xlsx", office(".xlsx"), OFFICE[".xlsx"][0]),
]
REFUSED = [
    ("evil.pdf", b"MZexecutable"),
    ("renamed.docx", office(".docx", True)),
    ("drawing.svg", b"<svg/>"),
    ("html.txt", b"<html>\0</html>"),
    ("empty.pdf", b""),
    ("wrong.docx", office(".xlsx")),
    ("bad.txt", b"\xff"),
    ("broken.docx", b"PK\x03\x04broken"),
]


@pytest.mark.parametrize("name,data,media", ALLOWED)
def test_allowed_signatures(name, data, media):
    result = BasicChecksScanner().scan(data, filename=name)
    assert result.verdict == "basic_checks_passed" and result.media_type == media


@pytest.mark.parametrize("name,data", REFUSED)
def test_refused_signatures(name, data):
    assert BasicChecksScanner().scan(data, filename=name).verdict == "rejected"


def test_safe_name_preserves_extension_and_strips_paths_controls():
    assert safe_name("../../etc/passwd.pdf") == "passwd.pdf"
    assert safe_name("C:\\folder\\a\x00  b.pdf") == "a b.pdf"
    name = safe_name("x" * 200 + ".pdf")
    assert len(name) == 120 and name.endswith(".pdf")


@pytest.mark.parametrize(
    "case", ["missing_main", "wrong_declaration", "oversized_metadata", "invalid_xml"]
)
def test_office_package_metadata_guards(case):
    output = io.BytesIO()
    _, main, content = OFFICE[".docx"]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if case != "missing_main":
            archive.writestr(main.lstrip("/"), "<document/>")
        if case == "wrong_declaration":
            content = OFFICE[".xlsx"][2]
        metadata = (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Override PartName="{main}" ContentType="{content}"/></Types>'
        )
        if case == "oversized_metadata":
            metadata += " " * (1024 * 1024)
        if case == "invalid_xml":
            metadata = "<broken"
        archive.writestr("[Content_Types].xml", metadata)
    assert BasicChecksScanner().scan(output.getvalue(), filename="file.docx").verdict == "rejected"


def test_scanner_size_guard():
    scanner = BasicChecksScanner()
    assert (
        scanner.scan(b"%PDF-" + b"x" * (10 * 1024 * 1024), filename="file.pdf").verdict
        == "rejected"
    )
