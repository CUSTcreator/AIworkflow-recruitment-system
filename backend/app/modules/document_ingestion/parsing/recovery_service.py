from __future__ import annotations

from typing import Any

from .document_blocks import blocks_from_pymupdf


class LocalPdfRecoveryService:
    """Extract embedded text locally when MinerU is unavailable or rejected."""

    def extract(self, data: bytes) -> tuple[str, int, list[dict[str, Any]]]:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("local_pdf_parser_not_installed") from exc
        try:
            document = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise RuntimeError("invalid_pdf") from exc
        try:
            pages = [page.get_text("text", sort=True) for page in document]
            blocks = blocks_from_pymupdf(document)
            return "\n\n".join(pages), document.page_count, blocks
        finally:
            document.close()
