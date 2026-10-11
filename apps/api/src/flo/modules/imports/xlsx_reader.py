"""Bounded OOXML reader and template writer using only the standard library."""

import io
import posixpath
import re
import zipfile
from collections.abc import Sequence
from typing import overload
from xml.etree import ElementTree as ET

from flo.modules.imports.parsers import invalid

MAX_ROWS = 50000
MAX_XML_SIZE = 50 * 1024 * 1024
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class SparseRow(Sequence[str]):
    """Preserve sparse cells without allocating thousands of blanks per row."""

    def __init__(self, cells: dict[int, str]) -> None:
        self.cells = cells
        self.width = max(cells, default=0)

    def __len__(self) -> int:
        return self.width

    @overload
    def __getitem__(self, index: int) -> str: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[str]: ...

    def __getitem__(self, index: int | slice) -> str | Sequence[str]:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(self.width))]
        if index < 0:
            index += self.width
        if not 0 <= index < self.width:
            raise IndexError(index)
        return self.cells.get(index + 1, "")


def read_xlsx(data: bytes) -> list[Sequence[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(members) > 100 or len(names) != len(set(names)):
                raise invalid("file", "Workbook has too many or duplicate ZIP members.")
            if sum(member.file_size for member in members) > MAX_XML_SIZE:
                raise invalid("file", "Workbook expands beyond 50 MiB.")
            for member in members:
                if member.file_size > 100 * max(1, member.compress_size) or member.flag_bits & 1:
                    raise invalid("file", "Unsafe ZIP compression ratio or encrypted member.")
                lowered = member.filename.lower()
                if "vbaproject" in lowered or "externallink" in lowered:
                    raise invalid("file", "Macros and external links are not allowed.")
            trees: dict[str, ET.Element] = {}
            formulas: list[str] = []
            for name in names:
                if name.endswith((".xml", ".rels")):
                    payload = archive.read(name)
                    # OOXML is UTF-8 or UTF-16; stripping NULs catches either declaration.
                    lexical = payload.replace(b"\x00", b"").upper()
                    if b"<!DOCTYPE" in lexical or b"<!ENTITY" in lexical:
                        raise invalid("file", "XML entities and doctypes are not allowed.")
                    tree = ET.fromstring(payload)
                    trees[name] = tree
                    for node in tree.iter():
                        if node.tag.rsplit("}", 1)[-1] == "Relationship":
                            if (
                                node.get("TargetMode", "").lower() == "external"
                                or "externallink" in node.get("Type", "").lower()
                            ):
                                raise invalid("file", "External workbook links are not allowed.")
                        if "macroenabled" in " ".join(node.attrib.values()).lower():
                            raise invalid("file", "Macro-enabled workbooks are not allowed.")
                    for cell in tree.iter(NS + "c"):
                        if cell.find(NS + "f") is not None:
                            formulas.append(f"{name}:{cell.get('r', '?')}")
            if formulas:
                raise invalid("file", "Formula cells are not allowed: " + ", ".join(formulas))
            workbook = trees["xl/workbook.xml"]
            relations = trees["xl/_rels/workbook.xml.rels"]
            sheet = workbook.find(NS + "sheets/" + NS + "sheet")
            if sheet is None:
                raise invalid("file", "Workbook must have a worksheet.")
            relation = next(item for item in relations if item.get("Id") == sheet.get(REL + "id"))
            target = relation.attrib["Target"]
            path = (
                target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            )
            tree = trees[path]
            shared = [
                "".join(text.text or "" for text in item.iter(NS + "t"))
                for item in trees.get("xl/sharedStrings.xml", ET.Element("empty"))
            ]
            rows: list[Sequence[str]] = []
            for row in tree.iter(NS + "row"):
                reference = row.get("r", "")
                if re.fullmatch(r"[1-9][0-9]*", reference) is None:
                    raise invalid("file", "A positive integer worksheet row number is required.")
                number = int(reference)
                if number <= len(rows):
                    raise invalid("file", "Worksheet row numbers must be strictly increasing.")
                if number > MAX_ROWS + 1:
                    raise invalid("file", "Maximum 50,000 data rows allowed.")
                rows.extend(SparseRow({}) for _ in range(number - len(rows) - 1))
                values: dict[int, str] = {}
                for cell in row.findall(NS + "c"):
                    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", cell.get("r", ""))
                    if match is None:
                        raise invalid("file", "Invalid cell address.")
                    index = 0
                    for char in match[1]:
                        index = index * 26 + ord(char) - ord("A") + 1
                    if index > 16384 or index in values:
                        raise invalid("file", "Invalid or duplicate cell column.")
                    value = cell.findtext(NS + "v", "")
                    if cell.get("t") == "s":
                        shared_index = int(value)
                        if shared_index < 0:
                            raise invalid("file", "Invalid shared string index.")
                        value = shared[shared_index]
                    elif cell.get("t") == "inlineStr":
                        value = "".join(text.text or "" for text in cell.iter(NS + "t"))
                    elif cell.get("t") in {"e", "b"}:
                        raise invalid(cell.get("r", "file"), "Use canonical text cell values.")
                    values[index] = value
                rows.append(SparseRow(values))
            return rows
    except (
        zipfile.BadZipFile,
        ET.ParseError,
        KeyError,
        IndexError,
        ValueError,
        StopIteration,
        RuntimeError,
        NotImplementedError,
    ):
        raise invalid("file", "Invalid XLSX structure. Upload a valid workbook.") from None


def write_xlsx(rows: list[list[str]], version: str) -> bytes:
    sheet = ET.Element(NS + "worksheet")
    sheet_data = ET.SubElement(sheet, NS + "sheetData")
    for number, values in enumerate(rows, 1):
        row = ET.SubElement(sheet_data, NS + "row", r=str(number))
        for index, value in enumerate(values, 1):
            letters = ""
            while index:
                index, rem = divmod(index - 1, 26)
                letters = chr(65 + rem) + letters
            cell = ET.SubElement(row, NS + "c", r=f"{letters}{number}", t="inlineStr")
            ET.SubElement(ET.SubElement(cell, NS + "is"), NS + "t").text = value
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<Types
 xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/xl/workbook.xml"
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
 <Override PartName="/xl/worksheets/sheet1.xml"
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
 <Override PartName="/docProps/core.xml"
 ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
 </Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<Relationships
 xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="r1"
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
 Target="xl/workbook.xml"/>
 <Relationship Id="r2"
 Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
 Target="docProps/core.xml"/></Relationships>""",
        )
        workbook = ET.Element(NS + "workbook")
        sheets = ET.SubElement(workbook, NS + "sheets")
        ET.SubElement(sheets, NS + "sheet", {"name": "Import", "sheetId": "1", REL + "id": "r1"})
        archive.writestr("xl/workbook.xml", ET.tostring(workbook))
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships
 xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="r1"
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
 Target="worksheets/sheet1.xml"/></Relationships>""",
        )
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(sheet))
        core = ET.Element(
            "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}coreProperties"
        )
        ET.SubElement(core, "{http://purl.org/dc/elements/1.1/}description").text = version
        archive.writestr("docProps/core.xml", ET.tostring(core))
    return output.getvalue()
