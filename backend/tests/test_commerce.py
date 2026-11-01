"""Commercial rules (EMP-8) with small hand-checkable numbers."""

import pytest

from emporio_assistant.commerce import (
    apply_discount,
    assess_installments,
    availability,
    format_brl,
    offer,
    payment_quote,
    split_installments,
)

PROMO_18 = [{"description": "Test", "discount_basis_points": 1800}]


@pytest.mark.parametrize(
    "cents, bp, expected",
    [
        (100, 5000, 50),  # exact
        (1, 5000, 1),  # 0.5 cent -> rounds half up
        (3, 5000, 2),  # 1.5 -> 2
        (15990, 500, 15191),  # 15190.5 -> 15191
        (59990, 500, 56991),  # 56990.5 -> 56991
        (239900, 1800, 196718),  # exact
        (551908, 500, 524313),  # 524312.6 -> 524313
        (999, 1, 999),  # 998.9001 -> 999
    ],
)
def test_discount_rounds_half_up_once(cents, bp, expected):
    assert apply_discount(cents, bp) == expected


def test_brl_formatting():
    assert format_brl(1328500) == "R$ 13.285,00"
    assert format_brl(15191) == "R$ 151,91"
    assert format_brl(5) == "R$ 0,05"


@pytest.mark.parametrize(
    "status, stock, expected",
    [
        ("active", 3, "available"),
        ("active", 0, "out_of_stock"),
        ("discontinued", 13, "discontinued"),
        ("coming_soon", 2, "coming_soon"),
    ],
)
def test_availability_classes(status, stock, expected):
    assert availability(status, stock) == expected


def test_only_one_active_promotion_sets_the_offer_price():
    assert offer(239900, [])["offer_price"]["cents"] == 239900
    assert offer(239900, PROMO_18)["offer_price"]["cents"] == 196718
    two = offer(1000, PROMO_18 + [{"description": "B", "discount_basis_points": 1000}])
    assert two["state"] == "needs_confirmation" and "offer_price" not in two


def test_pix_on_list_price_but_not_on_promotion():
    plain = payment_quote(129900, [], "pix")
    assert plain["final_price"]["cents"] == 123405 and plain["pix_discount_applied"]
    promo = payment_quote(599900, [{"description": "T", "discount_basis_points": 800}], "pix")
    assert promo["final_price"]["cents"] == 551908 and not promo["pix_discount_applied"]


def test_multiple_active_promotions_need_confirmation():
    promos = PROMO_18 + [{"description": "B", "discount_basis_points": 2000}]
    quote = payment_quote(10000, promos, "pix")
    assert quote["state"] == "needs_confirmation" and quote["final_price"] is None


@pytest.mark.parametrize(
    "total, count, classification, each",
    [
        (43920, 4, "supported", 10980),  # meets R$100 general and R$80 tier
        (43920, 5, "needs_confirmation", 8784),  # meets R$80 tier, fails R$100 general
        (43920, 6, "not_supported", 7320),  # fails both
        (219900, 12, "supported", 18325),
        (30000, 3, "supported", 10000),
        (15990, 2, "needs_confirmation", 7995),  # up to 3x has no minimum; general needs R$100
        (4000, 1, "needs_confirmation", 4000),  # 'except below R$ 50,00' is unclear
        (1328500, 13, "not_supported", None),  # nothing offers 13x
    ],
)
def test_installments_are_classified_against_both_rules(total, count, classification, each):
    plan = assess_installments(total, count)
    assert plan["classification"] == classification
    if each is not None:
        assert {a["cents"] for a in plan["amounts"]} == {each}
    assert len(plan["rules"]) == 2


def test_split_keeps_the_exact_total():
    assert split_installments(10000, 3) == [3334, 3333, 3333]
    assert sum(split_installments(123457, 7)) == 123457


def test_card_quote_uses_promotional_price_and_no_interest():
    quote = payment_quote(54900, [{"description": "T", "discount_basis_points": 2000}],
                          "credit_card", 5)  # fmt: skip
    assert quote["final_price"]["cents"] == 43920
    assert quote["installment_plan"]["classification"] == "needs_confirmation"
    assert sum(a["cents"] for a in quote["installment_plan"]["amounts"]) == 43920


def test_split_payment_is_flagged_not_invented():
    notes = payment_quote(1328500, [], "boleto")["notes"]
    assert any("not defined by the sources" in n for n in notes)
    assert not any("not defined" in n for n in payment_quote(129900, [], "boleto")["notes"])


def test_installments_only_with_credit_card():
    with pytest.raises(ValueError):
        payment_quote(1000, [], "pix", 3)
    with pytest.raises(ValueError):
        payment_quote(1000, [], "credit_card")
