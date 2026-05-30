"""Classifies expenses into fiscal categories using LLM. Biased toward high recall."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from renta_organizer.bank_parser import BankTransaction

logger = logging.getLogger(__name__)

FISCAL_CATEGORIES = [
    "sanitario",
    "formacion",
    "donaciones",
    "seguros",
    "suministros",
    "transporte",
    "vivienda",
    "potencialmente_deducible",
    "no_deducible",
]

SYSTEM_PROMPT = """You classify personal expenses for a Spanish IRPF tax declaration.
The taxpayer is a self-employed (autónomo) resident in Comunitat Valenciana.

Categories (pick ONE):
- sanitario: medical, dental, pharmacy, optical, hospital, physio, health insurance copays
- formacion: courses, books, educational materials, certifications
- donaciones: NGOs, foundations, political parties, charity
- seguros: health insurance, life insurance, home insurance
- suministros: electricity, water, gas, internet, mobile phone
- transporte: fuel, tolls, public transport, parking (work-related)
- vivienda: home repairs, maintenance, energy efficiency improvements
- potencialmente_deducible: doesn't clearly fit above but MIGHT be deductible — WHEN IN DOUBT, USE THIS
- no_deducible: clearly personal leisure, restaurants, entertainment, groceries, clothing, subscriptions

CRITICAL RULE: If there is ANY doubt about whether an expense could be deductible, classify as "potencialmente_deducible". It is much better to have false positives than to miss a real deduction. The taxpayer will review manually.

For each expense, return JSON:
{
  "category": "one of the categories above",
  "reason": "brief explanation in Spanish (max 15 words)",
  "confidence": 0.0 to 1.0
}

Return a JSON array, same order as input."""

BATCH_SIZE = 25


@dataclass
class ClassifiedExpense:
    transaction: BankTransaction
    category: str
    reason: str
    confidence: float
    invoice_path: str = ""
    invoice_issuer: str = ""
    invoice_amount: float | None = None


def classify_transactions(
    transactions: list[BankTransaction],
    api_key: str,
    model: str = "gpt-4o-mini",
) -> list[ClassifiedExpense]:
    """Classify bank transactions into fiscal categories."""
    if not api_key:
        logger.warning("Sin API key — todos los gastos quedan como potencialmente_deducible")
        return [
            ClassifiedExpense(
                transaction=t, category="potencialmente_deducible",
                reason="Sin clasificar (no API key)", confidence=0.0,
            )
            for t in transactions
        ]

    expenses = [t for t in transactions if t.is_expense]
    income = [t for t in transactions if not t.is_expense]

    results: list[ClassifiedExpense] = []

    for i in range(0, len(expenses), BATCH_SIZE):
        batch = expenses[i:i + BATCH_SIZE]
        classified = _classify_batch(batch, api_key, model)
        results.extend(classified)

    for t in income:
        results.append(ClassifiedExpense(
            transaction=t, category="ingreso",
            reason="Ingreso (no gasto)", confidence=1.0,
        ))

    logger.info("Clasificados: %d gastos + %d ingresos", len(expenses), len(income))
    return results


def _classify_batch(
    transactions: list[BankTransaction],
    api_key: str,
    model: str,
) -> list[ClassifiedExpense]:
    from openai import OpenAI

    payload = [
        {
            "date": t.date_str,
            "description": t.description[:200],
            "amount": f"{abs(t.amount):.2f} {t.currency}",
            "bank": t.bank,
        }
        for t in transactions
    ]

    client = OpenAI(api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )

        raw = resp.choices[0].message.content or "[]"
        parsed = _parse_json_array(raw)

        results = []
        for txn, item in zip(transactions, parsed, strict=False):
            cat = item.get("category", "potencialmente_deducible")
            if cat not in FISCAL_CATEGORIES and cat != "ingreso":
                cat = "potencialmente_deducible"

            conf = float(item.get("confidence", 0.5))
            if conf < 0.7 and cat == "no_deducible":
                cat = "potencialmente_deducible"

            results.append(ClassifiedExpense(
                transaction=txn,
                category=cat,
                reason=str(item.get("reason", ""))[:100],
                confidence=conf,
            ))
        return results

    except Exception:
        logger.error("LLM classification failed", exc_info=True)
        return [
            ClassifiedExpense(
                transaction=t, category="potencialmente_deducible",
                reason="Error en clasificacion", confidence=0.0,
            )
            for t in transactions
        ]


def _parse_json_array(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("expenses", "results", "items", "classifications"):
            if isinstance(data.get(key), list):
                return data[key]
    return [data]
