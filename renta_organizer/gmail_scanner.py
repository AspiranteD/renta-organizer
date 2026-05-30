"""Scans multiple Gmail accounts for invoice/receipt PDFs."""
from __future__ import annotations

import base64
import csv
import logging
import re
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from renta_organizer.config import EmailAccount, Settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

INVOICE_QUERY = (
    "has:attachment filename:pdf ("
    "factura OR invoice OR recibo OR receipt OR ticket OR "
    "presupuesto OR pago OR cargo OR pedido OR order OR "
    "confirmacion OR compra OR payment OR statement"
    ")"
)

BROAD_PDF_QUERY = "has:attachment filename:pdf"

LINK_HINTS = re.compile(
    r"descarga tu factura|download your invoice|ver factura|view invoice|"
    r"accede a tu factura|descargar pdf|download pdf",
    re.I,
)


def _get_service(account: EmailAccount, credentials_path: Path):
    creds = None
    token_path = account.token_path
    token_path.parent.mkdir(parents=True, exist_ok=True)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _sanitize_filename(text: str, max_len: int = 60) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = re.sub(r'_+', '_', text).strip('_ ')
    return text[:max_len]


def _extract_sender_domain(sender: str) -> str:
    m = re.search(r"@([a-z0-9.-]+\.[a-z]{2,})", sender.lower())
    return m.group(1) if m else "unknown"


def _parse_date(headers: list[dict]) -> datetime | None:
    for h in headers:
        if h["name"].lower() == "date":
            raw = h["value"]
            for fmt in (
                "%a, %d %b %Y %H:%M:%S %z",
                "%d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S",
            ):
                try:
                    return datetime.strptime(raw.strip().rsplit(" ", 1)[0].strip(), fmt.replace(" %z", ""))
                except ValueError:
                    continue
            try:
                from email.utils import parsedate_to_datetime
                return parsedate_to_datetime(raw)
            except Exception:
                pass
    return None


def _get_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def scan_account(
    account: EmailAccount,
    settings: Settings,
    year: int,
    output_dir: Path,
) -> tuple[list[dict], list[dict]]:
    """Scan one Gmail account. Returns (downloaded_pdfs, manual_review_items)."""
    service = _get_service(account, settings.gmail_credentials_path)
    raw_pdfs_dir = output_dir / "raw_pdfs"
    raw_pdfs_dir.mkdir(parents=True, exist_ok=True)

    date_start = f"{year}/01/01"
    date_end = f"{year + 1}/01/01"

    query = f"{INVOICE_QUERY} after:{date_start} before:{date_end}"
    logger.info("[%s] Buscando facturas: %s", account.email, query)

    downloaded: list[dict] = []
    manual_review: list[dict] = []
    seen_msg_ids: set[str] = set()

    for q in [query, f"{BROAD_PDF_QUERY} after:{date_start} before:{date_end}"]:
        page_token = None
        while True:
            resp = service.users().messages().list(
                userId="me", q=q, pageToken=page_token, maxResults=200,
            ).execute()

            for msg_stub in resp.get("messages", []):
                msg_id = msg_stub["id"]
                if msg_id in seen_msg_ids:
                    continue
                seen_msg_ids.add(msg_id)

                msg = service.users().messages().get(
                    userId="me", id=msg_id, format="full",
                ).execute()

                headers = msg.get("payload", {}).get("headers", [])
                sender = _get_header(headers, "From")
                subject = _get_header(headers, "Subject")
                date = _parse_date(headers)
                snippet = msg.get("snippet", "")

                if LINK_HINTS.search(snippet) or LINK_HINTS.search(subject):
                    manual_review.append({
                        "msg_id": msg_id,
                        "account": account.email,
                        "sender": sender,
                        "subject": subject,
                        "date": date.strftime("%Y-%m-%d") if date else "",
                        "reason": "Possible invoice link (no PDF attached or additional PDF)",
                    })

                pdfs = _find_pdf_parts(msg.get("payload", {}))
                for part_id, filename in pdfs:
                    att = service.users().messages().attachments().get(
                        userId="me", messageId=msg_id, id=part_id,
                    ).execute()

                    data = base64.urlsafe_b64decode(att["data"])

                    date_prefix = date.strftime("%Y-%m-%d") if date else "unknown-date"
                    domain = _extract_sender_domain(sender)
                    safe_subject = _sanitize_filename(subject, 40)
                    safe_filename = _sanitize_filename(filename.replace(".pdf", ""), 30)

                    out_name = f"{date_prefix}_{domain}_{safe_filename}.pdf"
                    out_path = raw_pdfs_dir / out_name

                    counter = 1
                    while out_path.exists():
                        out_path = raw_pdfs_dir / f"{date_prefix}_{domain}_{safe_filename}_{counter}.pdf"
                        counter += 1

                    out_path.write_bytes(data)

                    downloaded.append({
                        "msg_id": msg_id,
                        "account": account.email,
                        "sender": sender,
                        "subject": subject,
                        "date": date.strftime("%Y-%m-%d") if date else "",
                        "domain": domain,
                        "filename_original": filename,
                        "filename_saved": out_path.name,
                        "path": str(out_path),
                        "size_kb": len(data) // 1024,
                    })

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    logger.info("[%s] %d PDFs descargados, %d para revision manual",
                account.email, len(downloaded), len(manual_review))
    return downloaded, manual_review


def _find_pdf_parts(payload: dict) -> list[tuple[str, str]]:
    """Recursively find PDF attachment parts. Returns [(attachmentId, filename)]."""
    results = []
    filename = payload.get("filename", "")
    if filename.lower().endswith(".pdf") and payload.get("body", {}).get("attachmentId"):
        results.append((payload["body"]["attachmentId"], filename))

    for part in payload.get("parts", []):
        results.extend(_find_pdf_parts(part))

    return results


def scan_all_accounts(settings: Settings, year: int) -> tuple[list[dict], list[dict]]:
    """Scan all configured Gmail accounts. Returns (all_downloads, all_manual_review)."""
    output_dir = settings.output_dir(year)
    all_downloads: list[dict] = []
    all_manual: list[dict] = []

    for account in settings.email_accounts:
        try:
            downloads, manual = scan_account(account, settings, year, output_dir)
            all_downloads.extend(downloads)
            all_manual.extend(manual)
        except Exception:
            logger.error("Error escaneando %s", account.email, exc_info=True)

    index_path = output_dir / "raw_pdfs_index.csv"
    if all_downloads:
        _write_csv(index_path, all_downloads)
        logger.info("Indice de PDFs: %s (%d archivos)", index_path, len(all_downloads))

    if all_manual:
        manual_path = output_dir / "revision_manual.csv"
        _write_csv(manual_path, all_manual)
        logger.info("Revision manual: %s (%d items)", manual_path, len(all_manual))

    return all_downloads, all_manual


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
