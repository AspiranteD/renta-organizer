"""Main orchestration pipeline."""
from __future__ import annotations

import logging

from renta_organizer.bank_parser import parse_all_bank_files
from renta_organizer.classifier import ClassifiedExpense, classify_transactions
from renta_organizer.config import Settings
from renta_organizer.cross_reference import CrossReferenceReport, cross_reference
from renta_organizer.drive_uploader import upload_to_drive
from renta_organizer.excel_generator import generate_excel
from renta_organizer.gmail_scanner import scan_all_accounts
from renta_organizer.pdf_extractor import extract_all_pdfs

logger = logging.getLogger(__name__)


def run_pipeline(
    settings: Settings,
    year: int,
    skip_scan: bool = False,
    skip_bank: bool = False,
    upload_only: bool = False,
) -> None:
    """Run the full renta-organizer pipeline."""
    output_dir = settings.output_dir(year)
    output_dir.mkdir(parents=True, exist_ok=True)

    if upload_only:
        url = upload_to_drive(settings, year)
        if url:
            print(f"\nSubido a Google Drive: {url}")
        return

    # --- Step 1: Scan Gmail for invoices ---
    if not skip_scan:
        print(f"\n{'='*50}")
        print(f"  PASO 1: Escaneando emails ({len(settings.email_accounts)} cuentas)")
        print(f"{'='*50}")

        downloads, manual = scan_all_accounts(settings, year)
        print(f"  {len(downloads)} PDFs descargados")
        if manual:
            print(f"  {len(manual)} items para revision manual (ver revision_manual.csv)")
    else:
        print("  [Escaneo de emails omitido]")

    # --- Step 2: Parse bank statements ---
    if not skip_bank:
        print(f"\n{'='*50}")
        print(f"  PASO 2: Parseando movimientos bancarios")
        print(f"{'='*50}")

        transactions = parse_all_bank_files(settings)
        print(f"  {len(transactions)} movimientos parseados")
        expenses_only = [t for t in transactions if t.is_expense]
        print(f"  {len(expenses_only)} gastos, {len(transactions) - len(expenses_only)} ingresos")
    else:
        print("  [Parseo bancario omitido]")
        transactions = []

    # --- Step 3: Extract data from PDFs ---
    print(f"\n{'='*50}")
    print(f"  PASO 3: Extrayendo datos de facturas PDF")
    print(f"{'='*50}")

    raw_pdfs_dir = settings.raw_pdfs_dir(year)
    invoices = extract_all_pdfs(
        raw_pdfs_dir,
        api_key=settings.openai_api_key,
        vision_model=settings.vision_model,
    )
    print(f"  {len(invoices)} facturas procesadas")
    vision = sum(1 for i in invoices if i.extraction_method == "vision")
    if vision:
        print(f"  ({vision} necesitaron GPT-4o vision)")

    # --- Step 4: Classify expenses ---
    print(f"\n{'='*50}")
    print(f"  PASO 4: Clasificando gastos con LLM")
    print(f"{'='*50}")

    classified = classify_transactions(
        transactions, settings.openai_api_key, settings.classifier_model,
    )
    _print_classification_summary(classified)

    # --- Step 5: Cross-reference ---
    print(f"\n{'='*50}")
    print(f"  PASO 5: Cruzando facturas con movimientos")
    print(f"{'='*50}")

    xref = cross_reference(classified, invoices)
    print(f"  {len(xref.matched)} gastos con factura asociada")
    print(f"  {len(xref.expenses_without_invoice)} gastos sin factura")
    print(f"  {len(xref.invoices_without_match)} facturas sin movimiento")

    # --- Step 6: Generate Excel ---
    print(f"\n{'='*50}")
    print(f"  PASO 6: Generando Excel y organizando facturas")
    print(f"{'='*50}")

    excel_path = generate_excel(classified, xref, invoices, year, output_dir)
    print(f"  Excel: {excel_path}")
    print(f"  Facturas organizadas en: {output_dir / 'facturas'}")

    # --- Step 7: Upload to Drive (optional) ---
    if settings.drive_upload:
        print(f"\n{'='*50}")
        print(f"  PASO 7: Subiendo a Google Drive")
        print(f"{'='*50}")

        url = upload_to_drive(settings, year)
        if url:
            print(f"  Drive: {url}")
    else:
        print(f"\n  [Google Drive desactivado — pon DRIVE_UPLOAD=true en .env para activar]")

    # --- Summary ---
    print(f"\n{'='*50}")
    print(f"  COMPLETADO — Año fiscal {year}")
    print(f"{'='*50}")
    print(f"  Output: {output_dir}")
    print(f"  Excel:  {excel_path.name}")
    print(f"  Envia el Excel + la carpeta 'facturas' a tu gestoria")
    print()


def _print_classification_summary(classified: list[ClassifiedExpense]) -> None:
    from collections import Counter
    cats = Counter(e.category for e in classified)
    for cat, count in cats.most_common():
        total = sum(abs(e.transaction.amount) for e in classified if e.category == cat)
        label = cat.replace("_", " ").title()
        print(f"  {label:<25} {count:>4} gastos  {total:>10.2f}€")
