# ITBisa Shopee Order Bot

Automatically fetches new Shopee orders, converts shipping labels to images,
and sends them to a Telegram bot so the warehouse employee can print them
from their phone without any manual downloading.

## What it does

Every hour from 09:00 to 17:00 Jakarta time, GitHub Actions runs a Python
script that:

1. Asks Shopee for orders in `READY_TO_SHIP` or `PROCESSED` status.
2. Skips any orders already processed in a previous run.
3. For each new order:
   - If still `READY_TO_SHIP`, calls Shopee's `ship_order` API with
     dropoff method (equivalent to clicking "Atur Pengiriman" → "Antar ke
     Counter" in the Shopee Seller app). This moves the order to
     `PROCESSED` and triggers label generation.
   - Downloads the shipping label PDF, converts it to a PNG image, and
     sends it to a Telegram chat with a caption describing the order in
     Bahasa Indonesia.
4. Sends a heartbeat summary at the end of every run so the employee knows
   the bot is alive, even when no new orders came in.
5. Remembers what was processed by committing small JSON files back to the
   repository.

Everything runs on the GitHub Actions free tier. There is no server, no
cloud VM, and no database.

## Project structure

```text
itbisa-shopee-bot/
├── .github/workflows/
│   └── run.yml                      # GitHub Actions cron schedule
├── data/                            # Runtime state, lives on bot-state branch (not main)
│   ├── processed_orders.json        # Which orders we already sent to Telegram
│   └── shopee_tokens.json           # Current access + refresh tokens (created by bootstrap)
├── scripts/
│   ├── bootstrap_tokens.py          # One-time script to seed shopee_tokens.json
│   ├── update_inventory.py          # Utility to update Shopee stock from Excel
│   └── test_*.py                    # Helper / diagnostic scripts for API testing
├── src/
│   ├── __init__.py
│   ├── main.py                      # Entry point, orchestrates the flow
│   ├── config.py                    # Reads secrets + settings from env
│   ├── shopee_auth.py               # Token lifecycle (refresh every 4h)
│   ├── shopee_client.py             # Shopee API calls + HMAC signing
│   ├── shopee_client_fake.py        # Canned fake data for local testing
│   ├── label_processor.py           # PDF → PNG conversion
│   ├── telegram_sender.py           # Sends images + messages in Bahasa
│   └── state_manager.py             # Loads/saves processed_orders.json
├── requirements.txt
├── .env.example
└── README.md
```

## Requirements

- Python 3.11 (other versions may work but 3.11 matches production).
- `poppler` installed on your system for PDF rendering.
- A Shopee Open Platform app (sandbox for testing, live for production).
- A Telegram bot and chat ID.

## Initial setup

### 1. Clone the repo and install dependencies

Open Anaconda Prompt and run:

```bash
conda create -n itbisa_order_bot python=3.11
conda activate itbisa_order_bot
conda install -c conda-forge poppler
cd C:\path\to\itbisa-shopee-bot
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

USE_FAKE_SHOPEE=false
```

Shopee tokens are managed in `data/shopee_tokens.json` because they must be
refreshed automatically. See the authentication section below.

### 3. Point config.py at the right environment

Open `src/config.py` and make sure `SHOPEE_API_BASE_URL` matches the
environment you are targeting:

- Sandbox: `https://openplatform.sandbox.test-stable.shopee.sg`
- Live: `https://partner.shopeemobile.com`

Tokens are environment-specific. A sandbox token does not work against
live, and vice versa. If you change this URL, you must also re-run the
bootstrap script to get fresh tokens for the new environment.

### 4. Bootstrap the Shopee tokens (one-time)

This step gets your initial access token and refresh token from Shopee.

1. Log in to Shopee Open Platform Console.
2. Open your app and click **Authorize**.
3. After the redirect, copy the `code` value from the URL bar. The URL
   looks like `https://...?code=ABC123...&shop_id=XXXXX`. Copy just the
   `code` value. It expires in about 10 minutes and can only be used once.
4. From the project root, run:

   ```bash
   python scripts/bootstrap_tokens.py
   ```

5. Paste the code when prompted. The script writes `data/shopee_tokens.json`
   with a valid access token plus refresh token pair.

You only need to run this script in three situations: the very first setup,
when switching between sandbox and live environments, or if the refresh
token expires (every 30 days of inactivity).

## Running locally

### Test with fake data (no real Shopee needed)

Set `USE_FAKE_SHOPEE=true` in your `.env` file and run:

```bash
python -m src.main
```

The bot returns 3 canned fake orders matching a real Shopee response
format, generates dummy PDFs with `reportlab`, converts them to PNG,
and sends them to your Telegram bot. Useful for testing the full
pipeline without needing a working Shopee connection.

### Test with real Shopee (requires bootstrap done)

Set `USE_FAKE_SHOPEE=false` and run:

```bash
python -m src.main
```

The bot queries your real Shopee shop (sandbox or live depending on
`SHOPEE_API_BASE_URL`) and processes actual orders.

## Production deployment (GitHub Actions)

### 1. Push code to GitHub

Push your repository to GitHub. Make sure the repo is private because
it contains your shop configuration and the committed tokens file.

### 2. Add secrets in repository settings

Go to **Settings → Secrets and variables → Actions** and add these five
secrets. Do NOT add `USE_FAKE_SHOPEE`, since it should default to false
in production.

- `SHOPEE_PARTNER_ID`
- `SHOPEE_PARTNER_KEY`
- `SHOPEE_SHOP_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 3. Push the initial tokens file

The tokens file you bootstrapped locally needs to be on the repo so the
first workflow run can read it. Push it to main:

```bash
git add data/shopee_tokens.json data/processed_orders.json
git commit -m "Bootstrap initial state files"
git push
```

After the first successful workflow run, ongoing state updates are pushed
to the `bot-state` branch. You may keep the initial files on `main` for
bootstrap, but the `bot-state` branch should be treated as the source of
truth for runtime state.

### 4. Verify the workflow runs

Go to the **Actions** tab, click **Run Shopee Order Bot** in the sidebar,
then click **Run workflow** to trigger a manual test run. Watch the logs
to confirm everything works. You should see a heartbeat message arrive
in your Telegram chat within a minute.

After the first successful manual run, the cron takes over and runs the
bot automatically on schedule.

## State management (the bot-state branch)

This repo uses two branches:

- **main** holds the source code. It is protected by branch rules so all
  changes go through pull requests.
- **bot-state** holds the runtime state files (`processed_orders.json` and
  `shopee_tokens.json`). It is updated automatically by the bot on every
  successful run, with no PR required.

This separation matters because the bot needs to write state files
constantly (every time it processes an order or refreshes a token), but
those writes are not "code changes" that warrant code review. Putting them
on a separate branch keeps `main` clean and lets your branch protection
rules work as intended.

The bot-state branch is created automatically the first time the workflow
runs successfully. For the very first run, the workflow still expects the
initial state files to be available from `main`.

### Bootstrapping for the first time

When you initially set up the bot:

1. Run `python scripts/bootstrap_tokens.py` locally to create
   `data/shopee_tokens.json`.
2. Push your code to `main` (with `data/processed_orders.json` as `{}`
   and `data/shopee_tokens.json` from the bootstrap).
3. Trigger the workflow manually from the Actions tab.
4. The first run will create or update the `bot-state` branch and push the state
   files there. From then on, runtime updates go to `bot-state`.

### What you should NOT do

- Do not delete the `bot-state` branch unless you want to lose the bot's
  memory of which orders were already processed (it will resend recent
  labels) and which token is current (you will need to re-bootstrap).
- Do not enable branch protection on `bot-state`. It is supposed to be
  bot-writable.
- Do not manually edit files on `bot-state` unless you are recovering
  from a problem. Any manual edit is at risk of being overwritten by the
  next scheduled run.

## How authentication works

Shopee uses an OAuth-style flow with three credentials working together:

- **Authorization code:** single-use ticket you get by clicking Authorize
  in the Shopee Console. Valid for ~10 minutes. Used only during bootstrap.
- **Access token:** what the bot attaches to every API call. Valid for
  4 hours. Refreshed automatically by the bot.
- **Refresh token:** long-lived credential used to obtain new access
  tokens. Valid for 30 days. Replaced by a new refresh token on every
  refresh, which resets the 30-day clock.

As long as the bot runs at least once every 30 days, the refresh token
chain continues indefinitely. The bot running 9 times per day means
refreshes happen multiple times per day, so the 30-day limit is
never hit in normal operation.

When the access token expires overnight (while the bot is asleep), nothing
bad happens. The next run simply sees the stale token, calls the
refresh endpoint, and gets a fresh one before doing any real work.

The only time a human must intervene is if the refresh token itself
expires, which would only happen if the bot stopped running completely
for more than 30 days. When this happens, the bot sends a Telegram alert
asking you to re-run the bootstrap script.

## Daily schedule

The cron currently runs 9 times per day during Jakarta business hours:

- **09:00 WIB** — overnight sweep (catches orders placed while asleep)
- **10:00–16:00 WIB** — 7 hourly runs during active selling window
- **17:00 WIB** — final sweep before courier pickup

In cron syntax (GitHub Actions uses UTC), this is `0 2-10 * * *`.

## What your employee sees in Telegram

For each new order, a label image arrives with a caption like:

```text
📦 240418ABC123
🚚 SPX Express

Barang:
  • 20 x ITBISA-LED-5MM-RED
  • 15 x ITBISA-LED-5MM-GREEN
```

The caption uses SKU instead of the product name because product names
on Shopee are very long (often 100+ characters), while SKUs are short
and match how the warehouse is organized. When a product has variants
(like different colors), the variant SKU is shown. When a product has
no variants, the main product SKU is shown.

At the end of every run, a short heartbeat message appears:

- `✅ 11:00 - Tidak ada pesanan baru` (quiet hour)
- `✅ 12:00 - 3 label terkirim` (normal hour)
- `⚠️ 13:00 - 2 terkirim, 1 gagal (akan dicoba lagi)` (problem hour)

If the refresh token expires, a rare manual-intervention alert appears:

```text
🔐 14:00 - Otorisasi Shopee kadaluarsa. Mohon otorisasi ulang aplikasi
di Shopee Open Platform Console, lalu update file data/shopee_tokens.json
dengan token baru.
```

## Troubleshooting

### 403 Forbidden from Shopee

This almost always means one of two things. Either the access token in
`data/shopee_tokens.json` is from the wrong environment (sandbox token
being used against live URL, or vice versa), or the bootstrap has not
been done for the current environment yet.

To fix: make sure `SHOPEE_API_BASE_URL` in `src/config.py` matches the
environment you want, and re-run `python scripts/bootstrap_tokens.py`
to get tokens for that environment.

### invalid_code during bootstrap

The authorization code you pasted has expired, was already used, or was
from the wrong environment. Each code is valid for only ~10 minutes and
can only be used once. Go back to Shopee Console, click Authorize again
to generate a fresh code, and run the bootstrap script immediately.

### Telegram message not appearing

Check that `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are correct. Also
confirm your employee has actually started a conversation with the bot
(Telegram bots cannot send messages to users who have not initiated
contact). For group chats, make sure the bot is added to the group and
that the chat ID has the `-100` prefix.

### Workflow disabled after long inactivity

GitHub Actions disables scheduled workflows on repos with no activity
for 60 days. Since the bot commits state on every run, this should not
happen in normal operation. If it does, go to the Actions tab and
click "Enable workflow" to re-activate it.

### Duplicate labels appearing

Open `data/processed_orders.json`. The file maps each processed order
ID to the timestamp when it was sent. If you want to re-send a specific
label, delete that order's entry and the next run will reprocess it.

### State file corrupted

Delete `data/processed_orders.json` entirely. The next run will create
a fresh one. Worst case, the employee receives duplicate labels for
orders from the last 7 days, which is annoying but not catastrophic.

## Switching from sandbox to live

When your Shopee app is approved for Go Live:

1. Update `SHOPEE_API_BASE_URL` in `src/config.py` to the live URL.
2. Update the five GitHub Secrets with your live partner ID, partner key,
   and shop ID.
3. Run `python scripts/bootstrap_tokens.py` against the live environment
   to get live tokens.
4. Commit the new `data/shopee_tokens.json` and push.
5. Manually trigger a test run from the Actions tab to verify.

These steps always go together. Forgetting any one of them causes a
403 error because the tokens file environment does not match the URL.

## Cost

Free forever.

- GitHub Actions: 2000 free minutes per month on private repos. The bot
  uses roughly 9 runs/day × ~1 minute = 270 minutes/month.
- Telegram Bot API: free, no limits.
- Shopee Open API: free, generous rate limits.

## A note on premature optimization

This codebase deliberately avoids patterns that would be "nice to have"
but are not currently needed: no dependency injection, no abstract base
classes, no utils folder, no multi-marketplace abstraction. Those
additions should happen when real friction appears, not before.

If you later add a second marketplace like Tokopedia, the right move is
to copy the whole `src/` folder into a parallel structure and get it
working independently first, then extract shared patterns once both are
running. Abstractions designed for two concrete implementations are
much better than abstractions designed for one implementation and one
imagined future.
