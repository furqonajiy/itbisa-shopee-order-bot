"""
config.py
---------
Reads all secrets and settings from environment variables.

Why this file exists:
  We never hardcode secrets in the code. Instead, GitHub Actions injects them
  as environment variables at runtime, and this file is the ONLY place that
  reads them. Every other module gets these values by importing from here.

If you are running locally for testing, you can put these in a .env file
and load them with `export $(cat .env | xargs)` before running.
"""

import os


# STEP 1: Read Shopee API credentials.
# You get these from the Shopee Open Platform when you register your app.
SHOPEE_PARTNER_ID = int(os.environ["SHOPEE_PARTNER_ID"])
SHOPEE_PARTNER_KEY = os.environ["SHOPEE_PARTNER_KEY"]
SHOPEE_SHOP_ID = int(os.environ["SHOPEE_SHOP_ID"])
SHOPEE_ACCESS_TOKEN = os.environ["SHOPEE_ACCESS_TOKEN"]


# STEP 2: Read Telegram bot credentials.
# TELEGRAM_BOT_TOKEN comes from @BotFather when you create the bot.
# TELEGRAM_CHAT_ID is the chat where labels will be sent (your employee's chat).
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


# STEP 3: Define constants that control behavior.
# These are not secrets, just settings we might want to tweak later.
SHOPEE_API_BASE_URL = "https://partner.shopeemobile.com"
STATE_FILE_PATH = "data/processed_orders.json"
MAX_ORDERS_PER_RUN = 30  # Safety cap. If we see more than this, something is wrong.
LABEL_IMAGE_DPI = 200    # Resolution for PDF -> PNG conversion.
STATE_RETENTION_DAYS = 30  # How long to remember processed orders before pruning.
