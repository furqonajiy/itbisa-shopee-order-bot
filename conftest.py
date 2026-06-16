import os
import sys

# Make the repo root importable so tests can do `from src... import ...`.
sys.path.insert(0, os.path.dirname(__file__))

# src/config.py reads these at import time. Set dummy values so importing
# modules under test never fails for lack of real credentials. setdefault
# means a real environment (CI/local) is never overridden.
os.environ.setdefault("SHOPEE_PARTNER_ID", "1")
os.environ.setdefault("SHOPEE_PARTNER_KEY", "test_key")
os.environ.setdefault("SHOPEE_SHOP_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100")
