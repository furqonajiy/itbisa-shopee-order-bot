"""
test_label_generation.py
------------------------
Standalone script to diagnose Shopee shipping label generation.

When the main bot keeps reporting "label not ready" for orders that should
have labels, this script shows you exactly what Shopee is returning at each
step of the label flow, so you can see where the assumption is breaking.

What it does:
  1. Asks for an order_sn to test (or picks the first PROCESSED order).
  2. Calls Shopee's get_shipping_parameter to confirm shipping is set up.
  3. Calls create_shipping_document to request a label.
  4. Polls Shopee's get_shipping_document_result to check generation status.
  5. Calls download_shipping_document and prints what Shopee actually returns.
  6. Saves the result to a file for inspection.

The verbose output makes it obvious whether the label is missing because
Shopee genuinely has not generated it yet, or because our code is
mis-interpreting Shopee's response.

Usage:
  python scripts/test_label_generation.py
  python scripts/test_label_generation.py 2604209VHV24WU
"""

import json
import sys
from pathlib import Path

import requests

# Add project root to path so we can import our modules.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, shopee_auth, shopee_client


def main():
    """Walks through the label generation steps with verbose output."""

    print("=" * 60)
    print("Shopee Label Generation Diagnostic")
    print("=" * 60)
    print(f"Environment: {config.SHOPEE_API_BASE_URL}")
    print(f"Partner ID:  {config.SHOPEE_PARTNER_ID}")
    print(f"Shop ID:     {config.SHOPEE_SHOP_ID}")
    print()

    # STEP 1: Pick which order to test.
    if len(sys.argv) > 1:
        order_sn = sys.argv[1]
        print(f"Testing order from command line: {order_sn}")
    else:
        print("No order_sn given on command line, fetching from Shopee...")
        orders = shopee_client.get_pending_orders()
        if not orders:
            print("No pending orders found. Pass an order_sn as an argument:")
            print("  python scripts/test_label_generation.py ORDER_SN_HERE")
            sys.exit(1)
        order_sn = orders[0]["order_sn"]
        print(f"Using first pending order: {order_sn}")
        print(f"  Status: {orders[0].get('order_status', 'UNKNOWN')}")

    print()

    # STEP 2: Check if shipping is set up for this order.
    # get_shipping_parameter tells us what shipping options are available.
    # If this returns nothing, the order is not yet ready for label generation.
    print(f"[1/4] Checking shipping parameters for {order_sn}...")
    shipping_params = _call_shopee(
        path="/api/v2/logistics/get_shipping_parameter",
        method="GET",
        params={"order_sn": order_sn},
    )
    print(f"  Response:")
    print(_pretty_json(shipping_params))
    print()

    # STEP 3: Trigger label generation. This is the same call the main bot makes.
    print(f"[2/4] Calling create_shipping_document for {order_sn}...")
    create_response = _call_shopee(
        path="/api/v2/logistics/create_shipping_document",
        method="POST",
        body={"order_list": [{"order_sn": order_sn}]},
    )
    print(f"  Response:")
    print(_pretty_json(create_response))
    print()

    # STEP 4: Check generation status. This endpoint tells us whether Shopee
    # has finished generating the label, which is the question our main bot
    # is implicitly asking but never gets a clear answer to.
    print(f"[3/4] Checking generation result for {order_sn}...")
    result_response = _call_shopee(
        path="/api/v2/logistics/get_shipping_document_result",
        method="POST",
        body={"order_list": [{"order_sn": order_sn}]},
    )
    print(f"  Response:")
    print(_pretty_json(result_response))
    print()

    # STEP 5: Try to download the actual PDF and see what Shopee returns.
    # This is the call where our main bot keeps failing. We print the raw
    # response details so we can see what is actually coming back.
    print(f"[4/4] Calling download_shipping_document for {order_sn}...")
    raw_response = _call_shopee_raw(
        path="/api/v2/logistics/download_shipping_document",
        method="POST",
        body={"order_list": [{"order_sn": order_sn}]},
    )

    print(f"  HTTP status: {raw_response.status_code}")
    print(f"  Content-Type: {raw_response.headers.get('content-type', '(none)')}")
    print(f"  Content-Length: {raw_response.headers.get('content-length', '(none)')}")
    print(f"  Response body size: {len(raw_response.content)} bytes")
    print()

    content_type = raw_response.headers.get("content-type", "").lower()
    if "application/pdf" in content_type:
        # Save the PDF for inspection.
        output_path = PROJECT_ROOT / f"test_label_{order_sn}.pdf"
        with open(output_path, "wb") as f:
            f.write(raw_response.content)
        print(f"  ✓ Got a PDF! Saved to {output_path}")
        print(f"    Open it to verify the label looks correct.")
    elif "json" in content_type:
        # Shopee returned a JSON error or status response instead of a PDF.
        try:
            json_body = raw_response.json()
            print(f"  Shopee returned JSON instead of PDF:")
            print(_pretty_json(json_body))
            print()
            print(f"  THIS IS THE KEY DIAGNOSTIC INFORMATION.")
            print(f"  The main bot was treating this as 'not ready'. The actual")
            print(f"  reason is in the JSON above. Most likely causes:")
            print(f"    - Label needs to be created via a different endpoint")
            print(f"      (some carriers use create_shipping_document, others")
            print(f"      use ship_order with logistics info).")
            print(f"    - The shipping_document_type parameter might be needed.")
            print(f"    - The order needs a different state transition first.")
        except Exception as e:
            print(f"  Could not parse JSON: {e}")
            print(f"  Raw text: {raw_response.text[:500]}")
    else:
        print(f"  Unexpected content type. Raw bytes (first 200):")
        print(f"  {raw_response.content[:200]!r}")

    print()
    print("=" * 60)
    print("Diagnostic complete.")
    print("=" * 60)
    print()
    print("Next step: share the output above so we can fix the main bot's")
    print("label-fetching logic to match Shopee's actual response.")


# ============================================================
# Helpers
# ============================================================

def _pretty_json(data):
    """Format JSON for readable printing, indented under each line."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return "\n".join(f"    {line}" for line in text.split("\n"))


def _call_shopee(path, method, params=None, body=None):
    """
    Calls a Shopee endpoint with proper signing and returns parsed JSON.
    Use this for endpoints that return JSON (most of them).
    """
    response = _call_shopee_raw(path=path, method=method, params=params, body=body)
    try:
        return response.json()
    except Exception:
        return {"_error": "non-json response", "_text": response.text[:500]}


def _call_shopee_raw(path, method, params=None, body=None):
    """
    Calls a Shopee endpoint with proper signing and returns the raw response
    object. Use this for endpoints that may return non-JSON (like PDF downloads).
    """
    # Get a valid access token (refreshes if needed).
    access_token = shopee_auth.get_valid_access_token()

    # Build the signed URL using the same logic as shopee_client.
    import time
    import hashlib
    import hmac

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

    if method == "GET":
        return requests.get(url, params=params, timeout=30)
    return requests.post(url, json=body, timeout=30)


if __name__ == "__main__":
    main()
