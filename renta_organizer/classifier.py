"""Classifies expenses into fiscal categories using LLM. Biased toward high recall."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from renta_organizer.bank_parser import BankTransaction
from renta_organizer.cache import ResultCache, make_transaction_key

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You classify personal expenses for a Spanish IRPF tax declaration.
The taxpayer is a self-employed (autónomo) resident in Comunitat Valenciana.

Categories (pick ONE):
- sanitario: medical, dental, pharmacy, optical, hospital, physio, health insurance copays, psychology, veterinary
- formacion: courses, books, educational materials, certifications, conferences, workshops
- donaciones: NGOs, foundations, political parties, charity, humanitarian aid
- seguros: health insurance, life insurance, home insurance, vehicle insurance
- suministros: electricity, water, gas, internet, mobile phone, coworking
- transporte: fuel, tolls, public transport, parking, vehicle maintenance (if work-related)
- vivienda: home repairs, maintenance, energy efficiency improvements, furniture for home office
- actividad_profesional: software subscriptions, hosting, domains, professional tools, office supplies, hardware
- potencialmente_deducible: doesn't clearly fit above but MIGHT be deductible — WHEN IN DOUBT, USE THIS
- no_deducible: ONLY for things that are clearly personal leisure with zero chance of being deductible: restaurants with friends, cinema, videogames, clothing, supermarket groceries, alcohol, tobacco

CRITICAL RULES:
1. If there is ANY doubt, classify as "potencialmente_deducible". Better 100 false positives than 1 missed deduction.
2. Transfers between own accounts, ATM withdrawals, and loan payments are "no_deducible" (not real expenses).
3. Bizum/transfers to individuals: "potencialmente_deducible" (could be payments for services).

For each expense, return JSON:
{
  "category": "one of the categories above",
  "reason": "brief explanation in Spanish (max 15 words)",
  "confidence": 0.0 to 1.0
}

Return a JSON array, same order as input."""

FISCAL_CATEGORIES = [
    "sanitario",
    "formacion",
    "donaciones",
    "seguros",
    "suministros",
    "transporte",
    "vivienda",
    "actividad_profesional",
    "potencialmente_deducible",
    "no_deducible",
]

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
    cache: ResultCache | None = None,
) -> list[ClassifiedExpense]:
    """Classify bank transactions into fiscal categories. Uses cache to skip API calls."""
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
    to_classify: list[BankTransaction] = []
    cached_count = 0

    for t in expenses:
        key = make_transaction_key(t.date_str, t.description, t.amount)
        if cache and cache.has(key):
            hit = cache.get(key)
            results.append(ClassifiedExpense(
                transaction=t,
                category=hit["category"],
                reason=hit["reason"] + " [cache]",
                confidence=hit["confidence"],
            ))
            cached_count += 1
        else:
            to_classify.append(t)

    if cached_count:
        logger.info("Cache: %d gastos recuperados, %d pendientes de clasificar", cached_count, len(to_classify))

    for i in range(0, len(to_classify), BATCH_SIZE):
        batch = to_classify[i:i + BATCH_SIZE]
        classified = _classify_batch(batch, api_key, model)
        for exp in classified:
            if cache:
                key = make_transaction_key(
                    exp.transaction.date_str, exp.transaction.description, exp.transaction.amount,
                )
                cache.set(key, {
                    "category": exp.category,
                    "reason": exp.reason,
                    "confidence": exp.confidence,
                })
            results.append(exp)

    if cache:
        cache.save()

    for t in income:
        results.append(ClassifiedExpense(
            transaction=t, category="ingreso",
            reason="Ingreso (no gasto)", confidence=1.0,
        ))

    logger.info("Clasificados: %d gastos (%d cache + %d API) + %d ingresos",
                len(expenses), cached_count, len(to_classify), len(income))
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
