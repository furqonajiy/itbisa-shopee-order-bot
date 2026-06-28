# ITBisa Shopee Order Bot

Automatically fetches new Shopee orders, converts shipping labels to Telegram-ready images,
and sends them to a Telegram bot so the warehouse employee can print them
from their phone without any manual downloading.

## What it does

GitHub Actions runs the bot on demand via `workflow_dispatch` only (there is no
cron schedule). It is triggered manually from the Actions tab or by the Telegram
Worker.

Each run:

1. Checks out `main`, then overlays runtime files from the `bot-state` branch.
2. Asks Shopee for orders in `READY_TO_SHIP` or `PROCESSED` status.
3. Skips any orders already processed in a previous run.
4. For each new order:
    - If still `READY_TO_SHIP`, pre-checks the package with `get_package_detail`
      first. Only when `fulfillment_status` is `LOGISTICS_READY` and
      `is_shipment_arranged` is `false` does it call Shopee's `ship_order` API
      with dropoff method (equivalent to clicking "Atur Pengiriman" → "Antar ke
      Counter" in the Shopee Seller app). This pre-check protects the
      `v2.logistics.ship_order` API success rate by skipping packages that are
      still allocating or already arranged. After `ship_order`, the order moves
      to `PROCESSED` and label generation starts.
    - Gets Shopee's suggested shipping document type.
    - Gets the tracking number.
    - Calls `create_shipping_document` with the document type and tracking number.
    - Downloads the shipping label PDF, converts all pages to PNG images, merges
      every 2 PDF pages into 1 Telegram image, and sends the image(s) to a
      Telegram chat with a caption in Bahasa Indonesia.
    - Marks the `order_sn` as processed only after Telegram confirms delivery,
      then records each shipped variant SKU for the post-run balance dispatch.
5. After the order loop, dispatches the stock bot's `/stock_balance` once with
   all touched base SKUs (single `workflow_dispatch`; best-effort, never fatal).
   The dispatch is throttled to at most once per hour
   (`balance_throttle.MIN_INTERVAL_HOURS`); SKUs touched while the window is
   closed accumulate in `data/balance_throttle.json` and flush together when it
   reopens, so no SKU is ever dropped (`/stock_balance` is idempotent).
6. Sends a heartbeat summary at the end of every run so the employee knows
   the bot is alive, even when no new orders came in. The heartbeat appends
   `⚖️ Stock Balance: X/Y SKU dipicu` when a balance was dispatched, or
   `⏳ Stock Balance: N SKU menunggu` when the dispatch was deferred by the
   throttle.
7. Writes refreshed tokens and processed-order state locally during the run,
   then the workflow commits the `data/` files back to `bot-state`.

To save GitHub Actions minutes, the workflow runs a cheap precheck first
(`python -m src.main --precheck`, no poppler) that detects whether there are any
new orders. When there are none it sends the heartbeat itself and the poppler
install plus the full run are skipped; on work, error, or uncertainty it falls
back to the full run.

Everything runs on the GitHub Actions free tier. There is no server, no
cloud VM, and no database.

## Project structure

```text
itbisa-shopee-order-bot/
├── .github/workflows/
│   ├── run.yml                      # GitHub Actions workflow_dispatch (manual / Telegram Worker)
│   └── ci.yml                       # PR quality gate: runs pytest, no secrets, never touches bot-state
├── data/                            # Runtime state, source of truth is bot-state
│   ├── processed_orders.json        # order_sn values already sent to Telegram
│   ├── shopee_tokens.json           # Current access + refresh tokens
│   └── balance_throttle.json        # Balance dispatch throttle: last dispatch time + pending SKUs
├── scripts/
│   ├── bootstrap_tokens.py          # One-time script to seed shopee_tokens.json
│   ├── cleanup_branches.py          # Repo maintenance: delete merged/AI-named branches (dry-run by default)
│   └── test_telegram.py             # Diagnostic Telegram send
├── src/
│   ├── __init__.py
│   ├── main.py                      # Entry point, orchestrates one run
│   ├── config.py                    # Reads secrets + settings from env
│   ├── shopee_auth.py               # Shopee token lifecycle + rotation save
│   ├── shopee_client.py             # Shopee API calls + HMAC signing
│   ├── label_processor.py           # PDF → Telegram PNGs, 2 pages per image
│   ├── telegram_sender.py           # Sends images + summaries in Bahasa
│   ├── state_manager.py             # Loads/saves processed_orders.json
│   ├── balance_dispatcher.py        # Dispatches /stock_balance once after the run
│   └── balance_throttle.py          # Throttles balance dispatch + holds pending SKUs
├── tests/                           # pytest unit tests (pure logic only)
├── requirements.txt
├── requirements-dev.txt             # Adds pytest for CI / local test runs
├── pytest.ini
├── conftest.py
├── .env.example
└── README.md
```

## Tests

Pure logic is unit-tested with pytest (`balance_dispatcher`, `balance_throttle`,
and the `telegram_sender` caption helpers). Network/API calls and the label flow
are not unit-tested. Install dev deps and run:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

`ci.yml` runs the same suite on every PR that touches `src/`, `tests/`,
`requirements*.txt`, `pytest.ini`, `conftest.py`, or the CI workflow itself.

## Requirements

- Python 3.11 (other versions may work but 3.11 matches production).
- `poppler` installed on your system for PDF rendering.
- A Shopee Open Platform app.
- A Telegram bot and chat ID.

## Initial setup

### 1. Clone the repo and install dependencies

Open Anaconda Prompt and run:

```bash
conda create -n itbisa_order_bot python=3.11
conda activate itbisa_order_bot
conda install -c conda-forge poppler
cd C:\path\to\itbisa-shopee-order-bot
python -m pip install -r requirements.txt
```

### 2. Configure credentials

Copy `.env.example` to `.env` and fill in your values:

```env
SHOPEE_PARTNER_ID=1234567
SHOPEE_PARTNER_KEY=your_partner_key_here
SHOPEE_SHOP_ID=987654321

TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=-100123456789
```

Shopee tokens are managed in `data/shopee_tokens.json` because they must be
refreshed automatically. See the authentication section below.

### 3. Confirm the Shopee environment

Open `src/config.py` and check `SHOPEE_API_BASE_URL`.

The current production URL is:

```text
https://partner.shopeemobile.com
```

Tokens are environment-specific. A sandbox token does not work against the
live URL, and a live token does not work against the sandbox URL. If you ever
change this URL, you must also re-run the bootstrap script to get fresh tokens
for the new environment.

### 4. Bootstrap the Shopee tokens (one-time)

This step gets your initial access token and refresh token from Shopee.

1. Log in to Shopee Open Platform Console.
2. Open your app and click **Authorize**.
3. After the redirect, copy the `code` value from the URL bar. The URL
   looks like `https://...?code=ABC123...&shop_id=XXXXX`. Copy just the
   `code` value. It expires quickly and can only be used once.
4. From the project root, run:

   ```bash
   python scripts/bootstrap_tokens.py
   ```

5. Paste the code when prompted. The script writes `data/shopee_tokens.json`
   with a valid access token plus refresh token pair.

You only need to run this script in three situations: the very first setup,
when switching between sandbox and live environments, or if the refresh token
expires.

## Running locally

Local runs call the real Shopee API and send real Telegram messages.

Make sure `.env` is filled in and `data/shopee_tokens.json` exists, then run:

```bash
python -m src.main
```

The bot queries your Shopee shop and processes actual orders based on the
configured `SHOPEE_API_BASE_URL`.

## Production deployment (GitHub Actions)

### 1. Push code to GitHub

Push your repository to GitHub. Make sure the repo is private because it
contains shop configuration and runtime state files.

### 2. Add secrets in repository settings

Go to **Settings → Secrets and variables → Actions** and add these six
secrets:

- `SHOPEE_PARTNER_ID`
- `SHOPEE_PARTNER_KEY`
- `SHOPEE_SHOP_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `STOCK_DISPATCH_TOKEN` (PAT used to dispatch the stock bot's `/stock_balance`)

### 3. Push the initial tokens file

The tokens file you bootstrapped locally needs to be available for the first
workflow run. Push it to `main` together with an empty processed-orders file:

```bash
git add data/shopee_tokens.json data/processed_orders.json
git commit -m "Bootstrap initial state files"
git push
```

After the first workflow run reaches the state-commit step, ongoing runtime
updates are pushed to the `bot-state` branch. You may keep the initial files on
`main` for bootstrap, but the `bot-state` branch should be treated as the source
of truth for runtime state.

### 4. Verify the workflow runs

Go to the **Actions** tab, click **Run Shopee Order Bot** in the sidebar,
then click **Run workflow** to trigger a manual test run. Watch the logs to
confirm everything works. You should see a heartbeat message arrive in your
Telegram chat within a minute.

The bot runs only when dispatched (manually or by the Telegram Worker); there is
no automatic schedule.

## State management (the bot-state branch)

This repo uses two branches:

- **main** holds the source code. It can be protected by branch rules so code
  changes go through pull requests.
- **bot-state** holds the runtime state files (`processed_orders.json`,
  `shopee_tokens.json`, and `balance_throttle.json`). It is updated
  automatically by the bot workflow, with no PR required.

This separation matters because the bot needs to write state files constantly
(every time it processes an order or refreshes a token), but those writes are
not code changes that warrant code review. Putting them on a separate branch
keeps `main` clean and lets branch protection rules work as intended.

The workflow always starts from source code on `main`, then overlays only the
`data/` files from `bot-state` before running the bot. After the bot finishes,
the workflow commits `data/processed_orders.json`, `data/shopee_tokens.json`,
and `data/balance_throttle.json` back to `bot-state`. The commit step runs with
`if: always()` so a token refresh
or partial processed-order progress can still be preserved even if a later order
fails.

The `bot-state` branch is created automatically the first time the workflow
reaches the state-commit step. For the very first run, the workflow still
expects the initial state files to be available from `main`.

### Bootstrapping for the first time

When you initially set up the bot:

1. Run `python scripts/bootstrap_tokens.py` locally to create
   `data/shopee_tokens.json`.
2. Push your code to `main` with `data/processed_orders.json` as `{}` and
   `data/shopee_tokens.json` from the bootstrap.
3. Trigger the workflow manually from the Actions tab.
4. The first run will create or update the `bot-state` branch and push the
   state files there. From then on, runtime updates go to `bot-state`.

### What you should NOT do

- Do not delete the `bot-state` branch unless you want to lose the bot's
  memory of which orders were already processed and which token is current.
- Do not enable branch protection on `bot-state`. It is supposed to be
  bot-writable.
- Do not manually edit files on `bot-state` unless you are recovering from a
  problem. Any manual edit is at risk of being overwritten by the next
  scheduled run.

## How authentication works

Shopee uses an OAuth-style flow with three credentials working together:

- **Authorization code:** single-use ticket you get by clicking Authorize in
  the Shopee Console. Used only during bootstrap.
- **Access token:** what the bot attaches to every API call. Refreshed
  automatically by the bot.
- **Refresh token:** long-lived credential used to obtain new access tokens.
  Shopee may rotate it during refresh, so the bot saves rotated tokens
  immediately.

The Shopee token file intentionally stores only:

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "access_token_expires_at": "2026-04-19T14:00:00+00:00"
}
```

Do not add `refresh_token_expires_at`; this Shopee bot does not use it.

When the access token expires while the bot is not running, nothing bad
happens. The next run sees the stale token, calls the refresh endpoint, saves
the rotated tokens immediately, and then continues with the order flow.

The only time a human must intervene is if the refresh token itself expires or
is revoked. When this happens, the bot sends a Telegram alert asking you to
re-run the bootstrap script.

## Triggering

The workflow is `workflow_dispatch`-only — there is **no cron schedule**. It runs
when triggered manually from the Actions tab or dispatched by the Telegram Worker.
Treat `.github/workflows/run.yml` as the source of truth.

## What your employee sees in Telegram

For each new order, one or more label images arrive with a caption like:

```text
📦 240418ABC123
🚚 SPX Express

Barang:
  • 20 x ITBISA-LED-5MM-RED
  • 15 x ITBISA-LED-5MM-GREEN
```

Multi-page labels are grouped before sending:

- 1 PDF page → 1 Telegram image
- 2 PDF pages → 1 merged Telegram image
- 3 PDF pages → 2 Telegram images: pages 1-2 merged, page 3 alone
- 4 PDF pages → 2 merged Telegram images

If an order produces more than one Telegram image, the first image contains the
full order caption plus `Bagian 1/N`. The following images only show their
`Bagian X/N` label so the chat stays readable.

The caption uses SKU instead of the product name because product names on
Shopee are very long, while SKUs are short and match how the warehouse is
organized. When a product has variants, the variant SKU is shown. When a
product has no variants, the main product SKU is shown.

At the end of every run, a short heartbeat message appears:

- `✅ Shopee - 11:00 - Tidak ada pesanan baru`
- `✅ Shopee - 12:00 - 3 label terkirim`
- `⚠️ Shopee - 13:00 - 2 terkirim, 1 gagal (akan dicoba lagi)`

When labels are shipped, the heartbeat appends a balance-dispatch line:
`⚖️ Stock Balance: X/Y SKU dipicu` when the balance fired, or
`⏳ Stock Balance: N SKU menunggu` when the dispatch was deferred by the
once-per-hour throttle (the SKUs are held in `data/balance_throttle.json` and
flush on the next eligible run).

If the refresh token expires, a rare manual-intervention alert appears:

```text
🔐 14:00 - Otorisasi Shopee kadaluarsa. Mohon otorisasi ulang aplikasi
di Shopee Open Platform Console, lalu update file data/shopee_tokens.json
dengan token baru.
```

## Troubleshooting

### 403 Forbidden from Shopee

This almost always means one of two things. Either the access token in
`data/shopee_tokens.json` is from the wrong environment, or the bootstrap has
not been done for the current environment yet.

To fix: make sure `SHOPEE_API_BASE_URL` in `src/config.py` matches the
environment you want, and re-run `python scripts/bootstrap_tokens.py` to get
tokens for that environment.

### invalid_code during bootstrap

The authorization code you pasted has expired, was already used, or was from
the wrong environment. Each code is short-lived and can only be used once. Go
back to Shopee Console, click Authorize again to generate a fresh code, and run
the bootstrap script immediately.

### Telegram message not appearing

Check that `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are correct. Also
confirm your employee has started a conversation with the bot. Telegram bots
cannot send messages to users who have not initiated contact. For group chats,
make sure the bot is added to the group and that the chat ID has the `-100`
prefix.

### Duplicate labels appearing

Open `data/processed_orders.json` on the `bot-state` branch. The file maps each
processed `order_sn` to the timestamp when it was sent. If you want to re-send a
specific recent label, delete that order's entry and the next run will reprocess
it if the order is still inside the 3-day Shopee lookup window.

### State file corrupted

Recover `data/processed_orders.json` on the `bot-state` branch. If you delete it
entirely, the next run will create a fresh one. Worst case, the employee receives
duplicate labels for recent orders still inside the 3-day lookup window, which
is annoying but not catastrophic.

## Switching from sandbox to live

When your Shopee app is approved for Go Live:

1. Update `SHOPEE_API_BASE_URL` in `src/config.py` to the live URL.
2. Update the GitHub Secrets with your live partner ID, partner key, and
   shop ID.
3. Run `python scripts/bootstrap_tokens.py` against the live environment to get
   live tokens.
4. Commit the new `data/shopee_tokens.json` and push.
5. Manually trigger a test run from the Actions tab to verify.

These steps always go together. Forgetting any one of them causes a 403 error
because the tokens file environment does not match the URL.

## Cost

Free forever.

- GitHub Actions: 2000 free minutes per month on private repos. Each run is
  ~1 minute; total depends on how often the bot is dispatched.
- Telegram Bot API: free.
- Shopee Open API: free, subject to Shopee's normal rate limits.

## A note on premature optimization

This codebase deliberately avoids patterns that are not currently needed: no
dependency injection, no abstract base classes, no utils folder, and no
multi-marketplace abstraction. Those additions should happen when real friction
appears, not before.

Shopee and TikTok Shop order bots should stay self-contained unless there is a
clear reason to extract a shared library.
