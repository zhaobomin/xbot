from __future__ import annotations

from pathlib import Path

from .docx_parser import parse_docx
from .pdf_parser import parse_pdf
from .pptx_parser import parse_pptx
from .text_like import parse_text_like
from ..models import ParsedDocument, ScannedFile


def parse_file(scanned_file: ScannedFile) -> ParsedDocument:
    path = Path(scanned_file.path)
    if scanned_file.doc_type in {"md", "txt"}:
        return parse_text_like(path, scanned_file)
    if scanned_file.doc_type == "docx":
        return parse_docx(path, scanned_file)
    if scanned_file.doc_type == "pptx":
        return parse_pptx(path, scanned_file)
    if scanned_file.doc_type == "pdf":
        return parse_pdf(path, scanned_file)
    raise ValueError(f"Unsupported document type: {scanned_file.doc_type}")
