# ITBisa Shopee Order Bot

Automatically fetches new Shopee orders, converts shipping labels to images,
and sends them to a Telegram bot for the warehouse employee to print.

## What it does

Every hour from 09:00 to 17:00 Jakarta time, GitHub Actions runs a Python
script that:

1. Asks Shopee for orders ready to ship.
2. Skips any orders already processed in a previous run.
3. For each new order, downloads the shipping label PDF, converts it to a
   PNG image, and sends it to a Telegram chat with a caption describing
   the order.
4. Remembers which orders were processed by committing a small JSON file
   back to this repository.

## Project structure

```
itbisa-shopee-bot/
├── .github/workflows/run.yml    # GitHub Actions cron schedule
├── src/
│   ├── main.py                  # Entry point, orchestrates the flow
│   ├── config.py                # Reads secrets from environment variables
│   ├── shopee_client.py         # Shopee API calls + HMAC signing
│   ├── label_processor.py       # PDF → PNG conversion
│   ├── telegram_sender.py       # Sends image + caption to Telegram
│   └── state_manager.py         # Loads/saves processed_orders.json
├── data/
│   └── processed_orders.json    # State file (committed by the bot)
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

### 1. Create a private GitHub repository and push this code.

### 2. Add the following secrets in **Settings → Secrets and variables → Actions**:

- `SHOPEE_PARTNER_ID`
- `SHOPEE_PARTNER_KEY`
- `SHOPEE_SHOP_ID`
- `SHOPEE_ACCESS_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 3. Enable GitHub Actions.

Go to the **Actions** tab and enable workflows. The cron will start running
on the next scheduled hour. You can also click **Run workflow** to trigger
a run manually.

## Local testing

```bash
# Install dependencies (requires poppler installed on your system)
pip install -r requirements.txt

# Copy .env.example to .env and fill in real values
cp .env.example .env

# Load env vars and run
export $(cat .env | xargs)
python -m src.main
```

## Operations

### How to check if it is working

Open the **Actions** tab on GitHub. Each scheduled run appears as a separate
workflow run. Green checkmark = success. Click into a run to see the
script output, including how many orders were processed.

### What to do if a run fails

Most failures are temporary (Shopee API down, Telegram timeout). The next
scheduled run will retry any order that was not successfully delivered to
Telegram, because we only mark an order as processed AFTER Telegram
confirms delivery.

If failures persist, check:
1. Are the GitHub secrets still valid? (Tokens may have expired.)
2. Is the Shopee API responding? (Try a manual API call with the same credentials.)
3. Is Telegram reachable from GitHub Actions?

### What to do if duplicates appear

Open `data/processed_orders.json` in the repo. Each entry maps an order ID
to the timestamp it was processed. If you need to re-send a label, delete
that order's entry and the next run will reprocess it.

### What to do if the state file gets corrupted

Delete `data/processed_orders.json`. The next run will create a fresh one.
The worst case is that the employee receives duplicate labels for orders
from the last 7 days, which is annoying but not catastrophic.

## Cost

Free. GitHub Actions provides 2,000 free minutes per month for private
repositories. This bot uses roughly 9 runs/day × ~1 minute = 270 minutes
per month, well under the limit.
