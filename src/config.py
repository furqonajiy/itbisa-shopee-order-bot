"""
config.py
---------
Reads all secrets and settings from environment variables.

Why this file exists:
  We never hardcode secrets in the code. Instead, GitHub Actions injects them
  as environment variables at runtime, and this file is the ONLY place that
  reads them. Every other module gets these values by importing from here.

For local development:
  Create a .env file in the project root (copy from .env.example) and the
  load_dotenv() call below will read it automatically. In GitHub Actions
  there is no .env file, so load_dotenv() does nothing and the values come
  from the workflow's `env:` block instead.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


# STEP 0: Load .env file if it exists (for local development only).
# In production this is a no-op because the .env file is git-ignored
# and never deployed to GitHub Actions.
load_dotenv()


# STEP 1: Compute the project root folder.
# __file__ is the path to this config.py file, e.g. /path/to/repo/src/config.py
# .parent goes up to src/, another .parent goes up to the project root.
# We use absolute paths so the data folder ends up in the same place no
# matter which directory Python was launched from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# STEP 2: Check if we are running in fake mode.
# When fake mode is on, the Shopee credentials are not required because
# we never call the real API. This makes local testing easier.
USE_FAKE_SHOPEE = os.environ.get("USE_FAKE_SHOPEE", "false").lower() == "true"


# STEP 3: Read Shopee API credentials.
# You get these from the Shopee Open Platform when you register your app.
# In fake mode these are optional, so we use .get() with empty string defaults.
if USE_FAKE_SHOPEE:
    SHOPEE_PARTNER_ID = int(os.environ.get("SHOPEE_PARTNER_ID", "0"))
    SHOPEE_PARTNER_KEY = os.environ.get("SHOPEE_PARTNER_KEY", "")
    SHOPEE_SHOP_ID = int(os.environ.get("SHOPEE_SHOP_ID", "0"))
else:
    SHOPEE_PARTNER_ID = int(os.environ["SHOPEE_PARTNER_ID"])
    SHOPEE_PARTNER_KEY = os.environ["SHOPEE_PARTNER_KEY"]
    SHOPEE_SHOP_ID = int(os.environ["SHOPEE_SHOP_ID"])


# STEP 4: Read Telegram bot credentials.
# Always required, even in fake mode, because we still send real Telegram messages.
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


# STEP 5: Define constants that control behavior.
# File paths are anchored to PROJECT_ROOT so they always resolve to the same
# place regardless of where Python was launched from.
SHOPEE_API_BASE_URL = "https://partner.shopeemobile.com"
STATE_FILE_PATH = str(PROJECT_ROOT / "data" / "processed_orders.json")
TOKENS_FILE_PATH = str(PROJECT_ROOT / "data" / "shopee_tokens.json")
MAX_ORDERS_PER_RUN = 30  # Safety cap. If we see more than this, something is wrong.
LABEL_IMAGE_DPI = 200    # Resolution for PDF -> PNG conversion.
STATE_RETENTION_DAYS = 30  # How long to remember processed orders before pruning.
TOKEN_REFRESH_BUFFER_MINUTES = 10  # Refresh the access token N minutes before it expires.
