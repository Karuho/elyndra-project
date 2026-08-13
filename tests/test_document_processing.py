from __future__ import annotations

import io
import zipfile

from pypdf import PdfWriter

from elyndra.documents import process_document


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_json_validation_reports_valid_and_invalid() -> None:
    valid = process_document("config.json", b'{"enabled": true}')
    invalid = process_document("config.json", b'{"enabled": }')

    assert valid.validation_status == "valid"
    assert valid.processor == "python-json"
    assert invalid.validation_status == "invalid"
    assert invalid.diagnostics["line"] == 1


def test_yaml_is_validated_with_safe_loader() -> None:
    result = process_document("items.yml", b"items:\n  sword: true\n")
    invalid = process_document("broken.yml", b"items: [broken\n")

    assert result.validation_status == "valid"
    assert result.processor == "pyyaml-safe"
    assert invalid.validation_status == "invalid"
    assert invalid.diagnostics["line"] == 2


def test_docx_text_is_extracted_without_office_runtime() -> None:
    data = _zip_bytes(
        {
            "word/document.xml": (
                '<w:document xmlns:w="urn:w"><w:body><w:p>'
                "<w:r><w:t>Hola Elyndra</w:t></w:r>"
                "</w:p></w:body></w:document>"
            )
        }
    )

    result = process_document("nota.docx", data)

    assert result.kind == "document"
    assert result.validation_status == "valid"
    assert "Hola Elyndra" in result.extracted_text


def test_pptx_and_xlsx_extract_local_text() -> None:
    pptx = _zip_bytes(
        {
            "ppt/slides/slide1.xml": (
                '<p:sld xmlns:p="urn:p" xmlns:a="urn:a">'
                "<a:t>Memoria episódica</a:t></p:sld>"
            )
        }
    )
    xlsx = _zip_bytes(
        {
            "xl/sharedStrings.xml": (
                '<sst xmlns="urn:x"><si><t>Elyndra</t></si></sst>'
            ),
            "xl/worksheets/sheet1.xml": (
                '<worksheet xmlns="urn:x"><sheetData><row>'
                '<c t="s"><v>0</v></c><c><v>42</v></c>'
                "</row></sheetData></worksheet>"
            ),
        }
    )

    presentation = process_document("memoria.pptx", pptx)
    workbook = process_document("datos.xlsx", xlsx)

    assert "Memoria episódica" in presentation.extracted_text
    assert "Elyndra\t42" in workbook.extracted_text


def test_pdf_adapter_parses_document_locally() -> None:
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(buffer)

    result = process_document("blank.pdf", buffer.getvalue())

    assert result.kind == "document"
    assert result.validation_status == "valid"
    assert result.diagnostics["pages"] == 1


def test_code_validate_skill_uses_deterministic_yaml_parser(
    isolated_home,
) -> None:
    from pathlib import Path

    from elyndra.application import ElyndraApplication

    path = Path.home() / "Proyectos" / "items.yml"
    path.write_text("items:\n  sword: true\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill("code.validate", {"path": str(path)})

    assert result.ok is True
    assert result.data["validation_status"] == "valid"
    assert result.data["processor"] == "pyyaml-safe"


def test_code_validate_skill_does_not_claim_unknown_text_is_valid(
    isolated_home,
) -> None:
    from pathlib import Path

    from elyndra.application import ElyndraApplication

    path = Path.home() / "Proyectos" / "notes.txt"
    path.write_text("solo texto", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill("code.validate", {"path": str(path)})

    assert result.ok is False
    assert "Formato no soportado" in result.message
