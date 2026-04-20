"""
update_inventory.py
-------------------
Updates Shopee inventory from an Excel file.

What this script does:
  1. Reads an Excel file with two columns: SKU and Stock.
  2. Fetches all your products from Shopee, including their variants.
  3. Builds a lookup map: SKU -> (item_id, model_id or None).
  4. For each SKU in your Excel, updates the stock on Shopee.
  5. Prints a summary of what succeeded, failed, or was skipped.

Excel format expected:
  - First row: headers "SKU" and "Stock" (column order matters)
  - Subsequent rows: one product per row
  Example:
    SKU                              | Stock
    ITBISA-SOCKET-IC-DIP16-NARROW    | 50
    ITBISA-LED-SUPERBRIGHT-5MM-RED   | 100

Usage:
  python scripts/update_inventory.py path/to/inventory.xlsx

Important notes:
  - If a SKU in your Excel is not found on Shopee, it is skipped with a
    warning. The run continues for other SKUs.
  - If Shopee returns an error for a specific SKU, it is skipped with a
    warning. Other SKUs continue.
  - Shopee rate limits stock updates. The script adds a small delay
    between calls to stay polite. A 500-SKU run takes roughly 10 minutes.
"""

import hashlib
import hmac
import sys
import time
from pathlib import Path

import openpyxl
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config, shopee_auth


# How long to wait between API calls to stay polite and avoid rate limits.
# Shopee's documented limit is roughly 1 request per second per shop.
_DELAY_BETWEEN_CALLS_SECONDS = 1.0


def main():
    """Entry point: read Excel, fetch mappings, update each SKU."""

    if len(sys.argv) < 2:
        print("Usage: python scripts/update_inventory.py path/to/inventory.xlsx")
        sys.exit(1)

    excel_path = Path(sys.argv[1])
    if not excel_path.exists():
        print(f"✗ File not found: {excel_path}")
        sys.exit(1)

    print("=" * 60)
    print("Shopee Inventory Update")
    print("=" * 60)
    print(f"Environment: {config.SHOPEE_API_BASE_URL}")
    print(f"Partner ID:  {config.SHOPEE_PARTNER_ID}")
    print(f"Shop ID:     {config.SHOPEE_SHOP_ID}")
    print(f"Excel file:  {excel_path}")
    print()

    # STEP 1: Read the Excel file into a dict of SKU -> desired stock.
    print("[1/4] Reading Excel file...")
    desired_stock = _read_excel(excel_path)
    print(f"  Found {len(desired_stock)} SKUs to process")
    print()

    # STEP 2: Fetch all products from Shopee and build the SKU -> id mapping.
    # This is the slowest step because it may make dozens of API calls for
    # shops with many products. We print progress so it does not look frozen.
    print("[2/4] Fetching product catalog from Shopee...")
    sku_to_ids = _build_sku_mapping()
    print(f"  Mapped {len(sku_to_ids)} SKUs from Shopee catalog")
    print()

    # STEP 3: Match each Excel SKU to its Shopee ids.
    print("[3/4] Matching Excel SKUs to Shopee products...")
    matched = []
    missing = []
    for sku, stock in desired_stock.items():
        if sku in sku_to_ids:
            item_id, model_id = sku_to_ids[sku]
            matched.append((sku, stock, item_id, model_id))
        else:
            missing.append(sku)

    print(f"  Matched: {len(matched)}")
    print(f"  Missing: {len(missing)}")
    if missing:
        print("  These SKUs will be skipped (not found on Shopee):")
        for sku in missing:
            print(f"    - {sku}")
    print()

    if not matched:
        print("Nothing to update. Exiting.")
        return

    # STEP 4: Update each matched SKU's stock on Shopee.
    print(f"[4/4] Updating stock for {len(matched)} products...")
    success_count = 0
    failed = []
    for sku, stock, item_id, model_id in matched:
        try:
            _update_stock(item_id, model_id, stock)
            success_count += 1
            print(f"  ✓ {sku}: set stock to {stock}")
        except Exception as e:
            failed.append((sku, str(e)))
            print(f"  ✗ {sku}: failed - {e}")

        # Be polite to Shopee's rate limiter.
        time.sleep(_DELAY_BETWEEN_CALLS_SECONDS)

    print()
    print("=" * 60)
    print(f"Done. {success_count} updated, {len(failed)} failed, {len(missing)} skipped.")
    print("=" * 60)

    if failed:
        print("\nFailed SKUs (check these manually):")
        for sku, err in failed:
            print(f"  - {sku}: {err}")


# ============================================================
# Excel reading
# ============================================================

def _read_excel(path):
    """
    Reads the Excel file and returns a dict of SKU -> stock.

    Expects the first row to be headers and the first two columns to
    contain SKU and Stock. Any row where SKU is empty is skipped.
    """

    workbook = openpyxl.load_workbook(path, data_only=True)
    sheet = workbook.active

    # STEP 1: Build the mapping, skipping the header row.
    result = {}
    for row_num, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if not row or len(row) < 2:
            continue

        sku_raw = row[0]
        stock_raw = row[1]

        # Skip rows where SKU is empty.
        if sku_raw is None or str(sku_raw).strip() == "":
            continue

        sku = str(sku_raw).strip()

        # Stock must be a number. Warn and skip rows where it is not.
        try:
            stock = int(stock_raw)
        except (TypeError, ValueError):
            print(f"  Row {row_num}: invalid stock value '{stock_raw}' for SKU '{sku}', skipping")
            continue

        if stock < 0:
            print(f"  Row {row_num}: negative stock '{stock}' for SKU '{sku}', skipping")
            continue

        result[sku] = stock

    return result


# ============================================================
# Shopee product catalog fetching
# ============================================================

def _build_sku_mapping():
    """
    Fetches all products from Shopee and returns a dict of
    SKU -> (item_id, model_id or None).

    For products without variants, model_id is None.
    For products with variants, each variant gets its own entry mapped
    to (item_id, model_id).
    """

    # STEP 1: Get the full list of item_ids in this shop.
    all_item_ids = _fetch_all_item_ids()
    print(f"  Found {len(all_item_ids)} products total")

    if not all_item_ids:
        return {}

    # STEP 2: Fetch base info for each item in batches of 50 (Shopee's limit).
    # This gives us the parent SKU.
    sku_to_ids = {}
    for batch_start in range(0, len(all_item_ids), 50):
        batch = all_item_ids[batch_start:batch_start + 50]
        items = _fetch_item_base_info(batch)
        for item in items:
            item_id = item["item_id"]
            has_models = item.get("has_model", False)
            parent_sku = (item.get("item_sku") or "").strip()

            # If the item has no variants, the parent SKU maps directly to item_id.
            if not has_models and parent_sku:
                sku_to_ids[parent_sku] = (item_id, None)

            # If the item has variants, we need to fetch each variant's SKU separately.
            if has_models:
                variant_skus = _fetch_model_skus(item_id)
                for model_id, model_sku in variant_skus:
                    if model_sku:
                        sku_to_ids[model_sku] = (item_id, model_id)

        # Small delay between batches to be polite.
        time.sleep(_DELAY_BETWEEN_CALLS_SECONDS)

    return sku_to_ids


def _fetch_all_item_ids():
    """
    Fetches all item_ids in the shop using paginated get_item_list.
    Returns a flat list of item_ids.
    """

    path = "/api/v2/product/get_item_list"
    all_ids = []
    offset = 0
    page_size = 100  # Shopee's max

    while True:
        params = {
            "offset": offset,
            "page_size": page_size,
            "item_status": "NORMAL",
        }
        data = _call_shopee_get(path, params)
        items = data.get("response", {}).get("item", [])
        for item in items:
            all_ids.append(item["item_id"])

        has_next = data.get("response", {}).get("has_next_page", False)
        if not has_next:
            break
        offset += page_size

    return all_ids


def _fetch_item_base_info(item_ids):
    """
    Fetches base info for up to 50 items at once. Returns the raw item
    dicts, each containing item_id, item_sku, has_model, etc.
    """

    path = "/api/v2/product/get_item_base_info"
    params = {"item_id_list": ",".join(str(i) for i in item_ids)}
    data = _call_shopee_get(path, params)
    return data.get("response", {}).get("item_list", [])


def _fetch_model_skus(item_id):
    """
    For an item with variants, fetches the list of (model_id, model_sku)
    tuples for each variant.
    """

    path = "/api/v2/product/get_model_list"
    params = {"item_id": item_id}
    data = _call_shopee_get(path, params)
    models = data.get("response", {}).get("model", [])
    return [(m["model_id"], (m.get("model_sku") or "").strip()) for m in models]


# ============================================================
# Stock update
# ============================================================

def _update_stock(item_id, model_id, new_stock):
    """
    Updates the stock on Shopee for a given item or variant.

    For items without variants (model_id is None), we still use the same
    endpoint but with an empty model_id, which Shopee treats as updating
    the item-level stock.

    Args:
      item_id: Shopee's numeric item_id.
      model_id: Shopee's numeric model_id, or None for non-variant products.
      new_stock: the absolute stock count to set.

    Raises:
      RuntimeError if Shopee returns an error for this specific item.
    """

    path = "/api/v2/product/update_stock"

    stock_info = {
        "seller_stock": [{"stock": new_stock}],
    }
    if model_id is not None:
        stock_info["model_id"] = model_id

    body = {
        "item_id": item_id,
        "stock_list": [stock_info],
    }

    data = _call_shopee_post(path, body)

    # Shopee's update_stock returns a result_list with per-item status.
    # Check for errors in the result.
    if data.get("error"):
        raise RuntimeError(f"{data.get('error')}: {data.get('message')}")

    result_list = data.get("response", {}).get("result_list", [])
    for r in result_list:
        if r.get("fail_error"):
            raise RuntimeError(
                f"{r.get('fail_error')}: {r.get('fail_message')}"
            )


# ============================================================
# Signed API call helpers
# ============================================================

def _call_shopee_get(path, params):
    """Signed GET returning parsed JSON."""
    url = _build_signed_url(path)
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def _call_shopee_post(path, body):
    """Signed POST returning parsed JSON."""
    url = _build_signed_url(path)
    response = requests.post(url, json=body, timeout=30)
    response.raise_for_status()
    return response.json()


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
