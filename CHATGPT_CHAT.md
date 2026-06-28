# itbisa-shopee-order-bot — ChatGPT Chat guide

Condensed `CLAUDE.md` for ChatGPT Chat (≤ 8000 chars); `CLAUDE.md` is the full source of truth. Python bot: fetch Shopee orders → ship to dropoff → send waybill labels to Telegram → dispatch stock balance once. Runs once per invocation, then exits. GitHub Actions only: no server, DB, or long-running process. **Track unit: `order_sn`.**

## Stack & files (Python 3.11)
`src/`: `main.py` (orchestration), `shopee_client.py`, `shopee_auth.py`, `label_processor.py`, `telegram_sender.py`, `state_manager.py`, `balance_dispatcher.py`, `balance_throttle.py`. `scripts/`: `bootstrap_tokens.py`, `test_telegram.py`, `cleanup_branches.py`. Workflows: `run.yml` (execution); `ci.yml` (PR `pytest` quality gate, no secrets).

## Constants
`TOKEN_REFRESH_BUFFER_MINUTES = 10`, `STATE_RETENTION_DAYS = 3`, `MAX_ORDERS_PER_RUN = 30`, `LABEL_IMAGE_DPI = 200`, `MIN_INTERVAL_HOURS = 1` (balance throttle). `SHOPEE_API_BASE_URL = https://partner.shopeemobile.com`.

## State / tokens (committed to bot-state)
- `data/processed_orders.json`, `data/shopee_tokens.json`, `data/balance_throttle.json`. Token fields: `access_token`, `refresh_token`, `access_token_expires_at`. **Do NOT add `refresh_token_expires_at` for Shopee** (TikTok Shop has it; Shopee does not). Save rotated tokens immediately after refresh.
- `main` = source; `bot-state` = runtime state/token files only — never protect it; never commit live tokens to `main`.

## Order flow (key invariants)
- Statuses `READY_TO_SHIP`, `PROCESSED`; dedupe by `order_sn`. `get_order_detail` `response_optional_fields` **must include `package_list`** (required by `_is_ready_to_ship`).
- No new orders → heartbeat, no balance dispatch. New orders > `MAX_ORDERS_PER_RUN` → stop + alert via Telegram.
- For `READY_TO_SHIP`: pre-check `_is_ready_to_ship(order)` BEFORE `v2.logistics.ship_order`; skip silently (retry next run) when not ready. Only when it passes: `ship_order_to_dropoff(order_sn)` with `dropoff: {}` ("Atur Pengiriman" → "Antar ke Counter").
- Label flow: `get_shipping_document_parameter` → `get_tracking_number` → `create_shipping_document(tracking_number)` → `download_shipping_document`. Tracking/PDF not ready → skip + retry.
- Convert PDF → PNG, merge every 2 PDF pages into 1 vertical image, send via `sendPhoto`. Mark `order_sn` processed ONLY after Telegram confirms every image part delivered; save state immediately after each success; then record `_pick_balance_sku(item)` for every `order.item_list` entry. After the loop + final save: dispatch `/stock_balance` once with all touched base SKUs in a single `workflow_dispatch`. Heartbeat includes the balance result.

## Critical helpers — module scope in `src/main.py`
- `_is_ready_to_ship(order)`: reads `order.package_list[0].package_number`, calls `get_package_detail`. Returns `True` ONLY when `response.package_list[0].fulfillment_status == "LOGISTICS_READY"` AND `is_shipment_arranged == false`. Any failure mode → `False`; caller increments `skipped_count` and continues. Cost: +1 GET per `READY_TO_SHIP` order per run.
- `_pick_balance_sku(item)`: `model_sku` (variant) first, `item_sku` (parent) second. **No `item_name` fallback** (never a valid stock-bot catalog key); `""` when both empty. Do NOT import `telegram_sender._pick_sku` — its third tier differs.
- `telegram_sender._pick_sku(item)`: variant → parent → `item_name`. Caption-only.

## get_package_detail(order_sn, package_number)
GET `/api/v2/order/get_package_detail`. Param name is **`package_number_list`** (CSV string) — pass the single `package_number`. Response: `response.package_list[0].{fulfillment_status, is_shipment_arranged, …}`. Shop-level signed GET. Raises `RuntimeError` on API error (caught as "not ready").

## Signing
- Partner/auth = `partner_id + path + timestamp`; shop-level = `+ access_token + shop_id`. HMAC-SHA256 with `partner_key`. `SHOPEE_PARTNER_ID`/`SHOPEE_SHOP_ID` stored as raw strings; `int()` cast only at JSON-body call sites.

## Telegram output
- Bahasa Indonesia. Labels sent as PNG photo(s), not PDFs. First image gets the full caption; later images get "Bagian X/N".
- Caption item lines: `• {qty} x {sku}` — single space, no leading indent. SKU via `_pick_sku`, plus courier. Sent with `parse_mode=Markdown`; order number, courier, SKU wrapped in backtick code spans (`_mono`, strips backticks) so they are tap-to-copy. Do NOT show recipient name/address (Shopee masks it; the label already contains it).
- Heartbeat uses the plain label `Shopee` (hardcoded in `build_summary`; no `SHOPEE_LABEL` constant): e.g. `✅ Shopee - 12:00 - 3 label terkirim`. Append `⚖️ Stock Balance: X/Y SKU dipicu` when balance fired. Use "stock" not "inventory"; never abbreviate "TikTok Shop".

## balance_dispatcher.py — duplicated across both order bots intentionally
- `BalanceDispatcher` with `record(sku)`, `collected()`, `dispatch_all()`. `record()`/`to_base_sku()`: strips leading `^\d+PCS-`, uppercases, ignores empty/None, dedupes via internal set.
- `dispatch_all()`: fires a SINGLE `workflow_dispatch` on `furqonajiy/itbisa-shop-stock-bot/balance.yml`, `ref=main`, `sku` = collected base SKUs space-joined, `dry_run=false`. One HTTP call regardless of count. Requires env `STOCK_DISPATCH_TOKEN`; if missing, all SKUs reported failed and the run still finishes. Returns `{requested, dispatched, failed, skus}`. Best-effort: failure logged + in heartbeat, **never raised**. Shopee records via `_pick_balance_sku(item)`, never raw `item_sku`; `record()` only in the success branch. Don't factor out the duplicate.
- **Throttle (`balance_throttle.py`, duplicated):** `MIN_INTERVAL_HOURS = 1` — at most one dispatch per hour (set 0 for immediate rebalance every run). SKUs touched while withheld (window closed / failed dispatch) accumulate in `pending_skus` (`data/balance_throttle.json` on `bot-state`) and flush together when the window reopens — no SKU dropped (`/stock_balance` is idempotent). `_run_throttled_balance` orchestrates; heartbeat shows `⏳ Stock Balance: N SKU menunggu`.

## Workflow (run.yml)
`workflow_dispatch` only (manual or Telegram Worker); no schedule/cron. Checkout `main`; overlay `data/` from `bot-state`; run once; commit state/token files (`processed_orders.json`, `shopee_tokens.json`, `balance_throttle.json`) to `bot-state` with `if: always()`. Concurrency `bot-state-${{ github.repository }}`, `cancel-in-progress: false`; `timeout-minutes: 10`. Idle efficiency: `id: precheck` runs `python -m src.main --precheck` (no poppler) — emits `has_work=false` only on a clean zero-new-orders result (sends the heartbeat + saves state itself), else `true`; the Install poppler + Run steps are gated `if: steps.precheck.outputs.has_work != 'false'` (fail-safe to running). Install `poppler-utils` only when there is work; Python 3.11 pip-cached; `checkout@v5+`, `setup-python@v6+`; `permissions.contents: write`; run-step env must include `STOCK_DISPATCH_TOKEN`.

## Secrets
Shopee `partner_id`/`key`/`shop_id`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `STOCK_DISPATCH_TOKEN`. Never hardcode.

## Workflow & identity (process standard)
- Author commits/PRs as `C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`. **No AI references** anywhere — no Co-Authored-By, no "Generated by", no session links. Branch `feature/<desc>` off `main`; PR into `main`; merge with a merge commit (`--no-ff`); merge title ends with `(#PR)`. Docs + marker ride in the same PR. Maintainer is on Windows — give CLI commands in PowerShell.

## Flag before changing
State/token format, signing, `bot-state`, `workflow_dispatch`-only trigger, `_is_ready_to_ship` + `package_list` in `response_optional_fields`, `get_package_detail` param/response shape, `_pick_balance_sku` tiers, `balance_dispatcher`/`balance_throttle` batching + best-effort model, label flow, workflow concurrency (`cancel-in-progress: false`), Telegram chat authorization, token rotation.
