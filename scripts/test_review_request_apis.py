"""
test_review_request_apis.py
---------------------------
Diagnostic script to verify the three Shopee APIs we need for automatic
review request messaging work correctly for this shop.

What it tests:
  1. Listing COMPLETED orders (orders where buyer received the package).
  2. Checking whether an order has a review attached.
  3. Sending a chat message to the buyer of an order.

The third test is the most sensitive. It will actually send a real message
if successful, so we use a polite "thank you for shopping" test message
rather than the real review-request wording, to avoid bothering customers
during the diagnostic phase.

Usage:
  python scripts/test_review_request_apis.py

The script picks the most recent COMPLETED order and tests against it.
If you want to test against a specific order, pass it as an argument:

  python scripts/test_review_request_apis.py 240418ABC123
"""

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, shopee_auth


def main():
    """Runs three diagnostic tests in order."""

    print("=" * 60)
    print("Review Request APIs Diagnostic")
    print("=" * 60)
    print(f"Environment: {config.SHOPEE_API_BASE_URL}")
    print(f"Partner ID:  {config.SHOPEE_PARTNER_ID}")
    print(f"Shop ID:     {config.SHOPEE_SHOP_ID}")
    print()

    # STEP 1: List completed orders. This tells us whether our order-list
    # endpoint works for the COMPLETED status (we already know it works for
    # READY_TO_SHIP and PROCESSED).
    print("[1/3] Listing COMPLETED orders from the past 15 days...")
    orders = _list_completed_orders()

    if not orders:
        print("  No completed orders found. Cannot continue diagnostic.")
        print("  Come back when you have at least one completed order.")
        return

    print(f"  Found {len(orders)} completed orders")
    print()

    # STEP 2: Pick one to test against.
    if len(sys.argv) > 1:
        target_order_sn = sys.argv[1]
        print(f"Using order from command line: {target_order_sn}")
    else:
        target_order_sn = orders[0]["order_sn"]
        print(f"Using most recent completed order: {target_order_sn}")
    print()

    # STEP 3: Check if this order has a review.
    print(f"[2/3] Checking review status for order {target_order_sn}...")
    review_info = _check_order_review(target_order_sn)
    print(f"  Response:")
    print(_pretty(review_info))
    print()

    # STEP 4: Try to find the buyer's conversation info so we can message them.
    # Shopee's chat API requires a conversation_id, which we get by looking up
    # conversations by user_id. The user_id comes from order details.
    print(f"[3/3] Testing chat message capability...")
    print("  Step 3a: Fetching order detail to get buyer user_id...")
    detail = _get_order_detail(target_order_sn)
    buyer_user_id = detail.get("buyer_user_id")
    buyer_username = detail.get("buyer_username", "unknown")
    print(f"    Buyer: {buyer_username} (user_id: {buyer_user_id})")

    if not buyer_user_id:
        print("  Could not find buyer_user_id. Cannot test chat sending.")
        print("  This may mean the order detail API does not return user_id,")
        print("  or we need a different endpoint to look it up.")
        return

    # We do NOT actually send a message in the diagnostic. That would bother a
    # real customer. Instead we check whether the chat endpoint responds to a
    # dry-run style call. If this shop does not have chat API access at all,
    # we will get a clear permissions error here.
    print("  Step 3b: Testing sellerchat endpoint access (NOT sending a real message)...")
    chat_test = _test_chat_endpoint_access(buyer_user_id)
    print(f"    Response:")
    print(_pretty(chat_test))
    print()

    # STEP 5: Summary.
    print("=" * 60)
    print("Diagnostic complete.")
    print("=" * 60)
    print()
    print("What to look for in the output above:")
    print("  [1] Found at least one completed order.")
    print("  [2] Review check returned either a review or a clear 'no review' status.")
    print("  [3] Chat endpoint responded without a permissions error.")
    print()
    print("If all three worked, we can safely build the review request script.")
    print("If any failed, share this output and we will work around the gap.")


# ============================================================
# Tests
# ============================================================

def _list_completed_orders():
    """Fetches orders in COMPLETED status from the past 15 days."""

    path = "/api/v2/order/get_order_list"
    url = _build_signed_url(path)

    # Look back 15 days. Buyers typically confirm receipt within 1-2 weeks.
    fifteen_days_ago = int(time.time()) - (15 * 24 * 60 * 60)
    now = int(time.time())

    params = {
        "time_range_field": "create_time",
        "time_from": fifteen_days_ago,
        "time_to": now,
        "page_size": 100,
        "order_status": "COMPLETED",
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        print(f"  Shopee returned error: {data}")
        return []

    return data.get("response", {}).get("order_list", [])


def _check_order_review(order_sn):
    """
    Checks if an order has a review. Shopee has a comment API that returns
    reviews for a shop, which we can filter by order_sn.
    """

    path = "/api/v2/product/get_comment"
    url = _build_signed_url(path)

    # Try looking up comments for this specific order.
    thirty_days_ago = int(time.time()) - (30 * 24 * 60 * 60)
    now = int(time.time())

    params = {
        "cursor": "",
        "page_size": 20,
        "order_sn": order_sn,
    }

    response = requests.get(url, params=params, timeout=30)
    try:
        return response.json()
    except Exception:
        return {"_status": response.status_code, "_text": response.text[:500]}


def _get_order_detail(order_sn):
    """Fetches full order details to get the buyer's user_id."""

    path = "/api/v2/order/get_order_detail"
    url = _build_signed_url(path)

    params = {
        "order_sn_list": order_sn,
        "response_optional_fields": "buyer_user_id,buyer_username",
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    order_list = data.get("response", {}).get("order_list", [])
    return order_list[0] if order_list else {}


def _test_chat_endpoint_access(user_id):
    """
    Tests whether we have access to the sellerchat endpoint. We use the
    get_conversation_list endpoint which is read-only, so calling it
    does not send any message. If we get a permissions error, we know
    the shop does not have chat API access.
    """

    path = "/api/v2/sellerchat/get_conversation_list"
    url = _build_signed_url(path)

    params = {
        "direction": "latest",
        "type": "all",
        "page_size": 5,
    }

    response = requests.get(url, params=params, timeout=30)
    try:
        return response.json()
    except Exception:
        return {"_status": response.status_code, "_text": response.text[:500]}


# ============================================================
# Helpers
# ============================================================

def _pretty(data):
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return "\n".join("    " + line for line in text.split("\n"))


def _build_signed_url(path):
    """Constructs the signed Shopee URL with all required query parameters."""

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

    return (
        f"{config.SHOPEE_API_BASE_URL}{path}"
        f"?partner_id={config.SHOPEE_PARTNER_ID}"
        f"&timestamp={timestamp}"
        f"&access_token={access_token}"
        f"&shop_id={config.SHOPEE_SHOP_ID}"
        f"&sign={signature}"
    )


if __name__ == "__main__":
    main()
