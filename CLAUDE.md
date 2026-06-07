# CLAUDE.md — itbisa-shopee-order-bot

Python bot: fetch Shopee orders → ship to dropoff → generate/send waybill labels to Telegram → dispatch stock balance once. Runs once per invocation, then exits.

## Stack & files
- Python 3.11.
- `src/main.py` (orchestration), `src/shopee_client.py`, `src/shopee_auth.py`, `src/label_processor.py`, `src/telegram_sender.py`, `src/state_manager.py`, `src/balance_dispatcher.py`.
- Workflow: `.github/workflows/run.yml`.
- **Track unit: `order_sn`.**

## Constants
`TOKEN_REFRESH_BUFFER_MINUTES = 10`, `STATE_RETENTION_DAYS = 3`, `MAX_ORDERS_PER_RUN = 30`, `LABEL_IMAGE_DPI = 200`. `SHOPEE_API_BASE_URL = https://partner.shopeemobile.com`.

## State / tokens (committed to bot-state)
- `data/processed_orders.json`, `data/shopee_tokens.json`.
- Token file fields: `access_token`, `refresh_token`, `access_token_expires_at`.
- **Do NOT add `refresh_token_expires_at` for Shopee** (TikTok Shop has it; Shopee does not).
- Save rotated tokens to `data/shopee_tokens.json` immediately after refresh.

## Order flow (key invariants)
- Statuses: `READY_TO_SHIP`, `PROCESSED`. De-duplicate by `order_sn`.
- `get_order_detail` `response_optional_fields` **must include `package_list`** (required by `_is_ready_to_ship`).
- No new orders → heartbeat, no balance dispatch.
- New orders > `MAX_ORDERS_PER_RUN` → stop and alert via Telegram.
- For `READY_TO_SHIP`: pre-check `_is_ready_to_ship(order)` BEFORE `v2.logistics.ship_order`. Skip silently (Actions log only, retry next run) when not ready.
- Only when the pre-check passes: `ship_order_to_dropoff(order_sn)` with `dropoff: {}` (= "Atur Pengiriman" → "Antar ke Counter").
- Label flow: `get_shipping_document_parameter` → `get_tracking_number` → `create_shipping_document(tracking_number)` → `download_shipping_document`. If tracking or PDF not ready: skip and retry next run.
- Convert PDF → PNG, merge every 2 PDF pages into 1 vertical image, send via `sendPhoto`.
- Mark `order_sn` processed ONLY after Telegram confirms every image part delivered. Save state immediately after each success.
- After success, record `_pick_balance_sku(item)` for every `order.item_list` entry.
- After the loop + final save: dispatch `/stock_balance` once with all touched base SKUs in a single `workflow_dispatch`.
- Heartbeat summary includes the balance result.

## Critical helpers — module scope in `src/main.py`
- `_is_ready_to_ship(order)`: reads `order.package_list[0].package_number`, calls `shopee_client.get_package_detail(order_sn, package_number)`. Returns `True` ONLY when `response.package_list[0].fulfillment_status == "LOGISTICS_READY"` AND `is_shipment_arranged == false`. Any failure mode (missing `package_list`/`package_number`, API exception, not-ready state) → `False`; caller increments `skipped_count` and continues. Same skip-and-retry pattern as tracking-/label-not-ready. Cost: +1 GET per `READY_TO_SHIP` order per run.
- `_pick_balance_sku(item)`: `model_sku` (variant) first, `item_sku` (parent) second. **No `item_name` fallback** — an item name is never a valid stock-bot catalog key. Returns `""` when both empty (caller skips recording). Do NOT import `_pick_sku` from `telegram_sender`; its third tier differs.

`telegram_sender._pick_sku(item)`: variant → parent → `item_name`. Caption only.

## shopee_client.get_package_detail(order_sn, package_number)
GET `/api/v2/order/get_package_detail`. Param name is **`package_number_list`** (CSV string) — pass the single `package_number` as the value. Response shape: `response.package_list[0].{fulfillment_status, is_shipment_arranged, …}`. Shop-level signed GET. Raises `RuntimeError` on Shopee API error (caught by `_is_ready_to_ship` as "not ready").

## Signing
- Partner/auth signature = `partner_id + path + timestamp`.
- Shop-level signature = `partner_id + path + timestamp + access_token + shop_id`.
- HMAC-SHA256 with `partner_key`.
- `SHOPEE_PARTNER_ID` / `SHOPEE_SHOP_ID` stored as raw strings; `int()` cast only at JSON-body call sites.

## Telegram output
- Bahasa Indonesia. Labels sent as PNG photo(s), not PDFs.
- First image gets the full caption; later images get "Bagian X/N".
- Caption item lines: `• {qty} x {sku}` — single space, no leading indent. SKU via `_pick_sku`, plus courier.
- Do NOT show recipient name/address (Shopee masks it; the label already contains it).
- Heartbeat uses the plain label `Shopee` (hardcoded in `telegram_sender.build_summary`; no `SHOPEE_LABEL` constant in this repo):
    - `✅ Shopee - 11:00 - Tidak ada pesanan baru`
    - `✅ Shopee - 12:00 - 3 label terkirim`
    - `⚠️ Shopee - 13:00 - 2 terkirim, 1 gagal (akan dicoba lagi)`
- Append `⚖️ Stock Balance: X/Y SKU dipicu` when balance fired this run.

## balance_dispatcher.py — duplicated across both order bots intentionally
- `class BalanceDispatcher` with `record(sku)` and `dispatch_all()`.
- `record()`/`to_base_sku()`: strips leading `^\d+PCS-` and uppercases; ignores empty/None; dedupes via internal set.
- `dispatch_all()`: fires a SINGLE `workflow_dispatch` on `furqonajiy/itbisa-shop-stock-bot/balance.yml`, `ref=main`, `sku` = all collected base SKUs space-joined, `dry_run=false`. One HTTP call regardless of SKU count.
- Requires env `STOCK_DISPATCH_TOKEN`. If missing, all collected SKUs reported failed; the run still finishes normally.
- Returns `{requested, dispatched, failed, skus}`; counts reflect SKUs, not dispatch calls.
- Best-effort: dispatch failure is logged and reported in the heartbeat, **never raised**.
- Shopee records via `_pick_balance_sku(item)`, never raw `item_sku`. `record()` is called only in the success branch (after Telegram confirms delivery and state is saved); `dispatch_all()` once after the loop + final save.

## Workflow (run.yml) — required config
- Trigger: `workflow_dispatch` only (manual from the Actions tab, or dispatched by the Telegram Worker). No `schedule`/cron.
- Checkout `main` as source; overlay `data/` from `bot-state`; run the bot once; commit updated state/token files to `bot-state` with `if: always()` (so token rotations / delivered labels persist even before a later failure).
- Concurrency: group `bot-state-${{ github.repository }}`, `cancel-in-progress: false`.
- Install `poppler-utils` (pdf2image needs it). Python 3.11.
- `actions/checkout@v5+`, `actions/setup-python@v6+`.
- `permissions.contents: write`.
- Run-step env must include `STOCK_DISPATCH_TOKEN`.

## Secrets
Shopee `partner_id`/`key`/`shop_id`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `STOCK_DISPATCH_TOKEN`.

## Global architecture & conventions (shared across all ITBisa repos)
- GitHub Actions only. No VM, server, database, queue, or long-running process.
- `main` = source code. `bot-state` = runtime state/token files only. Never protect `bot-state` (bots must push to it). Never commit live token files to `main`.
- Never hardcode secrets.
- Self-contained repo, no shared library — `balance_dispatcher.py` is duplicated across the order bots on purpose; do not factor it out.
- Minimal, targeted changes only. No broad refactors; preserve existing behavior unless explicitly in scope.
- Telegram user-facing strings: Bahasa Indonesia. Never abbreviate "TikTok Shop" to "TikTok". Use "stock", not "inventory" (except real endpoint names).
- Platform label: heartbeats use the plain string `Shopee` (no glyph, no label constant in this repo). Single-space formatting; no multi-space alignment.
- Runtime dispatch/checkout ref is `main`. `feature/improve` must be merged to `main` before production uses it.

## Development workflow (process standard)
- Branch from `main` using `feature/<short-description>` (e.g. `feature/document-dev-workflow`).
- Always open a PR into `main` and **merge with a merge commit (`--no-ff`)** — never squash, never fast-forward — so the feature branch stays an ancestor of `main`.
- Commits and PRs are authored as **`C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`** (never "Claude").
- Keep changes minimal and targeted; update `CLAUDE.md` / `README.md` in the same PR whenever behavior or process changes.
- `PROJECT_INSTRUCTIONS.md` is the synced source for the Claude & ChatGPT project instructions (≤ 8000 chars, ChatGPT limit). Update it **only when explicitly asked**, not on every change.
- Sync marker: a file named `YYYY-MM-DD_HHMM.txt` (WIB) sits at the repo root. **On every update to this repo, rename it to the current WIB timestamp** — it signals whether the repo / Claude / ChatGPT instructions are in sync.

## Flag before changing
State/token format, signing, `bot-state`, `workflow_dispatch`-only trigger, `_is_ready_to_ship` semantics + the `package_list` dependency in `response_optional_fields`, `get_package_detail` param name / response shape, `_pick_balance_sku` tiers, `balance_dispatcher` batching / best-effort model, label flow, workflow concurrency (`cancel-in-progress: false`), Telegram chat authorization, token rotation.