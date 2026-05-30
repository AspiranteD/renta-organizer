"""Extracts structured data from invoice PDFs using text parsing and LLM vision."""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)

CIF_PATTERN = re.compile(r"\b[A-Z]\d{8}\b|[A-Z]-?\d{7}-?[A-Z0-9]")
AMOUNT_PATTERN = re.compile(
    r"(?:total|importe|amount|a pagar|total factura|total a pagar)[:\s]*"
    r"([0-9]{1,3}(?:[.,]\d{3})*[.,]\d{2})\s*€?",
    re.I,
)
DATE_PATTERN = re.compile(
    r"(?:fecha|date)[:\s]*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})", re.I,
)


@dataclass
class InvoiceData:
    file_path: str
    issuer: str
    cif: str
    date: str
    total_amount: float | None
    description: str
    raw_text: str
    extraction_method: str
    confidence: float

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "issuer": self.issuer,
            "cif": self.cif,
            "date": self.date,
            "total_amount": self.total_amount,
            "description": self.description,
            "extraction_method": self.extraction_method,
            "confidence": self.confidence,
        }


def extract_text_from_pdf(path: Path) -> str:
    """Extract text from PDF using pdfplumber."""
    text_parts = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:5]:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
    except Exception:
        logger.warning("pdfplumber failed for %s", path.name, exc_info=True)
    return "\n".join(text_parts)


def _parse_amount_from_text(text: str) -> float | None:
    m = AMOUNT_PATTERN.search(text)
    if m:
        raw = m.group(1).replace(".", "").replace(",", ".")
        try:
            return float(raw)
        except ValueError:
            pass

    amounts = re.findall(r"(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})\s*€", text)
    if amounts:
        parsed = []
        for a in amounts:
            try:
                parsed.append(float(a.replace(".", "").replace(",", ".")))
            except ValueError:
                continue
        if parsed:
            return max(parsed)
    return None


def _extract_from_text(text: str, path: Path) -> InvoiceData:
    """Try to extract invoice data from raw text using regex."""
    cif_match = CIF_PATTERN.search(text)
    date_match = DATE_PATTERN.search(text)
    amount = _parse_amount_from_text(text)

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    issuer = lines[0][:80] if lines else ""

    return InvoiceData(
        file_path=str(path),
        issuer=issuer,
        cif=cif_match.group(0) if cif_match else "",
        date=date_match.group(1) if date_match else "",
        total_amount=amount,
        description=" ".join(lines[:3])[:200] if lines else "",
        raw_text=text[:2000],
        extraction_method="text",
        confidence=0.6 if (cif_match and amount) else 0.3,
    )


def _extract_with_vision(path: Path, api_key: str, model: str) -> InvoiceData:
    """Use GPT-4o vision to extract invoice data from a PDF image."""
    from openai import OpenAI

    pdf_bytes = path.read_bytes()
    b64 = base64.b64encode(pdf_bytes).decode()

    client = OpenAI(api_key=api_key)

    prompt = """Analyze this invoice/receipt PDF and extract:
- issuer: company/business name that issued the invoice
- cif: Spanish tax ID (CIF/NIF) of the issuer, format like B12345678
- date: invoice date in DD/MM/YYYY format
- total_amount: total amount to pay (number only, including tax)
- description: brief description of what was purchased/paid for (in Spanish, max 50 words)

Return ONLY valid JSON: {"issuer": "", "cif": "", "date": "", "total_amount": 0.0, "description": ""}
If you can't determine a field, use empty string or null for amount."""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "file", "file": {
                        "filename": path.name,
                        "file_data": f"data:application/pdf;base64,{b64}",
                    }},
                ],
            }],
            temperature=0,
            max_tokens=500,
        )

        raw = resp.choices[0].message.content or "{}"
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"\s*```", "", raw)
        data = json.loads(raw)

        return InvoiceData(
            file_path=str(path),
            issuer=str(data.get("issuer", ""))[:80],
            cif=str(data.get("cif", ""))[:15],
            date=str(data.get("date", "")),
            total_amount=float(data["total_amount"]) if data.get("total_amount") else None,
            description=str(data.get("description", ""))[:200],
            raw_text="[extracted via vision]",
            extraction_method="vision",
            confidence=0.8,
        )
    except Exception:
        logger.error("Vision extraction failed for %s", path.name, exc_info=True)
        return InvoiceData(
            file_path=str(path),
            issuer="", cif="", date="", total_amount=None,
            description="Vision extraction failed",
            raw_text="", extraction_method="vision_failed", confidence=0.0,
        )


def extract_invoice_data(
    path: Path,
    api_key: str = "",
    vision_model: str = "gpt-4o",
) -> InvoiceData:
    """Extract data from an invoice PDF — text first, vision fallback."""
    text = extract_text_from_pdf(path)

    if len(text.strip()) > 50:
        result = _extract_from_text(text, path)
        if result.total_amount and result.confidence >= 0.5:
            return result
        logger.info("Text extraction weak for %s (conf=%.1f), trying vision...", path.name, result.confidence)

    if api_key:
        return _extract_with_vision(path, api_key, vision_model)

    return _extract_from_text(text, path) if text.strip() else InvoiceData(
        file_path=str(path), issuer="", cif="", date="", total_amount=None,
        description="Empty PDF", raw_text="", extraction_method="none", confidence=0.0,
    )


def extract_all_pdfs(
    pdf_dir: Path,
    api_key: str = "",
    vision_model: str = "gpt-4o",
) -> list[InvoiceData]:
    """Extract data from all PDFs in a directory."""
    if not pdf_dir.exists():
        return []

    results = []
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    logger.info("Extrayendo datos de %d PDFs...", len(pdfs))

    for i, path in enumerate(pdfs, 1):
        if i % 10 == 0:
            logger.info("  ... %d/%d procesados", i, len(pdfs))
        result = extract_invoice_data(path, api_key, vision_model)
        results.append(result)

    vision_count = sum(1 for r in results if r.extraction_method == "vision")
    logger.info("PDFs procesados: %d (texto: %d, vision: %d)",
                len(results), len(results) - vision_count, vision_count)
    return results
