"""Deterministic commercial rules (EMP-8). Integer cents only, no float.

Rule sources (store manual, Versão 2.1): payment table §3 p3, installment tiers §3.1 p3,
promotion rules §6.2 p6, availability §7.3 p7. Where the manual is silent or contradicts
itself, the functions return `needs_confirmation` instead of picking an answer.

Prototype conventions (not stated by the store or a card operator):
- discounts are computed exactly and rounded once, ROUND_HALF_UP to the cent;
- an installment split keeps the exact total: the remainder cents go one each to the first
  installments (e.g. R$ 100,00 in 3x -> 33,34 + 33,33 + 33,33).
"""

from __future__ import annotations

PIX_DISCOUNT_BASIS_POINTS = 500  # §3: "5% de desconto sobre o preço de tabela"
MAX_INSTALLMENTS = 12  # §3 and §3.1
GENERAL_MIN_INSTALLMENT_CENTS = 10_000  # §3 table: "Parcela mínima de R$ 100,00"
UP_TO_3X_EXCEPTION_CENTS = 5_000  # §3.1: "(exceto abaixo de R$ 50,00)", meaning unclear
COMBINED_PAYMENT_ABOVE_CENTS = 200_000  # §3.1: combination allowed above R$ 2.000,00
PAYMENT_METHODS = ("pix", "debit_card", "credit_card", "boleto")


def apply_discount(cents: int, basis_points: int) -> int:
    """cents * (1 - bp/10000), rounded half up to the cent, in integer arithmetic."""
    if cents < 0 or not 0 <= basis_points < 10_000:
        raise ValueError("invalid amount or discount")
    numerator = cents * (10_000 - basis_points)
    return (2 * numerator + 10_000) // 20_000


def format_brl(cents: int) -> str:
    reais, centavos = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


def money(cents: int) -> dict:
    return {"cents": cents, "brl": format_brl(cents)}


def availability(status: str, stock_quantity: int) -> str:
    """Service rule over the snapshot; the catalog record itself is unchanged."""
    if status == "active":
        return "available" if stock_quantity > 0 else "out_of_stock"
    if status in ("discontinued", "coming_soon"):
        return status
    raise ValueError(f"unknown status {status!r}")


AVAILABILITY_GUIDANCE = {
    "available": "Active with stock in the data snapshot (not live inventory).",
    "out_of_stock": "Temporarily unavailable: do not confirm availability; suggest alternatives.",
    "discontinued": "No longer in the catalog: do not confirm an immediate purchase, even if "
    "units are recorded; suggest active alternatives.",
    "coming_soon": "Not available yet and no launch date in the sources: do not confirm "
    "availability or invent a date.",
}


def offer(list_price_cents: int, active_promotions: list[dict]) -> dict:
    """Price before payment-method discounts. Promotions are not cumulative (§6.2)."""
    base = {"list_price": money(list_price_cents)}
    if not active_promotions:
        return {**base, "state": "list_price", "offer_price": money(list_price_cents)}
    if len(active_promotions) > 1:
        # The sources define no priority between simultaneous promotions.
        return {**base, "state": "needs_confirmation", "reason": "multiple_active_promotions"}
    promo = active_promotions[0]
    price = apply_discount(list_price_cents, promo["discount_basis_points"])
    return {
        **base,
        "state": "promotion",
        "promotion": {
            "description": promo["description"],
            "discount_percent": _percent(promo["discount_basis_points"]),
        },
        "offer_price": money(price),
    }


def _percent(basis_points: int) -> str:
    whole, rest = divmod(basis_points, 100)
    return f"{whole}%" if not rest else f"{whole}.{rest:02d}".rstrip("0") + "%"


def split_installments(total_cents: int, count: int) -> list[int]:
    base, remainder = divmod(total_cents, count)
    return [base + 1] * remainder + [base] * (count - remainder)


def assess_installments(total_cents: int, count: int) -> dict:
    """Classify an interest-free plan against both manual rules without choosing between them."""
    amounts = split_installments(total_cents, count)
    smallest = min(amounts)

    if count > MAX_INSTALLMENTS:
        general = tier = "fails"
        tier_rule = "no rule offers more than 12x"
    else:
        general = "meets" if smallest >= GENERAL_MIN_INSTALLMENT_CENTS else "fails"
        if count <= 3:
            tier_rule = "up to 3x: no minimum, 'except below R$ 50,00' (unclear)"
            clear = smallest >= UP_TO_3X_EXCEPTION_CENTS and total_cents >= UP_TO_3X_EXCEPTION_CENTS
            tier = "meets" if clear else "unclear"
        else:
            minimum = 8_000 if count <= 6 else 10_000
            tier_rule = f"{'4x-6x' if count <= 6 else '7x-12x'}: minimum {format_brl(minimum)}"
            tier = "meets" if smallest >= minimum else "fails"

    results = {general, tier}
    if results == {"meets"}:
        classification = "supported"
    elif results == {"fails"}:
        classification = "not_supported"
    else:
        classification = "needs_confirmation"
    return {
        "installments": count,
        "classification": classification,
        "amounts": [money(a) for a in amounts],
        "interest": "none stated by the sources (sem juros)",
        "rules": [
            {
                "source": "manual §3 p3, payment table",
                "rule": f"up to 12x, minimum installment "
                f"{format_brl(GENERAL_MIN_INSTALLMENT_CENTS)}",
                "result": general,
            },
            {"source": "manual §3.1 p3, installment tiers", "rule": tier_rule, "result": tier},
        ],
    }


def payment_quote(
    list_price_cents: int,
    active_promotions: list[dict],
    method: str,
    installments: int | None = None,
) -> dict:
    """Final price for one unit and one payment method, from catalog values only."""
    if method not in PAYMENT_METHODS:
        raise ValueError(f"unknown payment method {method!r}")
    if (installments is not None) != (method == "credit_card"):
        raise ValueError("installments apply to credit_card only, and credit_card needs them")
    current = offer(list_price_cents, active_promotions)
    quote = {"payment_method": method, **current}
    if current["state"] == "needs_confirmation":
        quote["final_price"] = None
        return quote

    offer_cents = current["offer_price"]["cents"]
    notes = []
    if method == "pix":
        if current["state"] == "promotion":
            final = offer_cents
            notes.append("PIX 5% does not apply to promotional prices (manual §6.2)")
        else:
            final = apply_discount(list_price_cents, PIX_DISCOUNT_BASIS_POINTS)
            notes.append("PIX 5% off the list price (manual §3)")
        quote["pix_discount_applied"] = current["state"] != "promotion"
    else:
        final = offer_cents
        if method == "boleto":
            notes.append("boleto clears in up to 3 business days (manual §3)")
    quote["final_price"] = money(final)
    if method == "credit_card":
        quote["installment_plan"] = assess_installments(final, installments)
    if final > COMBINED_PAYMENT_ABOVE_CENTS:
        notes.append(
            "combining payment methods is allowed above R$ 2.000,00 (manual §3.1); how the "
            "PIX discount applies to part of a split payment is not defined by the sources"
        )
    quote["notes"] = notes
    return quote
