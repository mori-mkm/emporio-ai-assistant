# Visual reference audit: Pride Music Shop home (EMP-20)

Purpose: borrow the visual language of a real musical-instrument store for the demo UI. The demo stays the **Empório da Música** assistant: no Pride name, logo, images, prices, contacts, policies or commercial benefits (free shipping, installments, insurance, discounts) are used in the app.

## Method
- URL: https://www.pridemusicshop.com.br/ (home page only). Date: 2026-11-03 (around 17:00 BRT).
- Tool: headless Microsoft Edge driven over the Chrome DevTools Protocol by a local script (`.local/pride-audit/`, not versioned). The script took screenshots and read `getComputedStyle` values and element boxes. No login, no form submitted, no cart action. The cookie banner was left unclicked; a promotional dialog ("Descontos imperdíveis!") was removed from the local DOM before the second probe so it would not cover the page.
- Viewports: 1440×900 (desktop), 768×1024 (tablet), 390×844 and 360×780 (mobile), device scale 1.
- Two probes: (1) computed styles of elements found by class/id (`#buscador`, `.thumb_produto_destaque`, `.preco-full`, `.pms-nws-btn`, `footer.footer`, ...) and the media queries of readable stylesheets; (2) `document.elementFromPoint` at fixed coordinates to read the element and effective background actually visible there.
- Raw data and reference screenshots stay in the ignored `.local/pride-audit/out/` (third-party content, not redistributed).

Evidence labels used below: **computed** = value returned by `getComputedStyle` in the browser; **visual** = estimated from screenshots; **ours** = our design decision, not a reference value.

## Stack observed
- Custom storefront on the Foundation grid (`grid-container`, `cell`, `large-*`/`small-*` classes), jQuery plugins (slick carousel, jquery-confirm, lightboxes), Font Awesome icons.
- Fonts loaded from Google Fonts: **Bebas Neue 400**, **Kanit 400/600**, **Roboto Condensed 400** (computed `document.fonts`).
- Direct downloads of the CSS files (`curl`) returned a Cloudflare "Just a moment" challenge page; the HTML of the home page downloaded normally. All CSS values below therefore come from the browser's computed styles, not from source stylesheets.

## Tokens
| Token | Value | Evidence | Where observed |
|---|---|---|---|
| Brand red | `rgb(231, 34, 46)` = `#E7222E` | computed | mobile top bar background, mobile benefits band, newsletter button, hamburger/cart icons |
| Red (hover) | `rgb(233, 29, 37)` = `#E91D25` | computed | category link on hover (desktop) |
| Desktop top bar | red, 36 px tall | visual (red) + computed (container height 35.9 px) | bar with social icons above the header |
| Body text | `rgb(10, 10, 10)` = `#0A0A0A` | computed | `body` |
| Page background | `rgb(254, 254, 254)` = `#FEFEFE` | computed | `body`, search input |
| Premium section | `#000000` | computed | `.destaques-home` (black band behind product cards) |
| Footer | `rgb(36, 36, 36)` = `#242424`, padding 64 px 0 32 px | computed | `footer.footer` |
| Copyright bar | `#000000`, Roboto Condensed 14.4 px / 21.6 px, white | computed | `.copyright` |
| Field border | 1 px `rgb(202, 202, 202)` = `#CACACA` | computed | search input; newsletter input uses `#CCCCCC` |
| Product name grey | `rgb(145, 145, 145)` = `#919191` | computed | product card name |
| List price grey | `rgb(89, 89, 89)` = `#595959` | computed | `.preco-full` (crossed/list price) |
| Search icon grey | `rgb(191, 191, 191)` = `#BFBFBF` | computed | `#btnBusca` icon |
| Base font | Bebas Neue 16 px / 24 px | computed | `body` (the whole page inherits a display face) |
| Section title | Bebas Neue 31 px / 43.4 px, 400, `#0A0A0A`, red rules either side | computed (type) + visual (rules) | "Categorias mais populares" |
| Menu links | Bebas Neue 17.6 px / 28.16 px, letter-spacing 0.5 px, black, padding 1 px 16 px | computed on the first `#cssmenu a` (may be the hidden mobile copy) | main navigation |
| Price | Bebas Neue 19.36 px, weight 900 (synthetic), `#000` (cash) / `#595959` (list) | computed | product card |
| Price legal text | Bebas Neue 16 px, weight 100 | computed | "em até 10X sem juros", "à vista no Pix" |
| Button | Kanit 700 14.4 px, uppercase, letter-spacing 0.5 px, white on red, radius 4 px, height 38 px, padding 0 20 px | computed | newsletter "Cadastre-se" |
| Footer links | Roboto Condensed 16 px, 600, white | computed | footer modules |
| Footer text | Bebas Neue 12.8 px / 19.2 px, white | computed | footer modules |
| Container | max-width 1520 px, padding 0 32 px | computed | `.grid-container` |
| Field radius | 6 px | computed | search input |
| Card radius | 12 px, white on black | computed | `.miolo_produto_destaque` |
| Small radius | 4 px | computed | newsletter input and button |
| Field shadow | `inset 0 1px 2px rgba(10,10,10,.1)` | computed | search and newsletter inputs |
| Dialog shadow | `0 2px 6px rgba(0,0,0,.2)`, radius 5 px | computed | promotional dialog box |

## Components
- **Header (desktop, 1440):** red top bar 36 px (social icons) → white main bar (padding-top 16 px, about 208 px tall to y=244): logo image 118×120 at left; search input 551×39 with a 44×44 icon button; "Bem-vindo! Entre ou cadastre-se" (Bebas 11.2 px), user and cart icons; a second logo block at right; a light divider; navigation row in uppercase Bebas (Categorias ▾, Marcas ▾, PMS Premium in a red outlined box, Novidades, Em breve, Em promoção, Blog, Lojas físicas) and a red WhatsApp phone line with "Fale com um especialista" (visual).
- **Header (mobile, 390/360):** black promo strip (campaign image) → red bar 37 px with the login text (Bebas 12.8 px, white) and user icon → white row with a wordmark logo 175×26, red cart (24 px) and hamburger (32 px) icons → full-width search 308×39 plus a 44×44 button, inset 10 px from the left edge.
- **Main search:** placeholder "Encontre seu instrumento", uppercase display face, grey border, inset shadow. Focus: border unchanged, `box-shadow: 0 0 5px #CACACA`, `outline: none` (computed) — the focus indication is very faint.
- **Hero:** full-width image carousel (1425×569 desktop, black background) with round red/white arrows.
- **Benefits bar:** red band (54 px tall on mobile) of **images** with text ("Parcele em até 10x...", "Entregas com seguro..."), a slider on desktop. These are Pride's commercial terms and are **not** reused.
- **Category tiles:** "Categorias mais populares": 10 tiles 136 px wide on desktop (image 124×164, uppercase Bebas label); on mobile a 2-column grid of light-grey cards with the image at left and the label at right (visual).
- **Product cards:** black section, white cards with 12 px radius, brand logo on top, 200×200 product image, grey uppercase name, list and cash prices with legal text; carousels with red side arrows.
- **Red call-to-action band:** `.barra-home`, 152 px tall, white text and a white pill button (YouTube).
- **Blog cards, brand logo strip, help + newsletter block** (green WhatsApp button, newsletter form, a "10% OFF Pix" graphic).
- **Footer:** `#242424` with a vertical logo, four link columns (red uppercase headings, white Roboto Condensed links), social icons and payment badges; black copyright bar with company data.

## States
| State | Observation | Evidence |
|---|---|---|
| Category hover | colour changes to `#E91D25` | computed (mouse moved over the first tile link) |
| Search focus | soft grey glow, no outline | computed |
| Menu link hover | no change recorded | computed on the first `#cssmenu a`, which may be the hidden mobile copy: **inconclusive** |
| Product card hover | no change recorded on the card container | computed; inner elements not probed |
| Keyboard focus | `outline-style: none` on the probed links and buttons | computed: no visible focus ring was found |
| Disabled | not observed (no disabled controls on the home page) | — |

## Responsive behaviour
- Media queries readable in the browser are mostly Foundation's: `min-width: 40em` (640 px, 51 rules), `min-width: 64em` (1024 px, 31 rules), `max-width: 39.9988em`, `max-width: 63.9988em`, `75em` (1200 px), plus plugin and page-specific ones (500, 575/576, 720, 768/769, 850/851, 920, 992 px).
- Desktop → mobile: the navigation collapses behind a hamburger, the search moves to its own full-width row, the logo becomes a one-line wordmark, the benefits move into a red band under the hero, and category tiles become a 2-column card grid.
- Horizontal overflow: `scrollWidth` exceeded the viewport by 1 px at 768, 390 and 360 px (computed `scrollWidth` 769/391/361).

## What the assistant adapts (and what it does not)
| Reference element | Adaptation in the demo | Evidence/decision |
|---|---|---|
| Black/white/red palette | Same red `#E7222E` for bands and accents; `#0A0A0A` text on `#FEFEFE`; black and `#242424` sections | computed values reused |
| Red used for small text and buttons | `#C41A24` instead (5.97:1 on white); `#E7222E` with white text is 4.50:1, at the AA limit | ours (accessibility) |
| Field border `#CACACA` (1.64:1) | `#909090` (3.19:1, meets the 3:1 non-text contrast) | ours (accessibility) |
| No visible focus ring | 3 px `#1A5FB4` outline everywhere (lighter blue on black areas) | ours (accessibility) |
| Bebas Neue display, Kanit buttons, Roboto Condensed | Same families for headings, labels and buttons; **body and chat text in Roboto Condensed**, not Bebas (Bebas has no lowercase, too hard to read in long answers) | computed families; body choice ours |
| Red top bar | Black strip with a 4 px red rule and the label "Demo conceitual de atendimento com IA · não é um canal de vendas" | ours (demo disclosure) |
| Logo with red "SHOP" tag | Text wordmark "EMPÓRIO" with a red notched tag "DA MÚSICA" (CSS, no image) | adapted device, our own words |
| Header search "Encontre seu instrumento" | "Encontre um instrumento ou pergunte": submits the question to the same chat session | adapted |
| Navigation row of categories | Buttons for the six catalog categories that have products; each asks the assistant about that category | ours (only catalog-backed categories) |
| Red benefits band | Red band stating what the assistant helps with (catalog prices, payment rules from the manual, exchanges/delivery/warranty with the cited section): no commercial promise | ours (no Pride benefits) |
| Section titles with red rules | Side-column titles with a red rule | adapted |
| White cards, 12 px radius on dark sections | Chat panel: white card with 12 px radius, black header bar, dialog shadow | adapted |
| Product cards | **Not adapted.** The public API returns only reply text and cited sections, no structured products; cards would require a contract change (recorded as later work) | ours |
| Footer `#242424` + black copyright bar | Same two-level footer with a short demo disclaimer; no links | adapted |
| Breakpoints 40em / 64em | Same breakpoints: one column below 64em, mobile header below 40em | computed breakpoints reused |
| Container 1520 px / 32 px padding | 80rem (1280 px) max-width, 32 px padding (12 px on mobile, close to the observed 10 px) | ours (chat readability) |

## Limitations
- Only the home page was inspected. Category, product, cart, login and blog pages were not opened.
- The desktop dropdown menus were not opened; the attempt to open the mobile menu scrolled the page instead and is **not verified**.
- Source stylesheets could not be downloaded (Cloudflare challenge), so values are computed results, not authored tokens; no CSS custom properties were exposed on `:root`.
- Some first matches for a selector were hidden duplicates (the menu links and their hover probe); those rows are marked inconclusive.
- Promotional content changes (a "Black November" campaign, a promotional dialog and a cookie banner were present); colours and fonts are less likely to change than images and copy.
- Headless Edge with a desktop user-agent override; the CDN challenge did not block the browser. Results may differ on real devices and with the cookie banner accepted.
