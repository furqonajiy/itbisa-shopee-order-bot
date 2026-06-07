# Project Instructions — itbisa-shopee-order-bot

> Synced source for **Claude** and **ChatGPT** project instructions — paste this same text into both. Keep ≤ 8000 characters (ChatGPT limit, incl. spaces). Update only when explicitly requested.

## What this is
Python bot: fetch Shopee orders → ship to dropoff → generate/send waybill labels to Telegram → dispatch stock balance once. Runs once per invocation, then exits. **Track unit: `order_sn`.**

## Stack & files
- Python 3.11. `src/main.py` (orchestration), `src/shopee_client.py`, `src/shopee_auth.py`, `src/label_processor.py`, `src/telegram_sender.py`, `src/state_manager.py`, `src/balance_dispatcher.py`. Workflow: `.github/workflows/run.yml`.

## Constants
`TOKEN_REFRESH_BUFFER_MINUTES = 10`, `STATE_RETENTION_DAYS = 3`, `MAX_ORDERS_PER_RUN = 30`, `LABEL_IMAGE_DPI = 200`. `SHOPEE_API_BASE_URL = https://partner.shopeemobile.com`.

## State / tokens (committed to bot-state)
- `data/processed_orders.json`, `data/shopee_tokens.json`.
- Token fields: `access_token`, `refresh_token`, `access_token_expires_at`. **Do NOT add `refresh_token_expires_at` for Shopee** (TikTok Shop has it; Shopee does not). Save rotated tokens immediately after refresh.

## Order flow (key invariants)
- Statuses: `READY_TO_SHIP`, `PROCESSED`. De-duplicate by `order_sn`.
- `get_order_detail` `response_optional_fields` **must include `package_list`** (required by `_is_ready_to_ship`).
- No new orders → heartbeat, no balance dispatch. New orders > `MAX_ORDERS_PER_RUN` → stop and alert via Telegram.
- For `READY_TO_SHIP`: pre-check `_is_ready_to_ship(order)` BEFORE `v2.logistics.ship_order`. Skip silently (Actions log only, retry next run) when not ready.
- Only when the pre-check passes: `ship_order_to_dropoff(order_sn)` with `dropoff: {}` (= "Atur Pengiriman" → "Antar ke Counter").
- Label flow: `get_shipping_document_parameter` → `get_tracking_number` → `create_shipping_document(tracking_number)` → `download_shipping_document`. If tracking/PDF not ready: skip and retry next run.
- Convert PDF → PNG, merge every 2 PDF pages into 1 vertical image, send via `sendPhoto`.
- Mark `order_sn` processed ONLY after Telegram confirms every image part delivered. Save state immediately after each success.
- After success, record `_pick_balance_sku(item)` for every `order.item_list` entry. After the loop + final save: dispatch `/stock_balance` once with all touched base SKUs in a single `workflow_dispatch`. Heartbeat includes the balance result.

## Critical helpers — module scope in `src/main.py`
- `_is_ready_to_ship(order)`: reads `order.package_list[0].package_number`, calls `shopee_client.get_package_detail(order_sn, package_number)`. Returns `True` ONLY when `response.package_list[0].fulfillment_status == "LOGISTICS_READY"` AND `is_shipment_arranged == false`. Any failure mode → `False`; caller increments `skipped_count` and continues.
- `_pick_balance_sku(item)`: `model_sku` (variant) first, `item_sku` (parent) second. **No `item_name` fallback.** Returns `""` when both empty. Do NOT import `_pick_sku` from `telegram_sender` (its third tier differs).
- `telegram_sender._pick_sku(item)`: variant → parent → `item_name`. Caption only.

## get_package_detail(order_sn, package_number)
GET `/api/v2/order/get_package_detail`. Param name is **`package_number_list`** (CSV string) — pass the single `package_number`. Response: `response.package_list[0].{fulfillment_status, is_shipment_arranged, …}`. Shop-level signed GET. Raises `RuntimeError` on Shopee API error (caught as "not ready").

## Signing
- Partner/auth signature = `partner_id + path + timestamp`. Shop-level = `partner_id + path + timestamp + access_token + shop_id`. HMAC-SHA256 with `partner_key`. `SHOPEE_PARTNER_ID`/`SHOPEE_SHOP_ID` stored as raw strings; `int()` cast only at JSON-body call sites.

## Telegram output
- Bahasa Indonesia. Labels sent as PNG photo(s), not PDFs. First image gets the full caption; later images get "Bagian X/N".
- Caption item lines: `• {qty} x {sku}` — single space, no leading indent. SKU via `_pick_sku`, plus courier.
- Do NOT show recipient name/address (Shopee masks it; the label already contains it).
- Heartbeat uses the plain label `Shopee` (hardcoded; no `SHOPEE_LABEL` constant): `✅ Shopee - 12:00 - 3 label terkirim`, `⚠️ Shopee - 13:00 - 2 terkirim, 1 gagal (akan dicoba lagi)`.
- Append `⚖️ Stock Balance: X/Y SKU dipicu` when balance fired this run.

## balance_dispatcher.py (duplicated across both order bots intentionally)
- `BalanceDispatcher` with `record(sku)` and `dispatch_all()`. `record()`/`to_base_sku()`: strips leading `^\d+PCS-`, uppercases, ignores empty/None, dedupes.
- `dispatch_all()`: a SINGLE `workflow_dispatch` on `furqonajiy/itbisa-shop-stock-bot/balance.yml`, `ref=main`, `sku` = base SKUs space-joined, `dry_run=false`. One HTTP call regardless of count.
- Requires env `STOCK_DISPATCH_TOKEN`. Best-effort: failure logged + reported in heartbeat, **never raised**. Shopee records via `_pick_balance_sku(item)` (success branch only).

## Workflow (run.yml)
- Trigger: `workflow_dispatch` only (manual or Telegram Worker). **No cron/schedule.**
- Checkout `main`; overlay `data/` from `bot-state`; run once; commit state/token files to `bot-state` with `if: always()`.
- Concurrency: group `bot-state-${{ github.repository }}`, `cancel-in-progress: false`. Install `poppler-utils`. Python 3.11. `actions/checkout@v5+`, `actions/setup-python@v6+`. `permissions.contents: write`. Run-step env must include `STOCK_DISPATCH_TOKEN`.

## Secrets
Shopee `partner_id`/`key`/`shop_id`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `STOCK_DISPATCH_TOKEN`.

## Conventions
- GitHub Actions only. `main` = source; `bot-state` = runtime state/token files only (never protect it; never commit live tokens to `main`). Never hardcode secrets. Self-contained repo; `balance_dispatcher.py` duplicated on purpose. Telegram strings Bahasa Indonesia; never abbreviate "TikTok Shop"; use "stock" not "inventory". Runtime ref is `main`.

## Development workflow (process standard)
- Branch from `main` using `feature/<short-description>`.
- Always open a PR into `main` and **merge with a merge commit (`--no-ff`)** — never squash, never fast-forward.
- Commits and PRs are authored as **`C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`** (never "Claude").

## Flag before changing
State/token format, signing, `bot-state`, `workflow_dispatch`-only trigger, `_is_ready_to_ship` semantics + the `package_list` dependency, `get_package_detail` param name / response shape, `_pick_balance_sku` tiers, `balance_dispatcher` batching / best-effort model, label flow, workflow concurrency (`cancel-in-progress: false`), Telegram chat authorization, token rotation.
