"""Parses bank CSV/Excel exports into a normalized format."""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class BankTransaction:
    date: datetime
    description: str
    amount: float
    currency: str
    bank: str
    raw_category: str = ""
    source_file: str = ""

    @property
    def date_str(self) -> str:
        return self.date.strftime("%Y-%m-%d")

    @property
    def is_expense(self) -> bool:
        return self.amount < 0


def _detect_encoding(path: Path) -> str:
    try:
        import chardet
        raw = path.read_bytes()
        result = chardet.detect(raw)
        return result.get("encoding", "utf-8") or "utf-8"
    except ImportError:
        return "utf-8"


def _read_file(path: Path) -> str:
    encoding = _detect_encoding(path)
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError:
        for fallback in ("utf-8", "iso-8859-1", "windows-1252", "latin-1"):
            try:
                return path.read_text(encoding=fallback)
            except UnicodeDecodeError:
                continue
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_amount(raw: str) -> float:
    """Parse amount strings with European/US format."""
    raw = raw.strip().replace(" ", "")
    if not raw or raw == "-":
        return 0.0
    raw = re.sub(r"[€$£]", "", raw)

    if re.match(r"^-?\d{1,3}(\.\d{3})*(,\d{1,2})?$", raw):
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw and "." in raw:
        if raw.rindex(",") > raw.rindex("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")

    return float(raw)


def _parse_date_flex(raw: str) -> datetime:
    """Parse dates in multiple formats."""
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S",
        "%d %b %Y", "%b %d, %Y",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {raw}")


# === Bank-specific parsers ===

def _parse_sabadell(content: str, path: Path) -> list[BankTransaction]:
    """Sabadell: semicolon-separated, DD/MM/YYYY, European amounts."""
    reader = csv.DictReader(io.StringIO(content), delimiter=";")
    txns = []
    for row in reader:
        date_key = _find_key(row, ["fecha", "date", "fecha operación", "fecha valor"])
        desc_key = _find_key(row, ["concepto", "descripción", "description", "movimiento"])
        amount_key = _find_key(row, ["importe", "amount", "cantidad"])
        if not (date_key and desc_key and amount_key):
            continue
        try:
            txns.append(BankTransaction(
                date=_parse_date_flex(row[date_key]),
                description=row[desc_key].strip(),
                amount=_parse_amount(row[amount_key]),
                currency="EUR",
                bank="sabadell",
                raw_category=row.get(_find_key(row, ["categoría", "category"]) or "", ""),
                source_file=path.name,
            ))
        except (ValueError, KeyError):
            continue
    return txns


def _parse_bbva(content: str, path: Path) -> list[BankTransaction]:
    """BBVA: semicolon-separated, similar to Sabadell."""
    reader = csv.DictReader(io.StringIO(content), delimiter=";")
    txns = []
    for row in reader:
        date_key = _find_key(row, ["fecha", "date", "fecha valor"])
        desc_key = _find_key(row, ["concepto", "descripción", "movimiento", "description"])
        amount_key = _find_key(row, ["importe", "amount", "cantidad"])
        if not (date_key and desc_key and amount_key):
            continue
        try:
            txns.append(BankTransaction(
                date=_parse_date_flex(row[date_key]),
                description=row[desc_key].strip(),
                amount=_parse_amount(row[amount_key]),
                currency="EUR",
                bank="bbva",
                raw_category=row.get(_find_key(row, ["categoría", "category"]) or "", ""),
                source_file=path.name,
            ))
        except (ValueError, KeyError):
            continue
    return txns


def _parse_bankinter(content: str, path: Path) -> list[BankTransaction]:
    """Bankinter: semicolon or comma separated."""
    sep = ";" if ";" in content.split("\n")[0] else ","
    reader = csv.DictReader(io.StringIO(content), delimiter=sep)
    txns = []
    for row in reader:
        date_key = _find_key(row, ["fecha", "date", "f. operación", "f. valor"])
        desc_key = _find_key(row, ["concepto", "descripción", "description"])
        amount_key = _find_key(row, ["importe", "amount", "cantidad"])
        if not (date_key and desc_key and amount_key):
            continue
        try:
            txns.append(BankTransaction(
                date=_parse_date_flex(row[date_key]),
                description=row[desc_key].strip(),
                amount=_parse_amount(row[amount_key]),
                currency="EUR",
                bank="bankinter",
                raw_category=row.get(_find_key(row, ["categoría", "category"]) or "", ""),
                source_file=path.name,
            ))
        except (ValueError, KeyError):
            continue
    return txns


def _parse_revolut(content: str, path: Path) -> list[BankTransaction]:
    """Revolut: comma-separated, YYYY-MM-DD or similar, amounts in various currencies."""
    reader = csv.DictReader(io.StringIO(content))
    txns = []
    for row in reader:
        date_key = _find_key(row, ["started date", "completed date", "date", "fecha"])
        desc_key = _find_key(row, ["description", "reference", "concepto"])
        amount_key = _find_key(row, ["amount", "importe"])
        currency_key = _find_key(row, ["currency", "moneda"])
        if not (date_key and desc_key and amount_key):
            continue
        try:
            date_raw = row[date_key].strip()
            if " " in date_raw:
                date_raw = date_raw.split(" ")[0]
            txns.append(BankTransaction(
                date=_parse_date_flex(date_raw),
                description=row[desc_key].strip(),
                amount=_parse_amount(row[amount_key]),
                currency=row.get(currency_key or "", "EUR").strip() or "EUR",
                bank="revolut",
                raw_category=row.get(_find_key(row, ["type", "category"]) or "", ""),
                source_file=path.name,
            ))
        except (ValueError, KeyError):
            continue
    return txns


def _parse_generic(content: str, path: Path) -> list[BankTransaction]:
    """Fallback parser — tries common delimiters and column names."""
    for sep in [";", ",", "\t"]:
        reader = csv.DictReader(io.StringIO(content), delimiter=sep)
        rows = list(reader)
        if not rows or len(rows[0]) < 2:
            continue

        date_key = _find_key(rows[0], ["fecha", "date", "fecha valor", "fecha operación"])
        desc_key = _find_key(rows[0], ["concepto", "descripción", "description", "detalle"])
        amount_key = _find_key(rows[0], ["importe", "amount", "cantidad", "monto"])

        if date_key and desc_key and amount_key:
            txns = []
            for row in rows:
                try:
                    txns.append(BankTransaction(
                        date=_parse_date_flex(row[date_key]),
                        description=row[desc_key].strip(),
                        amount=_parse_amount(row[amount_key]),
                        currency="EUR",
                        bank=path.stem.split("_")[0] if "_" in path.stem else "unknown",
                        source_file=path.name,
                    ))
                except (ValueError, KeyError):
                    continue
            return txns
    return []


BANK_PARSERS = {
    "sabadell": _parse_sabadell,
    "bbva": _parse_bbva,
    "bankinter": _parse_bankinter,
    "revolut": _parse_revolut,
}


def _detect_bank(content: str, filename: str) -> str | None:
    """Try to detect which bank a CSV belongs to based on filename or content."""
    name_lower = filename.lower()
    for bank in BANK_PARSERS:
        if bank in name_lower:
            return bank

    first_line = content.split("\n")[0].lower()
    if "started date" in first_line or "completed date" in first_line:
        return "revolut"
    if "bbva" in first_line:
        return "bbva"
    return None


def _find_key(row: dict, candidates: list[str]) -> str | None:
    """Find the first matching key (case-insensitive) in a dict."""
    row_keys_lower = {k.lower().strip(): k for k in row.keys()}
    for candidate in candidates:
        if candidate.lower() in row_keys_lower:
            return row_keys_lower[candidate.lower()]
    return None


def parse_bank_file(path: Path) -> list[BankTransaction]:
    """Parse a single bank CSV/Excel file."""
    if path.suffix.lower() in (".xls", ".xlsx"):
        return _parse_excel(path)

    content = _read_file(path)
    bank = _detect_bank(content, path.name)

    if bank and bank in BANK_PARSERS:
        txns = BANK_PARSERS[bank](content, path)
        if txns:
            logger.info("[%s] %d movimientos parseados de %s", bank, len(txns), path.name)
            return txns

    txns = _parse_generic(content, path)
    logger.info("[generic] %d movimientos parseados de %s", len(txns), path.name)
    return txns


def _parse_excel(path: Path) -> list[BankTransaction]:
    """Parse Excel bank exports using openpyxl."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if len(rows) < 2:
        return []

    headers = [str(h or "").strip() for h in rows[0]]
    header_map = {h.lower(): i for i, h in enumerate(headers)}

    date_idx = None
    desc_idx = None
    amount_idx = None

    for candidates, target in [
        (["fecha", "date", "fecha valor", "fecha operación"], "date"),
        (["concepto", "descripción", "description", "detalle"], "desc"),
        (["importe", "amount", "cantidad"], "amount"),
    ]:
        for c in candidates:
            if c in header_map:
                if target == "date":
                    date_idx = header_map[c]
                elif target == "desc":
                    desc_idx = header_map[c]
                elif target == "amount":
                    amount_idx = header_map[c]
                break

    if date_idx is None or desc_idx is None or amount_idx is None:
        return []

    txns = []
    bank_name = path.stem.split("_")[0] if "_" in path.stem else "unknown"
    for row in rows[1:]:
        try:
            date_val = row[date_idx]
            if isinstance(date_val, datetime):
                date = date_val
            else:
                date = _parse_date_flex(str(date_val))
            txns.append(BankTransaction(
                date=date,
                description=str(row[desc_idx] or "").strip(),
                amount=float(row[amount_idx]) if isinstance(row[amount_idx], (int, float)) else _parse_amount(str(row[amount_idx])),
                currency="EUR",
                bank=bank_name,
                source_file=path.name,
            ))
        except (ValueError, TypeError, IndexError):
            continue
    return txns


def parse_all_bank_files(settings: Settings) -> list[BankTransaction]:
    """Parse all CSV/Excel files in input/bancos/."""
    bancos_dir = settings.bancos_input_dir()
    if not bancos_dir.exists():
        logger.warning("Carpeta %s no existe", bancos_dir)
        return []

    all_txns: list[BankTransaction] = []
    for path in sorted(bancos_dir.iterdir()):
        if path.suffix.lower() in (".csv", ".xls", ".xlsx", ".tsv"):
            try:
                txns = parse_bank_file(path)
                all_txns.extend(txns)
            except Exception:
                logger.error("Error parseando %s", path.name, exc_info=True)

    logger.info("Total movimientos bancarios: %d", len(all_txns))
    return all_txns
