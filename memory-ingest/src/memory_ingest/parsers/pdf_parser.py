from __future__ import annotations

from pathlib import Path

from ..models import DocumentSection, ParsedDocument, ScannedFile


def parse_pdf(path: Path, scanned_file: ScannedFile) -> ParsedDocument:
    import pdfplumber

    sections: list[DocumentSection] = []
    with pdfplumber.open(str(path)) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                sections.append(DocumentSection(heading=f"Page {idx}", text=text))
    title = sections[0].text.splitlines()[0] if sections else path.stem
    return ParsedDocument(
        source_path=scanned_file.path,
        doc_type=scanned_file.doc_type,
        title=title,
        sections=sections,
        modified_time=scanned_file.modified_time,
        content_hash=scanned_file.content_hash,
    )
