# Data preparation (EMP-5)

The agent will read a local SQLite catalog and a page/section extraction of the store
manual. Both are generated from the challenge sources by one command. The sources and
everything derived from them stay local (git-ignored) until their distribution is decided.

## Command

```powershell
$src = 'C:\path\to\Desafio Técnico AI Engineer 2026'   # the extracted challenge folder
.\.venv\Scripts\python.exe -m emporio_assistant.prepare_data `
    --csv-dir "$src\data" `
    --manual "$src\data\políticas_da_loja.pdf" `
    --output-dir data\derived
```

`emporio-prepare-data` (installed in `.venv\Scripts`) is the same entry point. All three
arguments are required; quote paths with spaces or accents. Exit code 0 = a new generation is
active; 1 = validation or publication failed and the active generation was not changed. The
output folder must be outside the source folders.

`--csv-dir` must contain exactly one file per table, named `<table>.csv` or
`<prefix> - <table>.csv` (the challenge uses `desafio_tecnico_ai_eng - products.csv`).
Only `categories`, `products` and `promotions` are read. `customers`, `orders` and
`order_items` are never opened or loaded: v1 has no customer or order access.

Each run publishes one **generation**: a folder holding the three files, made active by a
single manifest.

```text
data/derived/
  current.json                      manifest: active generation id + SHA-256 of each file
  generations/<id>/catalog.sqlite   catalog tables + source_files (file name, SHA-256, records)
  generations/<id>/policies.json    manual source metadata, text per page, sections
  generations/<id>/quality_report.json  counts, normalization notes, warnings; no personal data
```

Publication steps: validate all sources → write a new `generations/<id>/` → read the files
back (JSON parse, SQLite integrity and row counts) → replace `current.json` with one
`os.replace`. Only that last step changes what is active, so a consumer never sees a mix of
generations. If anything fails, the command exits 1, `current.json` still names the previous
generation, and an unused `generations/<id>/` folder may remain (never referenced; delete it
manually if you like). Old generations are not deleted automatically.

Consumers must resolve files through `emporio_assistant.generations.active_generation(dir)`.
It reads the manifest once, accepts only the three expected file names inside
`generations/<id>/`, checks each file's SHA-256 and rejects a missing, malformed or
incomplete generation. Files are fsynced before the switch; there is no guarantee for a
power loss at the instant of the rename (Windows has no directory fsync).

Reruns on the same sources create a new generation with the same content (same rows, same
`policies.json` bytes; JSON is written with LF line endings on every OS).

**Legacy files.** Runs before this layout wrote `catalog.sqlite`, `policies.json` and
`quality_report.json` directly in the output root. They are not read by anything, are not
deleted automatically, and the command prints a note while they exist. Delete them manually.

## Catalog schema

| Table | Columns (constraints) |
|---|---|
| `categories` | `category_id` INTEGER PK > 0, `name` TEXT non-empty, `description` TEXT |
| `products` | `product_id` INTEGER PK > 0, `name`, `category_id` → categories, `description`, `price_cents` INTEGER > 0, `stock_quantity` INTEGER ≥ 0, `status` ∈ active/discontinued/coming_soon, `specs_json` TEXT (valid JSON object, original text kept), `created_at` TEXT ISO date |
| `promotions` | `promotion_id` INTEGER PK > 0, `product_id` → products, `discount_basis_points` INTEGER 1–9999, `description` TEXT, `is_active` INTEGER 0/1 |
| `source_files` | `role`, `file_name`, `sha256`, `records` |

Tables are SQLite `STRICT`; foreign keys, integrity and row counts are checked before the
file is published.

**Money.** `price_brl` is parsed with `Decimal` (plain digits, `.` decimal separator, at most
2 places) and stored as integer cents: `599.9` → `59990`. Discounts are stored as integer
basis points: `15` → `1500`. No float is used. Final prices, PIX and installment
calculations are in `commerce.py` (EMP-8, see `docs/tools.md`).

## Validation

Errors block the load (exit 1): missing/duplicated columns, rows with the wrong number of
fields, non-positive or duplicate ids, unknown category/product references, prices that are
not positive decimals with ≤ 2 places, stock that is not a non-negative integer, unknown
status, specs that are not a JSON object, invalid dates, discounts outside (0, 100), and
`is_active` other than exactly `0`/`1` (no `true`/`yes` coercion).

Warnings are loadable business ambiguities the agent must handle explicitly: active product
with zero stock, discontinued or coming-soon product with stock, category without products,
duplicate names, more than one promotion row (or more than one active promotion) for a
product, active promotion on an unavailable product, empty descriptions, ignored extra
columns.

Normalization: UTF-8 with an optional BOM removed; leading/trailing whitespace stripped from
each cell and counted in the report. Inner text is unchanged. Messages cite table, CSV line
and record id, never cell values.

## Snapshot of the challenge data (static, as received)

Sources verified on 2026-11-01: UTF-8 without BOM, comma-separated, CRLF line endings, no
trailing newline (counting lines would undercount by one; `csv.DictReader` is used).

| | Records |
|---|---|
| categories | 9 (3 have no products: ids 6, 7, 8) |
| products | 65 (61 active with stock, 1 active with zero stock, 2 discontinued, 1 coming soon) |
| promotions | 25 (4 active) |
| manual | 8 pages, 29 sections (outline + front page) |

Prices range from R$ 159,90 to R$ 19.567,00; stock from 0 to 15. No validation errors and no
whitespace normalization were needed. Warnings on the real data:

| Warning | Record |
|---|---|
| active with zero stock | product 96 |
| discontinued with stock (13) | product 136 |
| coming soon with stock (2) | product 130 |
| category without products | categories 6, 7, 8 |
| two promotion rows, none active | products 93, 96, 99 |

The data is a static snapshot, not a live inventory feed.

## Manual extraction

`políticas_da_loja.pdf` (Versão 2.1 — Junho de 2025; SHA-256
`b7527f96896929f4ba929de117651c6717e25b7a3b92b34bc54d5ba32f6156f7`) is extracted locally with
pypdf. The default extraction mode put one word per line and flattened the tables in this
Google Docs export; `extraction_mode="layout"` keeps lines, table columns and reading order,
and was checked page by page against the PDF. Sections come from the PDF outline (title and
page); a heading that can't be found on its page fails the run. The running header and the
"Página N" footers are left out of section text but kept in the per-page text. Text is stored
as extracted; the notes below are interpretation and are kept separate from it. The hash
identifies the bytes used, not the correctness of the text.

## Source evidence and open questions

References are manual section (page). These are documented facts and doubts, not decisions;
how the tools apply them is in `docs/tools.md` (EMP-8).

- **Contacts disagree.** Phone/WhatsApp (67) 3341-4444 on the cover (p1) and in §1.2 (p2);
  §7 (p6) names WhatsApp (67) 3321-4500 as the main remote channel. E-mail is
  `contato@emporiodamusica.com.br` on the cover but `contato@empóriodamusica.com.br`
  (accented domain) in §1.2. The agent must not silently pick one.
- **Installments.** §3 (p3) table: credit card "até 12x sem juros", minimum installment
  R$ 100,00. §3.1 (p3) is tiered: up to 3x with no minimum "(exceto abaixo de R$ 50,00)",
  4–6x minimum R$ 80,00, 7–12x minimum R$ 100,00. The general row conflicts with the tiers,
  and the R$ 50,00 exception is unclear.
- **PIX and promotions.** §3 (p3): PIX gives 5% off the list price. §6.2 (p6): promotions are
  not cumulative and the PIX 5% does not apply to already-promotional prices. Consistent with
  the planned rule.
- **Promotion validity.** The CSV has only `is_active`, no dates. §7.3 (p7) says to check
  whether a promotion is active "no sistema", which supports using the flag as the current
  state. §6.1 (p6) lists campaign months; the agent must not infer activity from the calendar.
  Inactive rows fall outside the §6.1 ranges (a "Black Friday" row at 10% vs 15–30%; an
  "Aniversário da Loja" row at 30% vs 10–25%). Active campaigns ("Desconto Exclusivo",
  "Oferta Especial") are not in the §6.1 list, which says "as principais campanhas incluem".
- **Out of stock.** §7.3 (p7): say the product is temporarily unavailable, suggest available
  alternatives, never confirm availability without checking stock. Product 96 is `active`
  with zero stock, so status alone is not availability.
- **Discontinued with stock.** §7.3 (p7): a discontinued product "não faz mais parte do
  catálogo". Product 136 is discontinued with 13 units. The sources conflict; EMP-8 classifies
  it as `discontinued` (no immediate purchase) and keeps the recorded stock as data.
- **Coming soon.** The manual does not mention `coming_soon`. Product 130 has stock 2. No
  release date or purchase rule exists in the sources.
- **Shipping.** §5.1 (p4): free "acima de R$ 500,00", fixed R$ 35,00 "abaixo de R$ 500,00";
  exactly R$ 500,00 is not covered. §5.2 (p5): freight outside the metro area depends on CEP,
  weight and dimensions, which the agent doesn't have.
- **Catalog scope.** §1 (p2) claims more than 300 instruments and lists wind and orchestral
  strings; the snapshot has 65 products and no products in categories 6–8. Accessories are not
  sold (§1 p2, §7.1 p6).
- **Orders and customers.** The assignment lists order status as an example query and ships
  order/customer tables. v1 deliberately does not load them (privacy, no authentication), so
  the agent must say it can't check orders instead of guessing.
- **Not in the sources (the agent must not invent):** promotion periods, freight values outside
  the metro area, coming-soon dates, holiday calendar and extended hours, partner stores
  (§7.1 suggests them "quando possível" but names none), a complaint registration system
  (§7.3 asks to register and forward complaints; v1 has no such connector), per-product
  manufacturer warranty, and purchase/receipt dates needed for returns (§4.1, §4.2, §8.1).
- **Doubt rule.** §10 (p8): in case of doubt the attendant consults management before
  answering.

## Synthetic fixtures vs challenge data

`backend/tests/fixtures/synthetic_catalog/` holds small invented CSVs (marked as test data)
and the tests generate a synthetic PDF manual on the fly. They exist so tests run in a clean
clone; they are not the Empório catalog. The challenge files are not copied into the
repository. Tests: `cd backend; ..\.venv\Scripts\python.exe -m pytest`.
