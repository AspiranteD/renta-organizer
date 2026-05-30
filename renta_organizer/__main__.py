"""
CLI para renta-organizer.

Uso:
  python -m renta_organizer                    # año por defecto (2025)
  python -m renta_organizer --year 2025        # año fiscal especifico
  python -m renta_organizer --skip-scan        # sin re-escanear email
  python -m renta_organizer --upload-only      # solo subir a Drive
"""
from __future__ import annotations

import argparse
import logging

from renta_organizer.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="renta-organizer",
        description="Organiza facturas y gastos para la declaracion de la renta española.",
    )
    parser.add_argument(
        "--year", type=int, default=None,
        help="Año fiscal (default: DEFAULT_YEAR del .env)",
    )
    parser.add_argument(
        "--skip-scan", action="store_true",
        help="No escanear emails (usar PDFs ya descargados)",
    )
    parser.add_argument(
        "--skip-bank", action="store_true",
        help="No parsear movimientos bancarios",
    )
    parser.add_argument(
        "--upload-only", action="store_true",
        help="Solo subir output existente a Google Drive",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Logging detallado",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    settings = Settings.load()
    year = args.year or settings.default_year

    print(f"\n  renta-organizer v0.1.0 — Año fiscal {year}")
    print(f"  {'='*40}\n")

    from renta_organizer.pipeline import run_pipeline

    run_pipeline(
        settings,
        year=year,
        skip_scan=args.skip_scan,
        skip_bank=args.skip_bank,
        upload_only=args.upload_only,
    )


if __name__ == "__main__":
    main()
