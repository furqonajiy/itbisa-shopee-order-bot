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
  - get_pending_orders() -> list of order dicts (READY_TO_SHIP + PROCESSED)
  - ship_order_to_dropoff(order_sn) -> None (raises on error)
  - get_shipping_label_pdf(order_sn) -> bytes (or None if not ready yet)

Fake mode:
  When config.USE_FAKE_SHOPEE is True, the public functions below delegate
  to shopee_client_fake instead of calling the real Shopee API. This is
  used during local development when sandbox access is not available.
  To remove fake mode entirely, delete shopee_client_fake.py and remove
  the if-blocks at the top of each public function below.
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

    This function calls shopee_auth.get_valid_access_token() which handles
    token freshness checking and refreshing transparently.
    """

    # STEP 1: Get a fresh access token. This call is cheap when the stored
    # token is still valid, and triggers a refresh when it is not.
    # We import inside the function to avoid a circular import at module load time.
    from src import shopee_auth
    access_token = shopee_auth.get_valid_access_token()

    # STEP 2: Get the current Unix timestamp. Shopee rejects requests with
    # timestamps that are too old, so we generate a fresh one each call.
    timestamp = int(time.time())

    # STEP 3: Generate the signature for this specific call.
    signature = _make_signature(
        path=path,
        timestamp=timestamp,
        access_token=access_token,
        shop_id=config.SHOPEE_SHOP_ID,
    )

    # STEP 4: Assemble the full URL with required query params.
    url = (
        f"{config.SHOPEE_API_BASE_URL}{path}"
        f"?partner_id={config.SHOPEE_PARTNER_ID}"
        f"&timestamp={timestamp}"
        f"&access_token={access_token}"
        f"&shop_id={config.SHOPEE_SHOP_ID}"
        f"&sign={signature}"
    )
    return url


# ============================================================
# Public functions (used by main.py)
# ============================================================

def get_pending_orders():
    """
    Fetches orders that need a label printed.

    This includes orders in two statuses:
      - READY_TO_SHIP: paid, but seller has not yet arranged shipment.
        These orders need ship_order_to_dropoff() called before the label
        becomes available.
      - PROCESSED: shipment arrangement done, label is being generated or
        already available for download.

    We fetch both statuses in one call because both need handling, and
    main.py decides what to do based on each order's individual status.

    Returns:
      A list of order dicts. Each dict includes order_status so the caller
      can decide whether to ship_order or just fetch the label.
    """

    # STEP 0: In fake mode, delegate to the fake client and return early.
    if config.USE_FAKE_SHOPEE:
        from src import shopee_client_fake
        return shopee_client_fake.get_pending_orders()

    # STEP 1: Shopee's get_order_list only accepts one status per call,
    # so we make two calls and combine the results.
    ready_to_ship = _get_order_summaries_by_status("READY_TO_SHIP")
    processed = _get_order_summaries_by_status("PROCESSED")
    all_summaries = ready_to_ship + processed

    if not all_summaries:
        return []

    # STEP 2: Get full details for all orders in one batched call.
    # We keep track of which order had which status so we can attach it to
    # the detail dict (Shopee's get_order_detail does not always return it).
    status_by_sn = {s["order_sn"]: s.get("order_status") for s in all_summaries}
    order_sns = list(status_by_sn.keys())

    details = _get_order_details(order_sns)

    # STEP 3: Attach the status from the summary to each detail dict so
    # main.py can decide what to do per order.
    for d in details:
        if not d.get("order_status"):
            d["order_status"] = status_by_sn.get(d["order_sn"], "UNKNOWN")

    return details


def ship_order_to_dropoff(order_sn):
    """
    Tells Shopee that the seller will drop off the package at the courier
    counter. This is the API equivalent of clicking
    "Atur Pengiriman" -> "Antar ke Counter" in the Shopee Seller app.

    After this call succeeds, the order moves from READY_TO_SHIP to
    PROCESSED, and Shopee starts generating the shipping label.

    Args:
      order_sn: the Shopee order number string.

    Raises:
      requests.HTTPError if Shopee rejects the request.
    """

    # STEP 0: In fake mode, delegate to the fake client.
    if config.USE_FAKE_SHOPEE:
        from src import shopee_client_fake
        return shopee_client_fake.ship_order_to_dropoff(order_sn)

    # STEP 1: Build the URL for the ship_order endpoint.
    path = "/api/v2/logistics/ship_order"
    url = _build_request_url(path)

    # STEP 2: Build the request body. We always use dropoff because that
    # matches how the warehouse operates (employee carries packages to the
    # courier counter at the end of the day).
    body = {
        "order_sn": order_sn,
        "dropoff": {},  # Empty dict means "use the default dropoff option"
    }

    # STEP 3: Make the call. Any error (4xx or 5xx) will raise an exception
    # and bubble up to main.py, which decides whether to retry or skip.
    response = requests.post(url, json=body, timeout=30)
    response.raise_for_status()


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

    # STEP 0: In fake mode, delegate to the fake client and return early.
    if config.USE_FAKE_SHOPEE:
        from src import shopee_client_fake
        return shopee_client_fake.get_shipping_label_pdf(order_sn)

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


# ============================================================
# More internal helpers (used only by the public functions above)
# ============================================================

def _get_order_summaries_by_status(order_status):
    """
    Fetches a single page of order summaries (order_sn + status only) for
    the given status. The full details come from a separate call.

    Args:
      order_status: one of READY_TO_SHIP, PROCESSED, etc.

    Returns:
      A list of {"order_sn": ..., "order_status": ...} dicts. Empty list
      if there are none.
    """

    # STEP 1: Build the URL for the "get order list" endpoint.
    path = "/api/v2/order/get_order_list"
    url = _build_request_url(path)

    # STEP 2: Look back 7 days to catch any orders we might have missed.
    # The state file ensures we never re-process them.
    seven_days_ago = int(time.time()) - (7 * 24 * 60 * 60)
    now = int(time.time())

    params = {
        "time_range_field": "create_time",
        "time_from": seven_days_ago,
        "time_to": now,
        "page_size": 100,
        "order_status": order_status,
    }

    # STEP 3: Make the call.
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    # STEP 4: Tag each summary with the status we asked for, since
    # Shopee does not always echo it back in the response.
    summaries = data.get("response", {}).get("order_list", [])
    for s in summaries:
        s["order_status"] = order_status

    return summaries


def _get_order_details(order_sns):
    """
    Fetches full details for a list of order numbers.

    Shopee's get_order_list only returns IDs, so we need a second call
    to get the actual recipient name, items, and courier.
    """

    # STEP 1: Build URL for the "get order detail" endpoint.
    path = "/api/v2/order/get_order_detail"
    url = _build_request_url(path)

    # STEP 2: Ask for the specific fields we need for the Telegram caption,
    # plus order_status so main.py can decide what to do with each order.
    params = {
        "order_sn_list": ",".join(order_sns),
        "response_optional_fields": "recipient_address,item_list,buyer_username,order_status",
    }

    # STEP 3: Make the call and return the order list.
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    return data.get("response", {}).get("order_list", [])


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
