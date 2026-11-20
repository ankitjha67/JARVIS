"""Universal file processor: extract text from PDF, DOCX, XLSX, CSV, PPTX, ZIP and more; convert between formats."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile
from pathlib import Path

from pydantic import Field

from ..policy import Tier, canonical, is_sensitive_path
from .registry import Args, ToolError, tool

_TEXT_EXTS = {".txt", ".md", ".log", ".ini", ".cfg", ".yaml", ".yml", ".toml", ".py", ".js", ".ts", ".html", ".css",
              ".xml", ".sql", ".sh", ".bat", ".ps1", ".env", ".gitignore", ".conf", ".rst"}
_MAX_TEXT = 8000


def _guard(raw: str) -> Path:
    path = canonical(raw)
    if is_sensitive_path(path):
        raise ToolError("That location holds credentials or private keys and is off limits.")
    if not path.exists():
        raise ToolError(f"File not found: {path}")
    return path


# -- extract / summarize -------------------------------------------------------

def _extract(path: Path) -> tuple[str, str]:
    """Return (file_type, extracted_text)."""
    ext = path.suffix.lower()

    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ToolError("pypdf is not installed. Run: uv pip install pypdf")
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "pdf", f"[{len(reader.pages)} pages]\n\n" + "\n\n---\n\n".join(pages)

    if ext == ".docx":
        try:
            from docx import Document
        except ImportError:
            raise ToolError("python-docx is not installed. Run: uv pip install python-docx")
        doc = Document(str(path))
        return "docx", "\n".join(p.text for p in doc.paragraphs)

    if ext in (".xlsx", ".xls"):
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise ToolError("openpyxl is not installed. Run: uv pip install openpyxl")
        wb = load_workbook(str(path), read_only=True, data_only=True)
        sheets = []
        for name in wb.sheetnames:
            ws = wb[name]
            rows = []
            for row in ws.iter_rows(max_row=200, values_only=True):
                rows.append("\t".join(str(c) if c is not None else "" for c in row))
            sheets.append(f"## Sheet: {name}\n" + "\n".join(rows))
        wb.close()
        return "xlsx", "\n\n".join(sheets)

    if ext == ".csv":
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            rows = []
            for i, row in enumerate(reader):
                if i >= 200:
                    rows.append(f"... (truncated at 200 rows)")
                    break
                rows.append("\t".join(row))
        return "csv", "\n".join(rows)

    if ext == ".pptx":
        try:
            from pptx import Presentation
        except ImportError:
            raise ToolError("python-pptx is not installed. Run: uv pip install python-pptx")
        prs = Presentation(str(path))
        slides = []
        for i, slide in enumerate(prs.slides, 1):
            texts = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    texts.append(shape.text.strip())
            slides.append(f"## Slide {i}\n" + "\n".join(texts))
        return "pptx", "\n\n".join(slides)

    if ext == ".json":
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        return "json", json.dumps(data, indent=2, ensure_ascii=False)

    if ext == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            entries = []
            for info in zf.infolist():
                size = f"{info.file_size:,}" if info.file_size else "dir"
                entries.append(f"  {info.filename}  ({size} bytes)")
        return "zip", f"Archive with {len(entries)} entries:\n" + "\n".join(entries[:500])

    if ext in _TEXT_EXTS or ext == "":
        text = path.read_text(encoding="utf-8", errors="replace")
        return ext.lstrip(".") or "text", text

    raise ToolError(
        f"Unsupported file type '{ext}'. Supported: pdf, docx, xlsx, csv, pptx, json, zip, "
        f"and plain text ({', '.join(sorted(_TEXT_EXTS))})"
    )


class FileSummarizeArgs(Args):
    path: str = Field(..., min_length=1, max_length=500, description="Path to the file to extract text from")


@tool("file_extract", "Extract and return text content from PDF, DOCX, XLSX, CSV, PPTX, JSON, ZIP, or any text file.",
      FileSummarizeArgs, Tier.T0, summary=lambda a: f"Extract text from {Path(a.path).name}")
async def file_extract(a: FileSummarizeArgs) -> dict:
    path = _guard(a.path)
    file_type, text = await asyncio.to_thread(_extract, path)
    truncated = len(text) > _MAX_TEXT
    return {
        "file": path.name,
        "type": file_type,
        "content": text[:_MAX_TEXT],
        "chars": len(text),
        "truncated": truncated,
    }


# -- convert -------------------------------------------------------------------

def _convert(path: Path, to_fmt: str) -> Path:
    ext = path.suffix.lower()
    to_fmt = to_fmt.lower().lstrip(".")

    if to_fmt == "pdf":
        try:
            from fpdf import FPDF
        except ImportError:
            raise ToolError("fpdf2 is not installed. Run: uv pip install fpdf2")
        if ext == ".docx":
            try:
                from docx import Document
            except ImportError:
                raise ToolError("python-docx is not installed. Run: uv pip install python-docx")
            doc = Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs)
        elif ext in _TEXT_EXTS or ext in (".md", ".txt", ".csv", ".json", ".log"):
            text = path.read_text(encoding="utf-8", errors="replace")
        else:
            raise ToolError(f"Cannot convert {ext} to PDF. Supported sources: text files, .docx")
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        for line in text.split("\n"):
            pdf.multi_cell(0, 6, line)
        out = path.with_suffix(".pdf")
        pdf.output(str(out))
        return out

    if to_fmt == "txt":
        if ext == ".docx":
            try:
                from docx import Document
            except ImportError:
                raise ToolError("python-docx is not installed. Run: uv pip install python-docx")
            doc = Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs)
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                raise ToolError("pypdf is not installed. Run: uv pip install pypdf")
            reader = PdfReader(str(path))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            raise ToolError(f"Cannot convert {ext} to TXT. Supported: .docx, .pdf")
        out = path.with_suffix(".txt")
        out.write_text(text, encoding="utf-8")
        return out

    if to_fmt == "csv":
        if ext in (".xlsx", ".xls"):
            try:
                from openpyxl import load_workbook
            except ImportError:
                raise ToolError("openpyxl is not installed. Run: uv pip install openpyxl")
            wb = load_workbook(str(path), read_only=True, data_only=True)
            ws = wb.active
            out = path.with_suffix(".csv")
            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in ws.iter_rows(values_only=True):
                writer.writerow([(c if c is not None else "") for c in row])
            wb.close()
            out.write_text(buf.getvalue(), encoding="utf-8")
            return out
        raise ToolError(f"Cannot convert {ext} to CSV. Supported: .xlsx, .xls")

    raise ToolError(f"Unsupported target format '{to_fmt}'. Supported: pdf, txt, csv")


class FileConvertArgs(Args):
    path: str = Field(..., min_length=1, max_length=500, description="Path to the source file")
    to_format: str = Field(..., min_length=1, max_length=10, description="Target format: pdf, txt, or csv")


@tool("file_convert", "Convert a file to another format (text/docx→PDF, docx/pdf→TXT, xlsx→CSV).",
      FileConvertArgs, Tier.T1,
      summary=lambda a: f"Convert {Path(a.path).name} to {a.to_format}")
async def file_convert(a: FileConvertArgs) -> dict:
    path = _guard(a.path)
    out = await asyncio.to_thread(_convert, path, a.to_format)
    return {"converted": str(out), "from": path.suffix, "to": a.to_format}
