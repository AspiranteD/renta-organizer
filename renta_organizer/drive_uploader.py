"""Uploads organized output to Google Drive."""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from renta_organizer.config import Settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DRIVE_TOKEN_PATH = "tokens/drive.json"


def _get_drive_service(settings: Settings):
    token_path = settings.root / DRIVE_TOKEN_PATH
    token_path.parent.mkdir(parents=True, exist_ok=True)
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(settings.gmail_credentials_path), SCOPES,
            )
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _find_or_create_folder(service, name: str, parent_id: str | None = None) -> str:
    """Find existing folder or create it. Returns folder ID."""
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = service.files().create(body=metadata, fields="id").execute()
    logger.info("Carpeta creada en Drive: %s", name)
    return folder["id"]


def _upload_file(service, local_path: Path, parent_id: str) -> str:
    """Upload a single file to Drive. Returns file ID."""
    mime_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    metadata = {
        "name": local_path.name,
        "parents": [parent_id],
    }
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
    uploaded = service.files().create(body=metadata, media_body=media, fields="id").execute()
    return uploaded["id"]


def _upload_directory(service, local_dir: Path, parent_id: str) -> int:
    """Recursively upload a directory to Drive. Returns file count."""
    count = 0
    for item in sorted(local_dir.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            folder_id = _find_or_create_folder(service, item.name, parent_id)
            count += _upload_directory(service, item, folder_id)
        elif item.is_file():
            _upload_file(service, item, parent_id)
            count += 1
    return count


def upload_to_drive(settings: Settings, year: int) -> str | None:
    """Upload the output folder to Google Drive. Returns folder URL."""
    if not settings.drive_upload:
        logger.info("Drive upload desactivado (DRIVE_UPLOAD=false)")
        return None

    output_dir = settings.output_dir(year)
    if not output_dir.exists():
        logger.warning("No existe %s — nada que subir", output_dir)
        return None

    service = _get_drive_service(settings)

    root_folder_id = _find_or_create_folder(service, settings.drive_folder_name)
    year_folder_id = _find_or_create_folder(service, str(year), root_folder_id)

    count = _upload_directory(service, output_dir, year_folder_id)
    logger.info("Subidos %d archivos a Drive/%s/%d", count, settings.drive_folder_name, year)

    url = f"https://drive.google.com/drive/folders/{year_folder_id}"
    return url
