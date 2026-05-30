"""Cross-references invoices with bank transactions by amount and date."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from renta_organizer.classifier import ClassifiedExpense
from renta_organizer.pdf_extractor import InvoiceData

logger = logging.getLogger(__name__)

DATE_TOLERANCE_DAYS = 5
AMOUNT_TOLERANCE = 0.02


@dataclass
class MatchResult:
    expense: ClassifiedExpense
    matched_invoice: InvoiceData | None = None
    match_confidence: float = 0.0
    match_note: str = ""


@dataclass
class CrossReferenceReport:
    matched: list[MatchResult] = field(default_factory=list)
    expenses_without_invoice: list[MatchResult] = field(default_factory=list)
    invoices_without_match: list[InvoiceData] = field(default_factory=list)


def cross_reference(
    expenses: list[ClassifiedExpense],
    invoices: list[InvoiceData],
) -> CrossReferenceReport:
    """Match invoices to bank transactions by amount ± tolerance and date ± 5 days."""
    report = CrossReferenceReport()
    available_invoices = list(invoices)
    used_invoices: set[str] = set()

    deductible_expenses = [
        e for e in expenses
        if e.category not in ("no_deducible", "ingreso")
    ]

    for expense in deductible_expenses:
        txn = expense.transaction
        best_match: InvoiceData | None = None
        best_score = 0.0

        for inv in available_invoices:
            if inv.file_path in used_invoices:
                continue
            if inv.total_amount is None:
                continue

            amount_diff = abs(abs(txn.amount) - inv.total_amount)
            if amount_diff > abs(txn.amount) * 0.05 + AMOUNT_TOLERANCE:
                continue

            if inv.date:
                try:
                    from renta_organizer.bank_parser import _parse_date_flex
                    inv_date = _parse_date_flex(inv.date)
                    days_diff = abs((txn.date - inv_date).days)
                    if days_diff > DATE_TOLERANCE_DAYS:
                        continue
                    date_score = 1.0 - (days_diff / (DATE_TOLERANCE_DAYS + 1))
                except ValueError:
                    date_score = 0.3
            else:
                date_score = 0.3

            amount_score = 1.0 - (amount_diff / (abs(txn.amount) + 0.01))
            score = (amount_score * 0.6) + (date_score * 0.4)

            if score > best_score:
                best_score = score
                best_match = inv

        if best_match and best_score > 0.5:
            used_invoices.add(best_match.file_path)
            expense.invoice_path = best_match.file_path
            expense.invoice_issuer = best_match.issuer
            expense.invoice_amount = best_match.total_amount
            report.matched.append(MatchResult(
                expense=expense,
                matched_invoice=best_match,
                match_confidence=best_score,
                match_note=f"Match por importe ({abs(txn.amount):.2f}≈{best_match.total_amount:.2f}) + fecha",
            ))
        else:
            report.expenses_without_invoice.append(MatchResult(
                expense=expense,
                match_note="Sin factura asociada",
            ))

    for inv in available_invoices:
        if inv.file_path not in used_invoices:
            report.invoices_without_match.append(inv)

    logger.info(
        "Cruce: %d matched, %d gastos sin factura, %d facturas sin match",
        len(report.matched),
        len(report.expenses_without_invoice),
        len(report.invoices_without_match),
    )
    return report
