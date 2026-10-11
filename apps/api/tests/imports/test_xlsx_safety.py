import io
import zipfile

import pytest

from flo.modules.imports.service import XLSX_TYPE
from flo.modules.imports.xlsx_reader import MAX_XML_SIZE, NS, read_xlsx, write_xlsx
from tests.imports.test_upload import admin, upload
from tests.imports.test_upload import storage as storage


def workbook():
    return write_xlsx([["code", "amount", "on_date"], ["A", "1234.56", "2026-10-08"]], "v1")


def altered(parts=None, transform=None, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(workbook())) as original:
        with zipfile.ZipFile(output, "w", compression) as result:
            for name in original.namelist():
                data = original.read(name)
                if transform:
                    data = transform(name, data)
                result.writestr(name, data)
            for name, data in (parts or {}).items():
                result.writestr(name, data)
    return output.getvalue()


@pytest.mark.parametrize(
    "payload,detail",
    [
        (
            lambda: altered(
                transform=lambda n, d: (
                    d.replace(
                        b"<ns0:is><ns0:t>1234.56</ns0:t></ns0:is>",
                        b"<ns0:f>SUM(1,2)</ns0:f><ns0:v>3</ns0:v>",
                    )
                    if n == "xl/worksheets/sheet1.xml"
                    else d
                )
            ),
            "B2",
        ),
        (lambda: altered(parts={"xl/vbaProject.bin": b"macro"}), "Macros"),
        (lambda: altered(parts={"xl/externalLinks/externalLink1.xml": b"<link/>"}), "Macros"),
        (
            lambda: altered(parts={"bomb": b"0" * 1000000}, compression=zipfile.ZIP_DEFLATED),
            "ratio",
        ),
        (
            lambda: altered(
                transform=lambda n, d: (
                    b'<!DOCTYPE x [<!ENTITY e "BAD">]>' + d
                    if n == "xl/worksheets/sheet1.xml"
                    else d
                )
            ),
            "entities",
        ),
        (lambda: altered(parts={"other.xml": b"<!DOCTYPE x><x/>"}), "doctypes"),
        (
            lambda: altered(
                parts={"other.xml": '<!DOCTYPE x [<!ENTITY e "BAD">]><x/>'.encode("utf-16")}
            ),
            "entities",
        ),
        (
            lambda: altered(
                parts={
                    "other.rels": b"<Relationships><Relationship "
                    b'TargetMode="External" Target="https://example.test"/></Relationships>'
                }
            ),
            "External",
        ),
        (
            lambda: altered(
                parts={"another.xml": b'<Types ContentType="application/macroEnabled"/>'}
            ),
            "Macro-enabled",
        ),
        (
            lambda: altered(
                parts={
                    "xl/worksheets/sheet2.xml": (
                        f'<worksheet xmlns="{NS[1:-1]}"><c r="D4"><f>1+1</f></c></worksheet>'
                    ).encode()
                }
            ),
            "D4",
        ),
    ],
)
def test_xlsx_rejects_planted_unsafe_content(imports_db, storage, payload, detail):
    admin(imports_db)
    response = upload(imports_db, storage, payload(), "data.xlsx", XLSX_TYPE)
    assert response.status_code == 422, response.text
    assert detail in response.json()["errors"][0]["message"]
    assert not storage.objects


def test_member_count_limit(imports_db, storage):
    admin(imports_db)
    payload = altered(parts={f"member{i}": b"" for i in range(101)})
    response = upload(imports_db, storage, payload, "data.xlsx", XLSX_TYPE)
    assert response.status_code == 422
    assert "too many" in response.text


def test_uncompressed_size_checked_before_read(monkeypatch):
    payload = workbook()
    info = zipfile.ZipInfo("huge")
    info.file_size = MAX_XML_SIZE + 1
    info.compress_size = info.file_size
    monkeypatch.setattr(zipfile.ZipFile, "infolist", lambda self: [info])

    def never_read(*args, **kwargs):
        raise AssertionError("read oversized archive")

    monkeypatch.setattr(zipfile.ZipFile, "read", never_read)
    from flo.kernel.errors import ProblemError

    with pytest.raises(ProblemError) as exc:
        read_xlsx(payload)
    assert "50 MiB" in exc.value.errors[0].message


@pytest.mark.parametrize("payload", [b"not a ZIP", altered(parts={"bad.xml": b"<broken"})])
def test_corrupt_workbook_rejected(imports_db, storage, payload):
    admin(imports_db)
    response = upload(imports_db, storage, payload, "data.xlsx", XLSX_TYPE)
    assert response.status_code == 422
    assert not storage.objects


def test_xlsx_shared_strings_and_sparse_cells():
    sheet = f'''<worksheet xmlns="{NS[1:-1]}"><sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
    <row r="2"><c r="A2" t="s"><v>2</v></c></row></sheetData></worksheet>'''.encode()
    shared = f'''<sst xmlns="{NS[1:-1]}"><si><t>code</t></si><si><t>amount</t></si>
    <si><r><t>EX</t></r><r><t>AMPLE</t></r></si></sst>'''.encode()
    payload = altered(
        parts={"xl/sharedStrings.xml": shared},
        transform=lambda n, d: sheet if n == "xl/worksheets/sheet1.xml" else d,
    )
    assert [list(row) for row in read_xlsx(payload)] == [["code", "amount"], ["EXAMPLE"]]


def test_sparse_cells_do_not_expand_to_a_dense_rectangle():
    sheet = f'''<worksheet xmlns="{NS[1:-1]}"><sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>code</t></is></c></row>
    <row r="2"><c r="XFD2"><v>1</v></c></row></sheetData></worksheet>'''.encode()
    payload = altered(transform=lambda n, d: sheet if n == "xl/worksheets/sheet1.xml" else d)
    row = read_xlsx(payload)[1]
    assert len(row) == 16384
    assert len(row.cells) == 1
    assert row[0] == ""
    assert row[16383] == "1"


@pytest.mark.parametrize(
    "cell",
    [
        '<c r="A0"/>',
        '<c r="XFE1"/>',
        '<c r="A1"/><c r="A1"/>',
        '<c r="A1" t="s"><v>-1</v></c>',
        '<c r="A1" t="s"><v>99</v></c>',
        '<c r="A1" t="e"><v>#VALUE!</v></c>',
        '<c r="A1" t="b"><v>1</v></c>',
    ],
)
def test_invalid_cells_rejected(cell):
    from flo.kernel.errors import ProblemError

    sheet = (
        f'<worksheet xmlns="{NS[1:-1]}"><sheetData><row r="1">{cell}</row></sheetData></worksheet>'
    )
    payload = altered(
        transform=lambda n, d: sheet.encode() if n == "xl/worksheets/sheet1.xml" else d
    )
    with pytest.raises(ProblemError):
        read_xlsx(payload)


@pytest.mark.parametrize("kind", ["duplicate", "encrypted"])
def test_unsafe_zip_metadata_rejected(monkeypatch, kind):
    from flo.kernel.errors import ProblemError

    payload = workbook()
    info = zipfile.ZipInfo("member")
    info.file_size = info.compress_size = 1
    info.flag_bits = 1 if kind == "encrypted" else 0
    monkeypatch.setattr(
        zipfile.ZipFile, "infolist", lambda self: [info, info] if kind == "duplicate" else [info]
    )
    with pytest.raises(ProblemError):
        read_xlsx(payload)


def test_xlsx_row_cap():
    from flo.kernel.errors import ProblemError

    sheet = (
        f'<worksheet xmlns="{NS[1:-1]}"><sheetData>'
        + '<row r="50002"/>'
        + "</sheetData></worksheet>"
    )
    payload = altered(
        transform=lambda n, d: sheet.encode() if n == "xl/worksheets/sheet1.xml" else d
    )
    with pytest.raises(ProblemError) as error:
        read_xlsx(payload)
    assert "50,000" in error.value.errors[0].message


def sheet_rows(rows):
    sheet = f'<worksheet xmlns="{NS[1:-1]}"><sheetData>{rows}</sheetData></worksheet>'
    return altered(transform=lambda n, d: sheet.encode() if n == "xl/worksheets/sheet1.xml" else d)


def test_skipped_row_numbers_preserve_spreadsheet_indexes(imports_db, storage):
    from flo.modules.imports.service import check_types, read_rows
    from flo.modules.imports.templates import get_template

    payload = sheet_rows(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>code</t></is></c></row>'
        '<row r="5"><c r="A5" t="inlineStr"><is><t>DATA</t></is></c></row>'
    )
    rows = read_xlsx(payload)
    assert [list(row) for row in rows] == [["code"], [], [], [], ["DATA"]]
    assert all(row.cells == {} for row in rows[1:4])
    assert list(read_rows(payload, "data.xlsx")[0]) == ["code"]
    check_types(get_template("test_fixture"), rows, {"code": "code"})
    admin(imports_db)
    response = upload(imports_db, storage, payload, "data.xlsx", XLSX_TYPE)
    assert response.status_code == 201, response.text
    assert response.json()["headers"] == ["code"]
    assert response.json()["row_count"] == 4


@pytest.mark.parametrize("references", [[None], ["bad"], ["0"], ["50002"], ["2", "1"], ["1", "1"]])
def test_invalid_row_numbers_rejected(imports_db, storage, references):
    payload = sheet_rows("".join("<row" + (f' r="{r}"' if r else "") + "/>" for r in references))
    admin(imports_db)
    response = upload(imports_db, storage, payload, "data.xlsx", XLSX_TYPE)
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["field"] == "file"
    assert not storage.objects


@pytest.mark.parametrize(
    "kind,field", [("header", "headers"), ("width", "rows[5]"), ("type", "rows[5].amount")]
)
def test_sparse_row_structure_and_mapping_checks(imports_db, storage, kind, field):
    header = '<row r="1"><c r="A1" t="inlineStr"><is><t>amount</t></is></c></row>'
    data = '<row r="5"><c r="A5"><v>bad</v></c></row>'
    if kind == "header":
        header = ""
    elif kind == "width":
        data = '<row r="5"><c r="B5"><v>1</v></c></row>'
    admin(imports_db)
    response = upload(imports_db, storage, sheet_rows(header + data), "data.xlsx", XLSX_TYPE)
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["field"] == field


def test_duplicate_worksheet_member_rejected(imports_db, storage):
    formula = f'<worksheet xmlns="{NS[1:-1]}"><c r="A1"><f>1+1</f></c></worksheet>'.encode()
    with pytest.warns(UserWarning, match="Duplicate name"):
        payload = altered(parts={"xl/worksheets/sheet1.xml": formula})
    admin(imports_db)
    response = upload(imports_db, storage, payload, "data.xlsx", XLSX_TYPE)
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["field"] == "file"
    assert "duplicate ZIP members" in response.json()["errors"][0]["message"]
    assert not storage.objects
