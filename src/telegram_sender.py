"""
telegram_sender.py
------------------
Sends shipping label images to the Telegram bot.

Why this file exists:
  The employee receives notifications via Telegram and prints the labels from
  there. This module is the only place that knows about the Telegram API.

Public functions (used by main.py):
  - send_label(png_bytes, caption) -> True if delivered, False otherwise
  - build_caption(order) -> formatted string with order info
"""

import requests

from src import config


# The Telegram Bot API base URL. We compose the full endpoint from this.
_TELEGRAM_API_URL = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def send_label(png_bytes, caption):
    """
    Sends a single label image to the Telegram chat.

    Args:
      png_bytes: the image contents as bytes.
      caption: a string shown below the image (order info for the employee).

    Returns:
      True if Telegram confirmed delivery, False if anything went wrong.

    Why we return a bool instead of raising an exception:
      main.py uses this return value to decide whether to mark the order
      as processed. If we raised an exception, main.py would have to wrap
      every call in try/except, which is noisier than just checking a bool.
    """

    # STEP 1: Build the URL for the sendPhoto endpoint.
    url = f"{_TELEGRAM_API_URL}/sendPhoto"

    # STEP 2: Prepare the multipart form data.
    # Telegram accepts the image as a file upload via the "photo" field.
    files = {
        "photo": ("label.png", png_bytes, "image/png"),
    }
    data = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "caption": caption,
    }

    # STEP 3: Send the request. We catch errors here so we can return False
    # instead of letting an exception bubble up to main.py.
    try:
        response = requests.post(url, files=files, data=data, timeout=30)
    except requests.RequestException as e:
        print(f"  Telegram request failed: {e}")
        return False

    # STEP 4: Check the response. Telegram returns {"ok": true, ...} on success.
    if response.status_code != 200:
        print(f"  Telegram returned status {response.status_code}: {response.text}")
        return False

    response_json = response.json()
    if not response_json.get("ok"):
        print(f"  Telegram rejected the message: {response_json}")
        return False

    # STEP 5: Delivery confirmed.
    return True


def send_summary(text):
    """
    Sends a plain text status message to the Telegram chat.

    Used at the end of every run as a "heartbeat" so the employee knows the
    bot is alive, even when there are zero new orders to process.

    Args:
      text: the message to send (plain text, no images).

    Returns:
      True if delivered, False otherwise. We do not retry on failure
      because the next scheduled run will send another summary anyway.
    """

    # STEP 1: Build the URL for the sendMessage endpoint.
    # Note: this is a different endpoint than sendPhoto.
    url = f"{_TELEGRAM_API_URL}/sendMessage"

    # STEP 2: Prepare the request body.
    data = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
    }

    # STEP 3: Send the request.
    try:
        response = requests.post(url, data=data, timeout=30)
    except requests.RequestException as e:
        print(f"  Telegram summary failed: {e}")
        return False

    # STEP 4: Check the response.
    if response.status_code != 200:
        print(f"  Telegram returned status {response.status_code}: {response.text}")
        return False

    return True


def build_caption(order):
    """
    Builds a human-readable caption for a single order, in Bahasa Indonesia.

    The caption appears below the label image in Telegram and helps the
    employee match the printed label with the right items in the warehouse.

    We show the SKU instead of the product name because product names on
    Shopee are very long (e.g. "ITBisa - Socket IC DIP 16 Pin 2.54mm
    Narrow 2x8 Lubang 8x2 Dudukan IC DIP16 untuk PCB Arduino & Project
    Elektronika"), but warehouse shelves are organized by SKU. SKU is
    short, precise, and matches how items are physically labeled.

    Args:
      order: the order dict returned by shopee_client.

    Returns:
      A formatted string ready to use as a Telegram caption.
    """

    # STEP 1: Pull out the fields we want to show.
    order_sn = order.get("order_sn", "?")
    recipient = order.get("recipient_address", {}).get("name", "?")
    courier = order.get("shipping_carrier", "?")

    # STEP 2: Build a short summary of items using SKUs.
    items = order.get("item_list", [])
    item_lines = []
    for item in items:
        qty = item.get("model_quantity_purchased", 1)
        sku = _pick_sku(item)
        item_lines.append(f"  • {qty} x {sku}")
    items_text = "\n".join(item_lines) if item_lines else "  (tidak ada barang)"

    # STEP 3: Assemble the caption in Bahasa Indonesia.
    caption = (
        f"📦 No. Pesanan: {order_sn}\n"
        f"👤 Penerima: {recipient}\n"
        f"🚚 Kurir: {courier}\n"
        f"\n"
        f"Barang:\n"
        f"{items_text}"
    )
    return caption


def _pick_sku(item):
    """
    Returns the most specific SKU for a Shopee order item.

    Shopee's item_list entries may include both:
      - item_sku:  the parent product SKU ("SKU Utama")
      - model_sku: the variant SKU when the product has variants ("SKU Varian")

    The rule: if the variant SKU exists, use it. Otherwise fall back to the
    main SKU. This matches how the warehouse organizes stock, where variants
    like colors and sizes each have their own shelf location.

    Example:
      - LED 5mm has no variants -> model_sku is empty, use item_sku ITBISA-LED-5MM.
      - LED 5mm Red is a variant -> use model_sku ITBISA-LED-5MM-RED.

    If both SKUs are missing (seller forgot to assign them), we fall back to
    the product name. A long name is still more useful to the employee than
    a cryptic placeholder, because it at least tells them what the item is.
    """

    # STEP 1: Prefer the variant SKU when it is present and non-empty.
    model_sku = item.get("model_sku", "").strip()
    if model_sku:
        return model_sku

    # STEP 2: Fall back to the main product SKU.
    item_sku = item.get("item_sku", "").strip()
    if item_sku:
        return item_sku

    # STEP 3: Last resort - use the product name. Still useful for identification
    # even if it is long, and better than a blank or cryptic placeholder.
    return item.get("item_name", "(tidak ada nama)")


def build_summary(time_hhmm, success_count, skipped_count):
    """
    Builds the heartbeat summary message in Bahasa Indonesia.

    Three patterns based on what happened during the run:
      - 0 orders:   "✅ 11:00 - Tidak ada pesanan baru"
      - All sent:   "✅ 12:00 - 3 label terkirim"
      - Some failed: "⚠️ 13:00 - 2 terkirim, 1 gagal (akan dicoba lagi)"

    Args:
      time_hhmm: current Jakarta time as "HH:MM" string.
      success_count: number of labels successfully sent this run.
      skipped_count: number of orders that failed and will retry next run.

    Returns:
      A formatted string ready to send via send_summary().
    """

    # STEP 1: No new orders this run.
    if success_count == 0 and skipped_count == 0:
        return f"✅ {time_hhmm} - Tidak ada pesanan baru"

    # STEP 2: Everything was processed successfully.
    if skipped_count == 0:
        return f"✅ {time_hhmm} - {success_count} label terkirim"

    # STEP 3: Some orders failed. Use a warning emoji so the employee notices.
    return (
        f"⚠️ {time_hhmm} - {success_count} terkirim, "
        f"{skipped_count} gagal (akan dicoba lagi)"
    )


def build_safety_stop_message(time_hhmm, order_count, max_allowed):
    """
    Builds the alert message for when there are suspiciously many orders.

    This message uses stronger language because it requires human attention.

    Args:
      time_hhmm: current Jakarta time as "HH:MM" string.
      order_count: how many new orders we saw.
      max_allowed: the safety cap from config.

    Returns:
      A formatted alert string.
    """
    return (
        f"⚠️ {time_hhmm} - PERINGATAN: {order_count} pesanan baru "
        f"melebihi batas {max_allowed}. Mohon dicek dahulu sebelum diproses."
    )
