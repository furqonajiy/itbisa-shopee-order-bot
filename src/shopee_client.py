"""
shopee_client.py
----------------
Talks to the Shopee Open API.

Why this file exists:
  Shopee requires every API call to be signed with HMAC-SHA256 using your
  partner key. The signing rules are specific and easy to get wrong, so we
  isolate all that logic here. The rest of the code does not need to know
  anything about HMAC, base URLs, or Shopee's quirks.

Public functions (used by main.py):
  - get_ready_to_ship_orders() -> list of order dicts
  - get_shipping_label_pdf(order_id) -> bytes (or None if not ready yet)
"""

import hashlib
import hmac
import time

import requests

from src import config


# ============================================================
# Internal helpers (start with underscore = "do not use from outside")
# ============================================================

def _make_signature(path, timestamp, access_token, shop_id):
    """
    Builds the HMAC-SHA256 signature that Shopee requires on every shop-level call.

    The signature is computed over a specific string that Shopee defines:
      partner_id + api_path + timestamp + access_token + shop_id

    Then we sign that string with our partner_key.
    """

    # STEP 1: Build the base string exactly as Shopee documents it.
    base_string = f"{config.SHOPEE_PARTNER_ID}{path}{timestamp}{access_token}{shop_id}"

    # STEP 2: Sign it with the partner key using HMAC-SHA256.
    signature = hmac.new(
        config.SHOPEE_PARTNER_KEY.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return signature


def _build_request_url(path):
    """
    Builds the full URL with all the common query parameters that Shopee
    requires (partner_id, timestamp, access_token, shop_id, sign).
    """

    # STEP 1: Get the current Unix timestamp. Shopee rejects requests with
    # timestamps that are too old, so we generate a fresh one each call.
    timestamp = int(time.time())

    # STEP 2: Generate the signature for this specific call.
    signature = _make_signature(
        path=path,
        timestamp=timestamp,
        access_token=config.SHOPEE_ACCESS_TOKEN,
        shop_id=config.SHOPEE_SHOP_ID,
    )

    # STEP 3: Assemble the full URL with required query params.
    url = (
        f"{config.SHOPEE_API_BASE_URL}{path}"
        f"?partner_id={config.SHOPEE_PARTNER_ID}"
        f"&timestamp={timestamp}"
        f"&access_token={config.SHOPEE_ACCESS_TOKEN}"
        f"&shop_id={config.SHOPEE_SHOP_ID}"
        f"&sign={signature}"
    )
    return url


# ============================================================
# Public functions (used by main.py)
# ============================================================

def get_ready_to_ship_orders():
    """
    Fetches the list of orders that are paid and waiting to be shipped.

    Returns:
      A list of dicts. Each dict contains at least:
        - order_sn: the Shopee order number (string)
        - recipient_name, courier, items: used for the Telegram caption
    """

    # STEP 1: Build the URL for the "get order list" endpoint.
    path = "/api/v2/order/get_order_list"
    url = _build_request_url(path)

    # STEP 2: Set up the time window. We look back 7 days to catch orders we
    # might have missed, but the state file makes sure we never re-process them.
    seven_days_ago = int(time.time()) - (7 * 24 * 60 * 60)
    now = int(time.time())

    params = {
        "time_range_field": "create_time",
        "time_from": seven_days_ago,
        "time_to": now,
        "page_size": 100,
        "order_status": "READY_TO_SHIP",
    }

    # STEP 3: Make the HTTP call.
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    # STEP 4: Extract the order numbers from the response.
    order_summaries = data.get("response", {}).get("order_list", [])
    if not order_summaries:
        return []

    order_sns = [o["order_sn"] for o in order_summaries]

    # STEP 5: Fetch full details for those orders (recipient name, items, etc).
    return _get_order_details(order_sns)


def _get_order_details(order_sns):
    """
    Fetches full details for a list of order numbers.

    Shopee's get_order_list only returns IDs, so we need a second call
    to get the actual recipient name, items, and courier.
    """

    # STEP 1: Build URL for the "get order detail" endpoint.
    path = "/api/v2/order/get_order_detail"
    url = _build_request_url(path)

    # STEP 2: Ask for the specific fields we need for the Telegram caption.
    params = {
        "order_sn_list": ",".join(order_sns),
        "response_optional_fields": "recipient_address,item_list,buyer_username",
    }

    # STEP 3: Make the call and return the order list.
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    return data.get("response", {}).get("order_list", [])


def get_shipping_label_pdf(order_sn):
    """
    Fetches the shipping label PDF for a single order.

    Shopee's flow has two steps:
      1. Tell Shopee to generate the label (create_shipping_document).
      2. Wait a few seconds, then download the generated PDF.

    Sometimes the label is not ready right away, so we retry up to 3 times
    within this single function call. If it is still not ready after that,
    we return None and let the next scheduled run try again.

    Args:
      order_sn: the Shopee order number string.

    Returns:
      PDF file contents as bytes, OR None if the label is not ready yet.
    """

    # STEP 1: Ask Shopee to create the shipping document.
    _create_shipping_document(order_sn)

    # STEP 2: Try to download the PDF, with short retries for "not ready yet".
    for attempt in range(3):
        # Wait a bit before each attempt. Shopee usually takes a few seconds.
        time.sleep(5)

        pdf_bytes = _download_shipping_document(order_sn)
        if pdf_bytes is not None:
            return pdf_bytes

        print(f"  Label for {order_sn} not ready yet, attempt {attempt + 1}/3")

    # STEP 3: Give up for this run. The next scheduled run will try again.
    print(f"  Label for {order_sn} still not ready, will retry next run")
    return None


def _create_shipping_document(order_sn):
    """
    Tells Shopee to start generating the shipping document for an order.
    This call returns quickly but the actual PDF takes a few seconds to generate.
    """

    path = "/api/v2/logistics/create_shipping_document"
    url = _build_request_url(path)

    body = {
        "order_list": [{"order_sn": order_sn}],
    }

    response = requests.post(url, json=body, timeout=30)
    response.raise_for_status()


def _download_shipping_document(order_sn):
    """
    Downloads the generated shipping document PDF.

    Returns:
      bytes if the PDF is ready, or None if Shopee says it is still generating.
    """

    path = "/api/v2/logistics/download_shipping_document"
    url = _build_request_url(path)

    body = {
        "order_list": [{"order_sn": order_sn}],
    }

    response = requests.post(url, json=body, timeout=30)

    # Shopee returns the PDF directly as the response body if it is ready.
    # If not ready, it returns a JSON error response instead.
    content_type = response.headers.get("content-type", "")
    if "application/pdf" in content_type:
        return response.content

    # Not ready yet.
    return None
