from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class EmailAccount:
    email: str
    label: str
    token_path: Path


@dataclass
class Settings:
    openai_api_key: str
    classifier_model: str
    vision_model: str
    gmail_credentials_path: Path
    email_accounts: list[EmailAccount]
    drive_upload: bool
    drive_folder_name: str
    default_year: int
    root: Path = field(default_factory=lambda: ROOT)

    def output_dir(self, year: int) -> Path:
        return self.root / "output" / str(year)

    def raw_pdfs_dir(self, year: int) -> Path:
        return self.output_dir(year) / "raw_pdfs"

    def facturas_dir(self, year: int) -> Path:
        return self.output_dir(year) / "facturas"

    def bancos_input_dir(self) -> Path:
        return self.root / "input" / "bancos"

    @classmethod
    def load(cls) -> Settings:
        load_dotenv(ROOT / ".env")

        emails_path = ROOT / "config" / "emails.yaml"
        if not emails_path.exists():
            example = ROOT / "config" / "emails.yaml.example"
            raise FileNotFoundError(
                f"Copia {example} a {emails_path} y configura tus cuentas Gmail."
            )

        with open(emails_path, encoding="utf-8") as f:
            email_cfg = yaml.safe_load(f)

        accounts = []
        for acc in email_cfg.get("accounts", []):
            accounts.append(EmailAccount(
                email=acc["email"],
                label=acc.get("label", "default"),
                token_path=ROOT / acc.get("token_path", f"tokens/{acc['email']}.json"),
            ))

        creds_path_str = email_cfg.get(
            "credentials_path",
            os.getenv("GMAIL_CREDENTIALS_PATH", "gmail_oauth_credentials.json"),
        )

        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            classifier_model=os.getenv("CLASSIFIER_MODEL", "gpt-4o-mini"),
            vision_model=os.getenv("VISION_MODEL", "gpt-4o"),
            gmail_credentials_path=ROOT / creds_path_str,
            email_accounts=accounts,
            drive_upload=os.getenv("DRIVE_UPLOAD", "false").lower() == "true",
            drive_folder_name=os.getenv("DRIVE_FOLDER_NAME", "Renta"),
            default_year=int(os.getenv("DEFAULT_YEAR", "2025")),
        )
