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
  - get_package_detail(order_sn, package_number) -> dict (raw Shopee response)
  - ship_order_to_dropoff(order_sn) -> None (raises on error)
  - get_shipping_label_pdf(order_sn) -> bytes (or None if not ready yet)
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


def _check_shopee_json_ok(response, context):
    """Raises RuntimeError if Shopee returns an API-level error inside HTTP 200."""
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f"{context}: Shopee returned non-JSON response")

    error = data.get("error")
    if error:
        message = data.get("message", "")
        raise RuntimeError(f"{context}: {error} - {message}")

    return data


def _raise_result_list_errors(data, context):
    """Raises if Shopee reports per-order failure inside response.result_list."""
    result_list = data.get("response", {}).get("result_list", [])
    for result in result_list:
        fail_error = result.get("fail_error")
        if fail_error:
            fail_message = result.get("fail_message", "")
            raise RuntimeError(f"{context}: {fail_error} - {fail_message}")


def _looks_like_label_not_ready(data):
    """Best-effort check for Shopee JSON responses that mean the PDF is not ready yet."""
    text = " ".join(
        str(value).lower()
        for value in (
            data.get("error", ""),
            data.get("message", ""),
            data.get("debug_message", ""),
        )
    )

    not_ready_markers = (
        "not ready",
        "not generated",
        "generating",
        "processing",
        "document not exist",
        "document does not exist",
        "shipping document not exist",
        "shipping_document_not_exist",
    )
    return any(marker in text for marker in not_ready_markers)


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
      can decide whether to ship_order or just fetch the label. Each dict
      also includes package_list so main.py can pick a package_number to
      pass to get_package_detail() before calling ship_order_to_dropoff.
    """

    # STEP 1: Shopee's get_order_list only accepts one status per call,
    # so we make two calls and combine the results.
    ready_to_ship = _get_order_summaries_by_status("READY_TO_SHIP")
    processed = _get_order_summaries_by_status("PROCESSED")
    all_summaries = ready_to_ship + processed

    if not all_summaries:
        return []

    # STEP 2: De-duplicate by order_sn while preserving READY_TO_SHIP priority.
    # If Shopee returns the same order during a status transition, we keep the
    # first status we saw. READY_TO_SHIP is fetched first because it still needs
    # ship_order_to_dropoff() before the label can be generated.
    status_by_sn = {}
    for summary in all_summaries:
        order_sn = summary["order_sn"]
        if order_sn not in status_by_sn:
            status_by_sn[order_sn] = summary.get("order_status")

    order_sns = list(status_by_sn.keys())

    details = _get_order_details(order_sns)

    # STEP 3: Attach the status from the summary to each detail dict so
    # main.py can decide what to do per order.
    for d in details:
        if not d.get("order_status"):
            d["order_status"] = status_by_sn.get(d["order_sn"], "UNKNOWN")

    return details


def get_package_detail(order_sn, package_number):
    """
    Fetches v2.order.get_package_detail for a single package.

    Used as a pre-check before ship_order_to_dropoff so we only call
    v2.logistics.ship_order when the package is actually in
    fulfillment_status=LOGISTICS_READY and is_shipment_arranged=false.
    This is the migration Shopee Open Platform recommends in their
    "Improve API Call Success Rate for v2.logistics.ship_order" task:
    the order-level status (READY_TO_SHIP) is too coarse, and calling
    ship_order on a package that is still allocating or already
    arranged counts as a failed API call against the >90% daily
    success rate target.

    Args:
      order_sn: the Shopee order number string.
      package_number: the package_number from order.package_list[].

    Returns:
      The raw Shopee JSON dict. response.fulfillment_status and
      response.is_shipment_arranged are the fields the caller checks.

    Raises:
      RuntimeError if Shopee returns an API-level error. The caller
      treats any exception here as "not ready" and retries next run.
    """

    path = "/api/v2/order/get_package_detail"
    url = _build_request_url(path)

    response = requests.get(
        url,
        params={"order_sn": order_sn, "package_number_list": package_number},
        timeout=30,
    )
    return _check_shopee_json_ok(
        response, context=f"get_package_detail {order_sn}/{package_number}"
    )


def ship_order_to_dropoff(order_sn):
    """
    Tells Shopee that the seller will drop off the package at the courier
    counter. This is the API equivalent of clicking
    "Atur Pengiriman" -> "Antar ke Counter" in the Shopee Seller app.

    After this call succeeds, the order moves from READY_TO_SHIP to
    PROCESSED, and Shopee starts generating the shipping label.

    Callers must verify package readiness via get_package_detail() first
    so this call only fires when fulfillment_status=LOGISTICS_READY and
    is_shipment_arranged=false. See main._is_ready_to_ship.

    Args:
      order_sn: the Shopee order number string.

    Raises:
      RuntimeError if Shopee rejects the request.
    """

    # STEP 1: Build the URL for the ship_order endpoint.
    path = "/api/v2/logistics/ship_order"
    url = _build_request_url(path)

    # STEP 2: Build the request body. We always use dropoff because that
    # matches how the warehouse operates (employee carries packages to the
    # courier counter at the end of day).
    body = {
        "order_sn": order_sn,
        "dropoff": {},  # Empty dict means "use the default dropoff option"
    }

    # STEP 3: Make the call. Any error will raise an exception and bubble up
    # to main.py, which decides whether to retry or skip.
    response = requests.post(url, json=body, timeout=30)
    data = _check_shopee_json_ok(response, context=f"ship_order {order_sn}")
    _raise_result_list_errors(data, context=f"ship_order {order_sn}")


def get_shipping_label_pdf(order_sn):
    """
    Fetches the shipping label PDF for a single order.

    The Shopee flow has four steps that must happen in order:
      1. Get the suggested document type (THERMAL_AIR_WAYBILL or NORMAL).
      2. Get the tracking number Shopee assigned for this order.
      3. Tell Shopee to generate the document, passing both the type and
         the tracking number explicitly.
      4. Wait briefly, then download the generated PDF.

    Steps 1 and 2 are required because Shopee rejects create_shipping_document
    if the tracking number is missing or if the document type does not match
    what the order needs. Calling them in this order matches what the Shopee
    Seller App does internally when you tap "Print Label."

    Sometimes the label is not ready right away in step 4, so we retry up to
    3 times within this single function call. If it is still not ready after
    that, we return None and let the next scheduled run try again.

    Args:
      order_sn: the Shopee order number string.

    Returns:
      PDF file contents as bytes, OR None if the label is not ready yet.
    """

    # STEP 1: Get the suggested document type for this order. Different
    # orders need different types (THERMAL_AIR_WAYBILL vs NORMAL_AIR_WAYBILL)
    # depending on the courier and order configuration.
    document_type = _get_suggested_document_type(order_sn)

    # STEP 2: Get the tracking number Shopee assigned to this order.
    # create_shipping_document requires this to be passed explicitly,
    # otherwise Shopee returns "tracking_number_invalid".
    tracking_number = _get_tracking_number(order_sn)
    if not tracking_number:
        print(f"  Tracking number for {order_sn} is not ready yet, will retry next run")
        return None

    # STEP 3: Ask Shopee to generate the shipping document.
    _create_shipping_document(order_sn, document_type, tracking_number)

    # STEP 4: Try to download the PDF, with short retries for "not ready yet".
    for attempt in range(3):
        # Wait a bit before each attempt. Shopee usually takes a few seconds.
        time.sleep(5)

        pdf_bytes = _download_shipping_document(order_sn, document_type)
        if pdf_bytes is not None:
            return pdf_bytes

        print(f"  Label for {order_sn} not ready yet, attempt {attempt + 1}/3")

    # STEP 5: Give up for this run. The next scheduled run will try again.
    print(f"  Label for {order_sn} still not ready, will retry next run")
    return None


# ============================================================
# More internal helpers (used only by the public functions above)
# ============================================================

def _get_order_summaries_by_status(order_status):
    """
    Fetches all order summaries (order_sn + status only) for the given status.
    The full details come from a separate call.

    Args:
      order_status: one of READY_TO_SHIP, PROCESSED, etc.

    Returns:
      A list of {"order_sn": ..., "order_status": ...} dicts. Empty list
      if there are none.
    """

    # STEP 1: Build the URL for the "get order list" endpoint.
    path = "/api/v2/order/get_order_list"
    url = _build_request_url(path)

    # STEP 2: Look back only within the airway-bill validity window.
    # STATE_RETENTION_DAYS intentionally stays at 3 because labels are no
    # longer useful after that window. Keeping the API lookback aligned with
    # state retention avoids retrying old PROCESSED orders after state pruning.
    lookback_seconds = config.STATE_RETENTION_DAYS * 24 * 60 * 60
    time_from = int(time.time()) - lookback_seconds
    now = int(time.time())

    all_summaries = []
    cursor = ""

    while True:
        params = {
            "time_range_field": "create_time",
            "time_from": time_from,
            "time_to": now,
            "page_size": 100,
            "order_status": order_status,
        }

        if cursor:
            params["cursor"] = cursor

        # STEP 3: Make the call.
        response = requests.get(url, params=params, timeout=30)
        data = _check_shopee_json_ok(response, context=f"get_order_list {order_status}")

        response_data = data.get("response", {})
        summaries = response_data.get("order_list", [])

        # STEP 4: Tag each summary with the status we asked for, since
        # Shopee does not always echo it back in the response.
        for s in summaries:
            s["order_status"] = order_status

        all_summaries.extend(summaries)

        has_next = response_data.get("more", False)
        cursor = response_data.get("next_cursor", "")

        if not has_next or not cursor:
            break

        time.sleep(0.3)

    return all_summaries


def _get_order_details(order_sns):
    """
    Fetches full details for a list of order numbers.

    Shopee's get_order_list only returns IDs, so we need a second call
    to get the actual recipient name, items, and courier.
    """

    all_details = []

    for batch_start in range(0, len(order_sns), 50):
        batch = order_sns[batch_start:batch_start + 50]

        # STEP 1: Build URL for the "get order detail" endpoint.
        path = "/api/v2/order/get_order_detail"
        url = _build_request_url(path)

        # STEP 2: Ask for the specific fields we need for the Telegram caption,
        # plus order_status so main.py can decide what to do with each order,
        # plus package_list so main.py can pick a package_number to pre-check
        # with get_package_detail before calling ship_order_to_dropoff.
        # We do NOT request recipient_address because Shopee masks it anyway,
        # and the unmasked details are visible on the printed label.
        params = {
            "order_sn_list": ",".join(batch),
            "response_optional_fields": (
                "item_list,order_status,shipping_carrier,package_list"
            ),
        }

        # STEP 3: Make the call and return the order list.
        response = requests.get(url, params=params, timeout=30)
        data = _check_shopee_json_ok(response, context="get_order_detail")

        all_details.extend(data.get("response", {}).get("order_list", []))

        if batch_start + 50 < len(order_sns):
            time.sleep(0.3)

    return all_details


def _get_suggested_document_type(order_sn):
    """
    Asks Shopee which shipping_document_type to use for this order.

    Shopee returns a list of selectable types and one suggested type.
    We always use the suggested one, since that is what the order's
    courier expects (THERMAL_AIR_WAYBILL for some, NORMAL_AIR_WAYBILL
    for others).
    """

    path = "/api/v2/logistics/get_shipping_document_parameter"
    url = _build_request_url(path)

    body = {"order_list": [{"order_sn": order_sn}]}
    response = requests.post(url, json=body, timeout=30)
    data = _check_shopee_json_ok(
        response,
        context=f"get_shipping_document_parameter {order_sn}",
    )
    _raise_result_list_errors(
        data,
        context=f"get_shipping_document_parameter {order_sn}",
    )

    result_list = data.get("response", {}).get("result_list", [])
    if not result_list:
        # Fall back to the most common type if Shopee did not give us one.
        return "THERMAL_AIR_WAYBILL"

    return result_list[0].get("suggest_shipping_document_type", "THERMAL_AIR_WAYBILL")


def _get_tracking_number(order_sn):
    """
    Fetches the tracking number Shopee assigned to this order.

    create_shipping_document requires this to be passed explicitly,
    otherwise Shopee returns "tracking_number_invalid". The tracking
    number was assigned during ship_order or by the Shopee Seller App.
    """

    path = "/api/v2/logistics/get_tracking_number"
    url = _build_request_url(path)

    response = requests.get(url, params={"order_sn": order_sn}, timeout=30)
    data = _check_shopee_json_ok(response, context=f"get_tracking_number {order_sn}")

    return data.get("response", {}).get("tracking_number", "")


def _create_shipping_document(order_sn, document_type, tracking_number):
    """
    Tells Shopee to start generating the shipping document for an order.
    This call returns quickly but the actual PDF takes a few seconds to generate.

    Both document_type and tracking_number must be passed for Shopee to accept
    the request. See get_shipping_label_pdf for why.
    """

    path = "/api/v2/logistics/create_shipping_document"
    url = _build_request_url(path)

    body = {
        "order_list": [
            {
                "order_sn": order_sn,
                "tracking_number": tracking_number,
                "shipping_document_type": document_type,
            }
        ],
    }

    response = requests.post(url, json=body, timeout=30)
    data = _check_shopee_json_ok(response, context=f"create_shipping_document {order_sn}")
    _raise_result_list_errors(data, context=f"create_shipping_document {order_sn}")


def _download_shipping_document(order_sn, document_type):
    """
    Downloads the generated shipping document PDF.

    The document_type must match the one used in create_shipping_document.

    Returns:
      bytes if the PDF is ready, or None if Shopee says it is still generating.
    """

    path = "/api/v2/logistics/download_shipping_document"
    url = _build_request_url(path)

    body = {
        "order_list": [
            {
                "order_sn": order_sn,
                "shipping_document_type": document_type,
            }
        ],
    }

    response = requests.post(url, json=body, timeout=30)
    response.raise_for_status()

    # Shopee returns the PDF directly as the response body if it is ready.
    # If not ready, it returns a JSON error response instead.
    content_type = response.headers.get("content-type", "")
    if "application/pdf" in content_type or response.content.startswith(b"%PDF"):
        return response.content

    try:
        data = response.json()
    except ValueError:
        return None

    if data.get("error"):
        if _looks_like_label_not_ready(data):
            return None

        raise RuntimeError(
            f"download_shipping_document {order_sn}: "
            f"{data.get('error')} - {data.get('message', '')}"
        )

    return None
