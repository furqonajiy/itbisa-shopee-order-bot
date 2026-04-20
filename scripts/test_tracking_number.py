"""
test_tracking_number.py
-----------------------
Diagnostic to check whether the "tracking_number_invalid" error from
create_shipping_document can be resolved by passing the tracking_number
explicitly.

What this script does:
  1. Calls get_tracking_number to find out what tracking number Shopee has
     on file for the order.
  2. If a tracking number is returned, retries create_shipping_document
     and download_shipping_document with that tracking number explicitly
     in the body.
  3. Reports whether the explicit tracking number unblocks the flow.

Usage:
  python scripts/test_tracking_number.py 2604209VHV24WU
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
    """Inspects the tracking number and retries the document flow."""

    if len(sys.argv) < 2:
        print("Usage: python scripts/test_tracking_number.py ORDER_SN")
        sys.exit(1)

    order_sn = sys.argv[1]

    print("=" * 60)
    print("Tracking Number Diagnostic")
    print("=" * 60)
    print(f"Order: {order_sn}")
    print()

    # STEP 1: Ask Shopee for the tracking number on file.
    # get_tracking_number is a GET endpoint that takes order_sn as a query param.
    print("[1/3] Calling get_tracking_number...")
    tracking_response = _call_shopee_get(
        path="/api/v2/logistics/get_tracking_number",
        params={"order_sn": order_sn},
    )
    print(_pretty(tracking_response))
    print()

    # Extract the tracking number from the response.
    tracking_number = (
        tracking_response.get("response", {}).get("tracking_number")
    )
    if not tracking_number:
        print("  No tracking number returned by Shopee.")
        print("  This means the order genuinely has no tracking number on file,")
        print("  or the API requires a different lookup method.")
        print()
        print("  Cannot continue with the retry. The support ticket is the right next step.")
        return

    print(f"  Found tracking number: {tracking_number}")
    print()

    # STEP 2: Retry create_shipping_document with the explicit tracking number.
    print("[2/3] Retrying create_shipping_document with explicit tracking number...")
    create_response = _call_shopee_post(
        path="/api/v2/logistics/create_shipping_document",
        body={
            "order_list": [
                {
                    "order_sn": order_sn,
                    "tracking_number": tracking_number,
                    "shipping_document_type": "THERMAL_AIR_WAYBILL",
                }
            ],
        },
    )
    print(_pretty(create_response))
    print()

    # STEP 3: Try to download the document.
    print("[3/3] Calling download_shipping_document...")
    raw = _call_shopee_post_raw(
        path="/api/v2/logistics/download_shipping_document",
        body={
            "order_list": [
                {
                    "order_sn": order_sn,
                    "shipping_document_type": "THERMAL_AIR_WAYBILL",
                }
            ],
        },
    )

    content_type = raw.headers.get("content-type", "").lower()
    print(f"  HTTP status: {raw.status_code}")
    print(f"  Content-Type: {content_type}")

    if "application/pdf" in content_type:
        output_path = PROJECT_ROOT / f"test_label_{order_sn}.pdf"
        with open(output_path, "wb") as f:
            f.write(raw.content)
        print(f"  ✓ SUCCESS! PDF saved to {output_path}")
        print()
        print("=" * 60)
        print("THE FIX WORKS.")
        print("=" * 60)
        print("We need to update shopee_client.py to:")
        print("  1. Call get_tracking_number first.")
        print("  2. Include the tracking number in create_shipping_document.")
    else:
        try:
            print(f"  Response body:")
            print(_pretty(raw.json()))
        except Exception:
            print(f"  Raw text: {raw.text[:300]}")
        print()
        print("Tracking number alone did not unblock it.")
        print("Send the support ticket. Include the get_tracking_number output above.")


def _pretty(data):
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return "\n".join("  " + line for line in text.split("\n"))


def _call_shopee_get(path, params=None):
    response = _make_request("GET", path, params=params)
    try:
        return response.json()
    except Exception:
        return {"_status": response.status_code, "_text": response.text[:500]}


def _call_shopee_post(path, body=None):
    response = _make_request("POST", path, body=body)
    try:
        return response.json()
    except Exception:
        return {"_status": response.status_code, "_text": response.text[:500]}


def _call_shopee_post_raw(path, body=None):
    return _make_request("POST", path, body=body)


def _make_request(method, path, params=None, body=None):
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

    if method == "GET":
        return requests.get(url, params=params, timeout=30)
    return requests.post(url, json=body, timeout=30)


if __name__ == "__main__":
    main()
