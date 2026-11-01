# Read-only tools and commercial rules (EMP-8)

Code: `backend/emporio_assistant/tools.py` (tools) and `commerce.py` (rules). No agent, prompt
or API endpoint uses them yet; EMP-9 will expose `tool_schemas()` to the model adapter.

## Data access
`CatalogSnapshot(data_dir)` calls `generations.active_generation` once and keeps that
generation (catalog, policies, id) for its whole life: a new generation published meanwhile
is not mixed in. The catalog is opened with a `mode=ro` URI (a missing file is an error, never
a new empty DB) plus `PRAGMA query_only`. The budget ledger is a different file. Missing or
broken data → error code `data_unavailable`.

## Tools (fixed allowlist)
`call_tool(snapshot, name, arguments)` is the single entry point. Arguments are validated by
strict Pydantic models (`extra="forbid"`, no type coercion: `"5"` is not an int, `1` is not a
bool). The model cannot pass SQL, table/column names, paths, URLs, generation ids, prices or
discount rates. No customer, order, write, file, shell or network tool exists.

| Tool | Arguments | Returns |
|---|---|---|
| `search_products` | `query` (2–80 chars, ≤ 8 words), `category_id`, `min_price_cents`, `max_price_cents`, `available_only`, `sort` (`name`/`price_asc`/`price_desc`), `limit` 1–10 (default 5), `offset` 0–200 | summaries + `total_matches`, `has_more` |
| `get_product` | `product_id` | description, specs, recorded stock, status, availability + guidance, active promotion |
| `quote_price` | `product_id`, `payment_method` (`pix`/`debit_card`/`credit_card`/`boleto`), `installments` 1–24 (credit card only, required there) | list/offer/final price, notes, installment classification |
| `list_categories` | none | categories with product and available counts (shows empty ones) |

Search semantics: every word must occur in name, category, description or specs, ignoring
case and accents (`violao` finds `Violão`). Matching is a literal substring (`instr`), so
`%`, `_` and quotes have no special meaning. Because descriptions are searched, a word can
match a product that only mentions it (e.g. a tenor ukulele described as louder than a
soprano); summaries carry the name so the agent can tell. Price filters use the **offer
price** (promotional price when exactly one promotion is active, else list price), the same
value `quote_price` starts from; they ignore PIX. Products whose price needs confirmation are
left out when a price filter is set. Ties are sorted by product id.

Example:
```json
call_tool(s, "quote_price", {"product_id": 121, "payment_method": "credit_card", "installments": 5})
→ {"ok": true, "tool": "quote_price", "snapshot": "<generation id>", "data": {
    "availability": "available", "state": "promotion",
    "list_price": {"cents": 54900, "brl": "R$ 549,00"},
    "promotion": {"description": "Desconto Exclusivo", "discount_percent": "20%"},
    "offer_price": {"cents": 43920, "brl": "R$ 439,20"},
    "final_price": {"cents": 43920, "brl": "R$ 439,20"},
    "installment_plan": {"classification": "needs_confirmation", "amounts": [{"cents": 8784, ...} ×5],
      "rules": [{"source": "manual §3 p3, payment table", "result": "fails"},
                {"source": "manual §3.1 p3, installment tiers", "result": "meets"}]}, ...}}
```
Errors: `{"ok": false, "error": {"code", "message"[, "fields"]}}` with codes `unknown_tool`,
`invalid_arguments` (field name + problem type, never the input), `not_found`,
`data_unavailable`, `internal_error`. No SQL, path or traceback is returned; details go to the
local Python log. Descriptions/specs are returned as data; they cannot change authorization or
arguments. This is a code boundary only: resistance of the future agent to prompt injection is
not claimed until it is tested with a model.

## Availability (service rule over the snapshot, record unchanged)
| Data | `availability` | Rule |
|---|---|---|
| active, stock > 0 | `available` | may be offered (snapshot, not live inventory) |
| active, stock 0 | `out_of_stock` | temporarily unavailable; suggest alternatives (§7.3) |
| discontinued | `discontinued` | no immediate purchase even with recorded stock (§7.3) |
| coming_soon | `coming_soon` | not available, no launch date in the sources |

`recorded_stock` is the number in the data; eligibility to offer is `availability`.

## Prices, promotions, PIX
- List price from the catalog; only a promotion with `is_active = 1` changes it; no dates are
  inferred. Inactive promotions are not returned.
- One active promotion → offer price; more than one → `needs_confirmation`, no price (the
  sources define no priority; the larger discount is not picked).
- PIX 5% applies to the list price only; never on top of a promotion (§3, §6.2).
- Debit card and boleto: offer price. Boleto clears in up to 3 business days.
- Above R$ 2.000,00 a note says combining methods is allowed (§3.1) and that the PIX discount
  on part of a split payment is not defined.
- **Rounding (prototype convention, not stated by the sources):** exact computation, rounded
  once ROUND_HALF_UP to the cent (R$ 159,90 with PIX → R$ 151,905 → R$ 151,91).

## Installments
Two manual rules disagree: the §3 table (up to 12x, minimum R$ 100,00) and the §3.1 tiers
(up to 3x no minimum "exceto abaixo de R$ 50,00"; 4–6x minimum R$ 80,00; 7–12x minimum
R$ 100,00). Each plan is checked against both: both met → `supported`; both failed →
`not_supported`; otherwise (or the unclear R$ 50,00 exception) → `needs_confirmation`. More than
12x is `not_supported`. No interest or fee is added (both say "sem juros").
Examples: R$ 439,20 → 4x R$ 109,80 supported, 5x R$ 87,84 needs confirmation, 6x R$ 73,20 not
supported; R$ 2.199,00 → 12x R$ 183,25 supported.
A split keeps the exact total; remainder cents go one each to the first installments
(R$ 100,00 in 3x → 33,34 + 33,33 + 33,33). This is a simulation convention, not the store's
or a card operator's rule.

## Not resolved by the tools
Contact divergence and shipping at exactly R$ 500,00 stay open questions (see `docs/data.md`);
the tools do not pick an answer. Policy text reaches the agent through the full manual in
EMP-9, not through these tools.

## Try it (PowerShell, offline, needs prepared data)
```powershell
.\.venv\Scripts\python.exe -m emporio_assistant.tools schemas
'{"query": "violao", "available_only": true, "max_price_cents": 100000, "sort": "price_asc"}' | .\.venv\Scripts\python.exe -m emporio_assistant.tools search_products -
'{"product_id": 121, "payment_method": "credit_card", "installments": 5}' | .\.venv\Scripts\python.exe -m emporio_assistant.tools quote_price -
```
Arguments are piped through stdin (`-`) because Windows PowerShell 5.1 strips double quotes
from JSON passed directly as a native-command argument.
