"""
fetch_label_sample.py
---------------------
Downloads the label PDF of already-labelled orders into out/, untouched.

Why this file exists:
  Telegram only ever receives the rendered PNG, so the original PDF (its text
  layer and exact column positions) is otherwise never kept. Changing how a
  label is rendered needs real PDFs to test against. The workflow
  label_sample.yml runs this, encrypts each PDF and uploads it as a one-day
  artifact: the repo is public and a label carries the buyer's name, address
  and phone.

It never arranges shipment, never sends to Telegram and never touches
processed_orders.json / order_items.json. Pass only orders the bot already
labelled (status PROCESSED or later).

Usage:
  python scripts/fetch_label_sample.py ORDER_SN [ORDER_SN ...]
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import shopee_client  # noqa: E402

OUT_DIR = PROJECT_ROOT / "out"


def main(order_sns):
    OUT_DIR.mkdir(exist_ok=True)
    failed = 0
    for order_sn in order_sns:
        try:
            pdf_bytes = shopee_client.get_shipping_label_pdf(order_sn)
        except Exception as e:                              # noqa: BLE001
            print(f"✗ {order_sn}: {e}")
            failed += 1
            continue
        if pdf_bytes is None:
            print(f"✗ {order_sn}: label belum siap")
            failed += 1
            continue
        (OUT_DIR / f"{order_sn}.pdf").write_bytes(pdf_bytes)
        print(f"✓ {order_sn}: {len(pdf_bytes)} bytes")
    return 1 if failed == len(order_sns) else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1:]))
