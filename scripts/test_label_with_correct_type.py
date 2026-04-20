"""
test_label_with_correct_type.py
-------------------------------
Final diagnostic: try the full label flow using the correct document type
that Shopee suggests for the order.

Theory we are confirming:
  Shopee returns "shipping_document_should_print_first" not because the
  label needs to be printed, but because we are asking for the wrong
  shipping_document_type. Each order has a suggested type (THERMAL or
  NORMAL air waybill), and we need to use that specific type when calling
  create_shipping_document and download_shipping_document.

What this script does:
  1. Asks Shopee for the suggested document type for the order.
  2. Calls create_shipping_document with that exact type.
  3. Polls get_shipping_document_result to wait for it to be READY.
  4. Calls download_shipping_document with that same type.
  5. If a PDF comes back, saves it for inspection.

Usage:
  python scripts/test_label_with_correct_type.py 2604209VHV24WU
"""

import json
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, shopee_auth


def main():
    """Runs the full label flow with the correct document type."""

    if len(sys.argv) < 2:
        print("Usage: python scripts/test_label_with_correct_type.py ORDER_SN")
        sys.exit(1)

    order_sn = sys.argv[1]

    print("=" * 60)
    print("Label Flow With Correct Document Type")
    print("=" * 60)
    print(f"Order: {order_sn}")
    print()

    # STEP 1: Get the suggested document type for this order.
    print("[1/4] Getting suggested document type...")
    params_response = _call_shopee_json(
        "/api/v2/logistics/get_shipping_document_parameter",
        body={"order_list": [{"order_sn": order_sn}]},
    )
    print(_pretty(params_response))

    result_list = params_response.get("response", {}).get("result_list", [])
    if not result_list:
        print("  No result_list returned, cannot continue.")
        sys.exit(1)

    suggested_type = result_list[0].get("suggest_shipping_document_type")
    print(f"\n  Suggested type: {suggested_type}")
    print()

    # STEP 2: Create the shipping document with the suggested type.
    print(f"[2/4] Creating shipping document with type {suggested_type}...")
    create_response = _call_shopee_json(
        "/api/v2/logistics/create_shipping_document",
        body={
            "order_list": [
                {
                    "order_sn": order_sn,
                    "shipping_document_type": suggested_type,
                }
            ],
        },
    )
    print(_pretty(create_response))
    print()

    # STEP 3: Wait briefly and check the result status.
    print("[3/4] Waiting 5 seconds, then checking document status...")
    time.sleep(5)
    result_response = _call_shopee_json(
        "/api/v2/logistics/get_shipping_document_result",
        body={
            "order_list": [
                {
                    "order_sn": order_sn,
                    "shipping_document_type": suggested_type,
                }
            ],
        },
    )
    print(_pretty(result_response))
    print()

    # STEP 4: Download the document with the correct type.
    print(f"[4/4] Downloading document with type {suggested_type}...")
    raw_response = _call_shopee_raw(
        "/api/v2/logistics/download_shipping_document",
        body={
            "order_list": [
                {
                    "order_sn": order_sn,
                    "shipping_document_type": suggested_type,
                }
            ],
            "shipping_document_type": suggested_type,
        },
    )

    content_type = raw_response.headers.get("content-type", "").lower()
    print(f"  HTTP status: {raw_response.status_code}")
    print(f"  Content-Type: {content_type}")
    print(f"  Body size: {len(raw_response.content)} bytes")
    print()

    if "application/pdf" in content_type:
        output_path = PROJECT_ROOT / f"test_label_{order_sn}.pdf"
        with open(output_path, "wb") as f:
            f.write(raw_response.content)
        print(f"  ✓ SUCCESS! Got a PDF, saved to {output_path}")
        print(f"  Open it to verify the label looks right.")
        print()
        print("=" * 60)
        print("THE FIX IS CONFIRMED.")
        print("=" * 60)
        print("We need to update shopee_client.py to:")
        print("  1. Call get_shipping_document_parameter for each order to")
        print("     learn the suggested shipping_document_type.")
        print("  2. Pass that type to both create_shipping_document and")
        print("     download_shipping_document.")
    else:
        try:
            print("  Response body (JSON):")
            print(_pretty(raw_response.json()))
        except Exception:
            print(f"  Response body: {raw_response.text[:500]}")
        print()
        print("Theory not confirmed. We need a different approach.")
        print("Share this output and we can plan the next step.")


def _pretty(data):
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return "\n".join("  " + line for line in text.split("\n"))


def _call_shopee_json(path, body):
    """Signed POST that returns parsed JSON."""
    response = _call_shopee_raw(path, body)
    try:
        return response.json()
    except Exception:
        return {"_status": response.status_code, "_text": response.text[:500]}


def _call_shopee_raw(path, body):
    """Signed POST that returns the raw response object."""
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

    return requests.post(url, json=body, timeout=30)


if __name__ == "__main__":
    main()
