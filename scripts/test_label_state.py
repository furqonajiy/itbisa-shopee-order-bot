"""
test_label_state.py
-------------------
Focused diagnostic: ask Shopee what the current state of the label task is.

Why this matters:
  We get "shipping_document_should_print_first" when trying to download.
  Before we try fancy fixes, we want to know what Shopee thinks the
  current state of this label is. This script calls
  get_shipping_document_result, which is Shopee's official way to ask
  "what is the status of this label?"

  Possible answers we might see:
    - status: READY        -> label exists, download should work
    - status: PRINTED      -> already downloaded once, may need re-create
    - status: NO_RECORD    -> never created, need create_shipping_document
    - status: PROCESSING   -> Shopee is still generating, just wait

  Knowing the actual status tells us the correct next action.

Usage:
  python scripts/test_label_state.py 2604209VHV24WU
"""

import json
import sys
from pathlib import Path

import requests

# Add project root to path so we can import our modules.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, shopee_auth


def main():
    """Inspects the document state for a given order."""

    if len(sys.argv) < 2:
        print("Usage: python scripts/test_label_state.py ORDER_SN")
        sys.exit(1)

    order_sn = sys.argv[1]

    print("=" * 60)
    print("Shopee Label State Inspector")
    print("=" * 60)
    print(f"Order: {order_sn}")
    print()

    # STEP 1: Ask Shopee for the document task status.
    print("[1/3] Calling get_shipping_document_result...")
    result = _call_shopee(
        "/api/v2/logistics/get_shipping_document_result",
        body={"order_list": [{"order_sn": order_sn}]},
    )
    print(_pretty(result))
    print()

    # STEP 2: Ask Shopee for the suggested document parameters.
    # This tells us which document type Shopee expects for this order.
    print("[2/3] Calling get_shipping_document_parameter...")
    params = _call_shopee(
        "/api/v2/logistics/get_shipping_document_parameter",
        body={"order_list": [{"order_sn": order_sn}]},
    )
    print(_pretty(params))
    print()

    # STEP 3: Try to create a fresh shipping document, in case the existing
    # one is stuck. This call should be idempotent if the doc already exists.
    print("[3/3] Calling create_shipping_document (in case it needs re-creation)...")
    create = _call_shopee(
        "/api/v2/logistics/create_shipping_document",
        body={"order_list": [{"order_sn": order_sn}]},
    )
    print(_pretty(create))
    print()

    print("=" * 60)
    print("Inspection complete.")
    print("=" * 60)
    print()
    print("Key things to look for:")
    print("  - In step 1, what is the 'status' field? (READY / PRINTED / etc)")
    print("  - In step 2, what shipping_document_type is suggested?")
    print("  - In step 3, did create_shipping_document accept or reject?")
    print("  Share this output and we can pick the right fix.")


def _pretty(data):
    """Pretty-print JSON with indentation for readability."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return "\n".join("  " + line for line in text.split("\n"))


def _call_shopee(path, body):
    """Calls a signed Shopee endpoint and returns parsed JSON."""
    import time
    import hashlib
    import hmac

    access_token = shopee_auth.get_valid_access_token()
    timestamp = int(time.time())

    base_string = (
        f"{config.SHOPEE_PARTNER_ID}{path}{timestamp}"
        f"{access_token}{config.SHOPEE_SHOP_ID}"
    )
    signature = hmac.new(
        config.SHOPEE_PARTNER_KEY.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    url = (
        f"{config.SHOPEE_API_BASE_URL}{path}"
        f"?partner_id={config.SHOPEE_PARTNER_ID}"
        f"&timestamp={timestamp}"
        f"&access_token={access_token}"
        f"&shop_id={config.SHOPEE_SHOP_ID}"
        f"&sign={signature}"
    )

    response = requests.post(url, json=body, timeout=30)
    try:
        return response.json()
    except Exception:
        return {"_http_status": response.status_code, "_text": response.text[:500]}


if __name__ == "__main__":
    main()
