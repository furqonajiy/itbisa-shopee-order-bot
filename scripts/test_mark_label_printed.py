"""
test_mark_label_printed.py
--------------------------
Diagnostic script to find the correct Shopee endpoint for marking a
shipping label as "printed."

Why we need this:
  Shopee returns "logistics.shipping_document_should_print_first" when we
  try to download a label that was previously generated but not yet
  acknowledged as printed. This blocks our bot from re-fetching labels.

  Shopee's documentation calls this acknowledgment differently in different
  places, so we try a few candidate endpoints and print the response from
  each. The one that succeeds is the endpoint we should add to the main bot.

Usage:
  python scripts/test_mark_label_printed.py 2604209VHV24WU
"""

import json
import sys
from pathlib import Path

import requests

# Add project root to path so we can import our modules.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, shopee_auth


# Candidate endpoints to try, ordered by how likely they are to be correct.
# Each entry has the path and the body shape we will send.
_CANDIDATES = [
    {
        "name": "Candidate A: get_shipping_document_info",
        "path": "/api/v2/logistics/get_shipping_document_info",
        "method": "POST",
        "body_template": {"order_list": [{"order_sn": "{order_sn}"}]},
    },
    {
        "name": "Candidate B: update_shipping_order",
        "path": "/api/v2/logistics/update_shipping_order",
        "method": "POST",
        "body_template": {"order_sn": "{order_sn}"},
    },
    {
        "name": "Candidate C: download_to_label_creator",
        "path": "/api/v2/logistics/download_to_label_creator",
        "method": "POST",
        "body_template": {"order_list": [{"order_sn": "{order_sn}"}]},
    },
    {
        "name": "Candidate D: download_shipping_document with shipping_document_type",
        "path": "/api/v2/logistics/download_shipping_document",
        "method": "POST",
        "body_template": {
            "order_list": [{"order_sn": "{order_sn}"}],
            "shipping_document_type": "NORMAL_AIR_WAYBILL",
        },
    },
]


def main():
    """Tries each candidate endpoint and prints the result."""

    if len(sys.argv) < 2:
        print("Usage: python scripts/test_mark_label_printed.py ORDER_SN")
        print("Example: python scripts/test_mark_label_printed.py 2604209VHV24WU")
        sys.exit(1)

    order_sn = sys.argv[1]

    print("=" * 60)
    print("Mark Label Printed - Endpoint Discovery")
    print("=" * 60)
    print(f"Environment: {config.SHOPEE_API_BASE_URL}")
    print(f"Order to test: {order_sn}")
    print()
    print(f"Trying {len(_CANDIDATES)} candidate endpoints...")
    print()

    # STEP 1: Try each candidate.
    for i, candidate in enumerate(_CANDIDATES, start=1):
        print(f"[{i}/{len(_CANDIDATES)}] {candidate['name']}")
        print(f"  Path: {candidate['path']}")

        # Substitute the order_sn into the body template.
        body = _fill_body(candidate["body_template"], order_sn)
        print(f"  Body: {json.dumps(body)}")

        try:
            response = _call_shopee(
                path=candidate["path"],
                method=candidate["method"],
                body=body,
            )
            print(f"  HTTP status: {response.status_code}")
            print(f"  Content-Type: {response.headers.get('content-type', '(none)')}")

            # Print the response body. If it is JSON, pretty-print it.
            content_type = response.headers.get("content-type", "").lower()
            if "json" in content_type:
                try:
                    data = response.json()
                    print(f"  Response:")
                    print(_indent(json.dumps(data, indent=2)))
                except Exception:
                    print(f"  Could not parse JSON, raw text:")
                    print(_indent(response.text[:500]))
            elif "application/pdf" in content_type:
                print(f"  ✓ Got a PDF! ({len(response.content)} bytes)")
                pdf_path = PROJECT_ROOT / f"test_label_{order_sn}.pdf"
                with open(pdf_path, "wb") as f:
                    f.write(response.content)
                print(f"  Saved to {pdf_path}")
            else:
                print(f"  Unexpected content-type. First 200 bytes:")
                print(_indent(repr(response.content[:200])))
        except Exception as e:
            print(f"  ✗ Exception: {e}")

        print()

    # STEP 2: After all candidates, try the original download once more
    # to see if any of the candidates above unblocked it.
    print("[Final] Re-trying download_shipping_document...")
    try:
        response = _call_shopee(
            path="/api/v2/logistics/download_shipping_document",
            method="POST",
            body={"order_list": [{"order_sn": order_sn}]},
        )
        content_type = response.headers.get("content-type", "").lower()
        print(f"  HTTP status: {response.status_code}")
        print(f"  Content-Type: {content_type}")
        if "application/pdf" in content_type:
            print(f"  ✓ Got a PDF! ({len(response.content)} bytes)")
            print(f"  This means one of the candidates above unblocked the order.")
        else:
            print(f"  Still blocked. Response:")
            print(_indent(response.text[:500]))
    except Exception as e:
        print(f"  ✗ Exception: {e}")

    print()
    print("=" * 60)
    print("Discovery complete.")
    print("=" * 60)
    print()
    print("Look for the candidate above that returned a successful response")
    print("(no 'error' field in the JSON, or HTTP 200 with PDF content).")
    print("That tells us which endpoint to add to the main bot.")


# ============================================================
# Helpers
# ============================================================

def _fill_body(template, order_sn):
    """Fills {order_sn} placeholders in the template body."""
    text = json.dumps(template)
    text = text.replace("{order_sn}", order_sn)
    return json.loads(text)


def _indent(text, prefix="    "):
    """Indents every line of text by the prefix, for readable nesting."""
    return "\n".join(prefix + line for line in text.split("\n"))


def _call_shopee(path, method, body=None):
    """
    Calls a Shopee endpoint with proper signing and returns the raw response.
    """
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

    if method == "GET":
        return requests.get(url, timeout=30)
    return requests.post(url, json=body, timeout=30)


if __name__ == "__main__":
    main()
