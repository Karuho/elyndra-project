from __future__ import annotations

import csv
import importlib.util
import io
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".csv",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".conf",
    ".log",
    ".sql",
    ".py",
    ".php",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".xml",
    ".sh",
    ".bash",
    ".zsh",
    ".java",
    ".kt",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".rb",
    ".pl",
    ".lua",
    ".gradle",
    ".properties",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".odt", ".pptx", ".xlsx"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS

_MAX_EXTRACTED_CHARS = 200_000
_MAX_ZIP_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
_MAX_ZIP_MEMBER_BYTES = 12 * 1024 * 1024


class DocumentProcessorUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DocumentProcessingResult:
    kind: str
    mime_type: str
    extracted_text: str
    extraction_status: str
    validation_status: str
    processor: str
    diagnostics: dict[str, Any]


def document_capabilities() -> dict[str, bool]:
    return {
        "pdf": importlib.util.find_spec("pypdf") is not None,
        "yaml": importlib.util.find_spec("yaml") is not None,
        "php": shutil.which("php") is not None,
        "office": True,
    }


def process_document(
    filename: str,
    data: bytes,
    *,
    supplied_mime: str = "",
) -> DocumentProcessingResult:
    extension = Path(filename).suffix.casefold()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Tipo de archivo no admitido todavía.")
    if extension in IMAGE_EXTENSIONS:
        _verify_image(extension, data)
        return DocumentProcessingResult(
            kind="image",
            mime_type=_mime_for(extension, "image", supplied_mime),
            extracted_text="",
            extraction_status="not_applicable",
            validation_status="not_checked",
            processor="image-signature",
            diagnostics={"messages": ["Firma binaria de imagen verificada."]},
        )
    if extension in DOCUMENT_EXTENSIONS:
        return _process_binary_document(filename, extension, data, supplied_mime)
    return _process_text_document(filename, extension, data, supplied_mime)


def _process_text_document(
    filename: str,
    extension: str,
    data: bytes,
    supplied_mime: str,
) -> DocumentProcessingResult:
    text = _decode_text(data)[:_MAX_EXTRACTED_CHARS]
    status, processor, diagnostics = _validate_text(filename, extension, text)
    return DocumentProcessingResult(
        kind="text",
        mime_type=_mime_for(extension, "text", supplied_mime),
        extracted_text=text,
        extraction_status="extracted",
        validation_status=status,
        processor=processor,
        diagnostics=diagnostics,
    )


def _process_binary_document(
    filename: str,
    extension: str,
    data: bytes,
    supplied_mime: str,
) -> DocumentProcessingResult:
    try:
        if extension == ".pdf":
            text, metadata = _extract_pdf(data)
            processor = "pypdf"
        elif extension == ".docx":
            text, metadata = _extract_docx(data)
            processor = "docx-xml"
        elif extension == ".odt":
            text, metadata = _extract_odt(data)
            processor = "odt-xml"
        elif extension == ".pptx":
            text, metadata = _extract_pptx(data)
            processor = "pptx-xml"
        elif extension == ".xlsx":
            text, metadata = _extract_xlsx(data)
            processor = "xlsx-xml"
        else:  # pragma: no cover - protected by extension set
            raise ValueError("Documento no reconocido.")
    except DocumentProcessorUnavailableError as exc:
        return DocumentProcessingResult(
            kind="document",
            mime_type=_mime_for(extension, "document", supplied_mime),
            extracted_text="",
            extraction_status="unavailable",
            validation_status="unavailable",
            processor=f"{extension.removeprefix('.')}-unavailable",
            diagnostics={"messages": [str(exc)]},
        )
    except (ValueError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        return DocumentProcessingResult(
            kind="document",
            mime_type=_mime_for(extension, "document", supplied_mime),
            extracted_text="",
            extraction_status="failed",
            validation_status="invalid",
            processor=f"{extension.removeprefix('.')}-parser",
            diagnostics={"messages": [str(exc)]},
        )

    clean = text[:_MAX_EXTRACTED_CHARS].strip()
    messages = ["Contenedor y estructura documental procesados correctamente."]
    if not clean:
        messages.append("No se encontró texto extraíble en el documento.")
    return DocumentProcessingResult(
        kind="document",
        mime_type=_mime_for(extension, "document", supplied_mime),
        extracted_text=clean,
        extraction_status="extracted" if clean else "empty",
        validation_status="valid",
        processor=processor,
        diagnostics={"messages": messages, **metadata},
    )


def _validate_text(
    filename: str,
    extension: str,
    text: str,
) -> tuple[str, str, dict[str, Any]]:
    try:
        if extension == ".json":
            json.loads(text)
            return "valid", "python-json", {"messages": ["JSON válido."]}
        if extension == ".toml":
            tomllib.loads(text)
            return "valid", "python-tomllib", {"messages": ["TOML válido."]}
        if extension == ".xml":
            ElementTree.fromstring(text)
            return "valid", "python-xml", {"messages": ["XML bien formado."]}
        if extension in {".yaml", ".yml"}:
            return _validate_yaml(text)
        if extension == ".php":
            return _validate_php(filename, text)
        if extension == ".csv":
            rows = list(csv.reader(io.StringIO(text[:50_000])))
            width = max((len(row) for row in rows), default=0)
            return (
                "partial",
                "python-csv",
                {
                    "messages": [
                        "CSV legible. La coherencia semántica de columnas no fue validada."
                    ],
                    "rows_sampled": len(rows),
                    "max_columns": width,
                },
            )
    except json.JSONDecodeError as exc:
        return _invalid("python-json", exc.msg, exc.lineno, exc.colno)
    except tomllib.TOMLDecodeError as exc:
        return "invalid", "python-tomllib", {"messages": [str(exc)]}
    except ElementTree.ParseError as exc:
        line, column = exc.position
        return _invalid("python-xml", str(exc), line, column + 1)
    except csv.Error as exc:
        return "invalid", "python-csv", {"messages": [str(exc)]}
    return (
        "not_checked",
        "plain-text",
        {"messages": ["Contenido leído, pero no existe un validador específico activo."]},
    )


def _validate_yaml(text: str) -> tuple[str, str, dict[str, Any]]:
    try:
        import yaml
    except ImportError:
        return (
            "unavailable",
            "yaml-unavailable",
            {
                "messages": [
                    "PyYAML no está instalado; el archivo fue leído pero no validado."
                ]
            },
        )
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        diagnostics: dict[str, Any] = {"messages": [str(exc)]}
        if mark is not None:
            diagnostics["line"] = int(mark.line) + 1
            diagnostics["column"] = int(mark.column) + 1
        return "invalid", "pyyaml-safe", diagnostics
    return (
        "valid",
        "pyyaml-safe",
        {
            "messages": ["YAML válido mediante carga segura."],
            "documents": len(documents),
        },
    )


def _validate_php(filename: str, text: str) -> tuple[str, str, dict[str, Any]]:
    binary = shutil.which("php")
    if binary is None:
        return (
            "unavailable",
            "php-cli-unavailable",
            {
                "messages": [
                    "PHP CLI no está instalado; el código fue leído pero no validado."
                ]
            },
        )
    suffix = Path(filename).suffix or ".php"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=suffix,
        delete=True,
    ) as handle:
        handle.write(text)
        handle.flush()
        try:
            completed = subprocess.run(
                [binary, "-l", handle.name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return (
                "unavailable",
                "php-cli",
                {"messages": ["PHP CLI excedió el límite local de 10 segundos."]},
            )
    output = "\n".join(
        item.strip() for item in (completed.stdout, completed.stderr) if item.strip()
    )
    if completed.returncode == 0:
        return "valid", "php-cli", {"messages": [output or "Sintaxis PHP válida."]}
    return "invalid", "php-cli", {"messages": [output or "Sintaxis PHP inválida."]}


def _invalid(
    processor: str,
    message: str,
    line: int,
    column: int,
) -> tuple[str, str, dict[str, Any]]:
    return (
        "invalid",
        processor,
        {"messages": [message], "line": line, "column": column},
    )


def _extract_pdf(data: bytes) -> tuple[str, dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentProcessorUnavailableError(
            "pypdf no está instalado; instala el extra local de documentos."
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf exposes several parser exceptions
        raise ValueError(f"PDF inválido o ilegible: {exc}") from exc
    pages: list[str] = []
    for index, page in enumerate(reader.pages[:250], start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = f"[Página {index}: extracción fallida: {exc}]"
        if text.strip():
            pages.append(f"Página {index}\n{text.strip()}")
    return "\n\n".join(pages), {"pages": len(reader.pages)}


def _extract_docx(data: bytes) -> tuple[str, dict[str, Any]]:
    with _safe_zip(data) as archive:
        root = _xml_member(archive, "word/document.xml")
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if _local_name(paragraph.tag) != "p":
            continue
        text = "".join(
            node.text or "" for node in paragraph.iter() if _local_name(node.tag) == "t"
        ).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs), {"paragraphs": len(paragraphs)}


def _extract_odt(data: bytes) -> tuple[str, dict[str, Any]]:
    with _safe_zip(data) as archive:
        root = _xml_member(archive, "content.xml")
    paragraphs: list[str] = []
    for node in root.iter():
        if _local_name(node.tag) not in {"p", "h"}:
            continue
        text = "".join(node.itertext()).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs), {"paragraphs": len(paragraphs)}


def _extract_pptx(data: bytes) -> tuple[str, dict[str, Any]]:
    with _safe_zip(data) as archive:
        members = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_numeric_member_key,
        )
        slides: list[str] = []
        for index, name in enumerate(members, start=1):
            root = _xml_member(archive, name)
            text = " ".join(
                (node.text or "").strip()
                for node in root.iter()
                if _local_name(node.tag) == "t" and (node.text or "").strip()
            )
            if text:
                slides.append(f"Diapositiva {index}\n{text}")
    return "\n\n".join(slides), {"slides": len(members)}


def _extract_xlsx(data: bytes) -> tuple[str, dict[str, Any]]:
    with _safe_zip(data) as archive:
        shared = _xlsx_shared_strings(archive)
        members = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            ),
            key=_numeric_member_key,
        )
        sheets: list[str] = []
        for index, name in enumerate(members, start=1):
            root = _xml_member(archive, name)
            rows: list[str] = []
            for row in root.iter():
                if _local_name(row.tag) != "row":
                    continue
                values: list[str] = []
                for cell in row:
                    if _local_name(cell.tag) != "c":
                        continue
                    cell_type = cell.attrib.get("t", "")
                    value = _xlsx_cell_value(cell, cell_type, shared)
                    values.append(value)
                if any(value for value in values):
                    rows.append("\t".join(values))
            if rows:
                sheets.append(f"Hoja {index}\n" + "\n".join(rows))
    return "\n\n".join(sheets), {"sheets": len(members)}


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _xml_member(archive, "xl/sharedStrings.xml")
    values: list[str] = []
    for item in root.iter():
        if _local_name(item.tag) != "si":
            continue
        values.append(
            "".join(
                node.text or ""
                for node in item.iter()
                if _local_name(node.tag) == "t"
            )
        )
    return values


def _xlsx_cell_value(cell: ElementTree.Element, cell_type: str, shared: list[str]) -> str:
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.iter() if _local_name(node.tag) == "t"
        )
    value = next(
        (node.text or "" for node in cell.iter() if _local_name(node.tag) == "v"),
        "",
    )
    if cell_type == "s" and value.isdigit():
        index = int(value)
        return shared[index] if index < len(shared) else value
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


class _SafeZipContext:
    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)
        self.archive: zipfile.ZipFile | None = None

    def __enter__(self) -> zipfile.ZipFile:
        archive = zipfile.ZipFile(self._buffer)
        infos = archive.infolist()
        if sum(info.file_size for info in infos) > _MAX_ZIP_UNCOMPRESSED_BYTES:
            archive.close()
            raise ValueError("El documento comprimido excede el límite local de extracción.")
        if any(info.file_size > _MAX_ZIP_MEMBER_BYTES for info in infos):
            archive.close()
            raise ValueError("El documento contiene una pieza interna demasiado grande.")
        if any(".." in Path(info.filename).parts for info in infos):
            archive.close()
            raise ValueError("El documento contiene rutas internas no seguras.")
        self.archive = archive
        return archive

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.archive is not None:
            self.archive.close()
        self._buffer.close()


def _safe_zip(data: bytes) -> _SafeZipContext:
    return _SafeZipContext(data)


def _xml_member(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        data = archive.read(name)
    except KeyError as exc:
        raise ValueError(f"El documento no contiene {name}.") from exc
    return ElementTree.fromstring(data)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _numeric_member_key(name: str) -> tuple[int, str]:
    match = re.search(r"(\d+)(?=\.xml$)", name)
    return (int(match.group(1)) if match else 0, name)


def _verify_image(extension: str, data: bytes) -> None:
    valid = False
    if extension == ".png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension in {".jpg", ".jpeg"}:
        valid = data.startswith(b"\xff\xd8\xff")
    elif extension == ".gif":
        valid = data.startswith((b"GIF87a", b"GIF89a"))
    elif extension == ".webp":
        valid = len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if not valid:
        raise ValueError("El contenido no coincide con el formato de imagen declarado.")


def _mime_for(extension: str, kind: str, supplied_mime: str) -> str:
    clean_supplied = supplied_mime.strip().casefold()
    guessed, _encoding = mimetypes.guess_type(f"file{extension}")
    detected = guessed or {
        "image": "image/*",
        "document": "application/octet-stream",
        "text": "text/plain",
    }[kind]
    if clean_supplied and clean_supplied != "application/octet-stream":
        if kind == "image" and not clean_supplied.startswith("image/"):
            raise ValueError("El tipo declarado no coincide con una imagen permitida.")
        if kind != "image" and clean_supplied.startswith("image/"):
            raise ValueError("El tipo declarado no coincide con un documento permitido.")
    return detected


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise ValueError("El archivo parece binario y no puede tratarse como texto.")
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("No pude decodificar el archivo como texto.")
