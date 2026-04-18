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


def build_caption(order):
    """
    Builds a human-readable caption for a single order.

    The caption appears below the label image in Telegram and helps the
    employee match the printed label with the right items in the warehouse.

    Args:
      order: the order dict returned by shopee_client.

    Returns:
      A formatted string ready to use as a Telegram caption.
    """

    # STEP 1: Pull out the fields we want to show.
    order_sn = order.get("order_sn", "?")
    recipient = order.get("recipient_address", {}).get("name", "?")
    courier = order.get("shipping_carrier", "?")

    # STEP 2: Build a short summary of items in the order.
    items = order.get("item_list", [])
    item_lines = []
    for item in items:
        name = item.get("item_name", "?")
        qty = item.get("model_quantity_purchased", 1)
        item_lines.append(f"  • {qty}x {name}")
    items_text = "\n".join(item_lines) if item_lines else "  (no items)"

    # STEP 3: Assemble the caption. We keep it short so it fits well on mobile.
    caption = (
        f"📦 Order: {order_sn}\n"
        f"👤 To: {recipient}\n"
        f"🚚 Courier: {courier}\n"
        f"\n"
        f"Items:\n"
        f"{items_text}"
    )
    return caption
