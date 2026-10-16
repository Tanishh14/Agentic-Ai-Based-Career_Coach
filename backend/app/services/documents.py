"""Text extraction from user-supplied PDF / DOCX / TXT / HTML uploads (no network)."""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

from ..errors import AppError
from ..integrations.safe_fetch import html_to_text, normalize_text

ALLOWED = {
    "application/pdf": "pdf", "text/plain": "txt", "text/markdown": "txt", "text/html": "html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


@dataclass
class ExtractedDoc:
    text: str
    page_offsets: list[int] = field(default_factory=list)  # char offset where each page starts
    method: str = ""
    warning: str | None = None


def detect_format(filename: str, mime: str | None, data: bytes) -> str:
    name = (filename or "").lower()
    if data[:5] == b"%PDF-" or name.endswith(".pdf"):
        return "pdf"
    if data[:2] == b"PK" and (name.endswith(".docx") or "wordprocessingml" in (mime or "")):
        return "docx"
    if name.endswith((".html", ".htm")):
        return "html"
    if name.endswith((".txt", ".md", ".text")) or (mime or "").startswith("text/"):
        return "txt"
    raise AppError("unsupported_upload", "Unsupported file type. Upload PDF, DOCX, TXT or HTML.", status=415)


def extract_text(filename: str, mime: str | None, data: bytes, max_bytes: int = 15_000_000) -> ExtractedDoc:
    if len(data) > max_bytes:
        raise AppError("upload_too_large", f"File exceeds {max_bytes // 1_000_000} MB.", status=413)
    fmt = detect_format(filename, mime, data)
    if fmt == "pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise AppError("pdf_encrypted", "The PDF is password-protected.", status=422)
            parts, offsets, pos = [], [], 0
            for page in reader.pages[:60]:
                t = normalize_text(page.extract_text() or "")
                offsets.append(pos)
                parts.append(t)
                pos += len(t) + 2
            text = "\n\n".join(parts)
        except AppError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AppError("pdf_unreadable", f"Could not read the PDF: {type(e).__name__}", status=422)
        warn = None
        if len(text.strip()) < 80:
            warn = "Very little text could be extracted (the PDF may be scanned images). Paste the text instead."
        return ExtractedDoc(text, offsets, "pypdf", warn)
    if fmt == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml")
        except (zipfile.BadZipFile, KeyError):
            raise AppError("docx_unreadable", "Could not read the DOCX file.", status=422)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        root = ElementTree.fromstring(xml)
        paras = []
        for p in root.iter(f"{{{ns['w']}}}p"):
            txt = "".join(t.text or "" for t in p.iter(f"{{{ns['w']}}}t"))
            paras.append(txt)
        return ExtractedDoc(normalize_text("\n".join(paras)), [0], "docx-xml")
    if fmt == "html":
        return ExtractedDoc(html_to_text(data.decode("utf-8", "replace")), [0], "html")
    return ExtractedDoc(normalize_text(data.decode("utf-8", "replace")), [0], "text")


def page_for_offset(offsets: list[int], pos: int) -> int | None:
    if not offsets:
        return None
    page = 1
    for i, o in enumerate(offsets, 1):
        if pos >= o:
            page = i
    return page


def section_for_offset(text: str, pos: int) -> str | None:
    idx = pos
    for _ in range(30):
        e = text.rfind("\n", 0, idx)
        if e < 0:
            return None
        s = text.rfind("\n", 0, e) + 1
        line = text[s:e].strip()
        idx = s
        if line and len(line) <= 40 and len(line.split()) <= 4 and not re.search(r"[.,;]$", line) and (line.isupper() or line.istitle()):
            return line[:60]
    return None
