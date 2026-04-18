"""
shopee_client_fake.py
---------------------
Fake replacement for shopee_client.py. Used only during local development
when the real Shopee sandbox is not accessible.

Why this file exists:
  We want to test the full pipeline (fetch orders -> get label -> convert to
  PNG -> send to Telegram) without depending on the real Shopee API. This
  file provides the same public functions as shopee_client.py, but returns
  canned data and generates dummy PDF labels in memory.

How to use it:
  Set USE_FAKE_SHOPEE=true in your .env file. The dispatcher in
  shopee_client.py will route calls here automatically.

When you no longer need this file:
  Delete it and remove the if-check in shopee_client.py. No other code
  needs to change.
"""

import io
from datetime import datetime, timezone

from reportlab.lib.pagesizes import A6
from reportlab.pdfgen import canvas


# ============================================================
# Canned fake orders.
# Order #1 matches the screenshot Aji shared. The other two are variants
# so we can test how the bot handles multiple orders in one run.
# ============================================================

_FAKE_ORDERS = [
    {
        "order_sn": "2604186D4MY0Y0",
        "shipping_carrier": "SPX Hemat",
        "recipient_address": {
            "name": "d*****j",
            "phone": "******48",
            "full_address": "****** 20, RT.14/RW.9, Pd. Bambu, Duren Sawit, "
                            "KOTA JAKARTA TIMUR, DUREN SAWIT, DKI JAKARTA, ID, 13430",
        },
        "item_list": [
            {
                "item_name": "ITBisa - Socket IC DIP 16 Pin 2.54mm Narrow 2x8 "
                             "Lubang 8x2 Dudukan IC DIP16 untuk PCB Arduino & "
                             "Project Elektronika",
                "model_quantity_purchased": 10,
            }
        ],
    },
    {
        "order_sn": "2604186D4MY0Y1",
        "shipping_carrier": "JNE REG",
        "recipient_address": {
            "name": "b*****o",
            "phone": "******12",
            "full_address": "Jl. Sudirman No. 45, Bandung, JAWA BARAT, ID, 40115",
        },
        "item_list": [
            {
                "item_name": "ITBisa - Kabel HDMI 2 Meter High Speed",
                "model_quantity_purchased": 2,
            },
            {
                "item_name": "ITBisa - Adaptor USB-C 65W Fast Charging",
                "model_quantity_purchased": 1,
            },
        ],
    },
    {
        "order_sn": "2604186D4MY0Y2",
        "shipping_carrier": "J&T Express",
        "recipient_address": {
            "name": "s****a",
            "phone": "******99",
            "full_address": "Jl. Diponegoro No. 12, Surabaya, JAWA TIMUR, ID, 60241",
        },
        "item_list": [
            {
                "item_name": "ITBisa - Resistor Pack 1/4W 1% Toleransi 100 Nilai",
                "model_quantity_purchased": 1,
            }
        ],
    },
]


# ============================================================
# Public functions (same signatures as shopee_client.py)
# ============================================================

def get_ready_to_ship_orders():
    """Returns the canned list of fake orders."""

    # STEP 1: Print a clear marker so we never confuse fake runs with real ones.
    print("  [FAKE MODE] Returning 3 canned fake orders")

    # STEP 2: Return a copy of the list so callers cannot accidentally mutate it.
    return [dict(order) for order in _FAKE_ORDERS]


def get_shipping_label_pdf(order_sn):
    """
    Generates a dummy PDF that looks roughly like a shipping label.

    The PDF is created in memory using reportlab. It contains the order
    number, recipient info, and items, formatted to fit on an A6 page
    (which is the standard label size in Indonesia).

    Args:
      order_sn: the fake order number to look up.

    Returns:
      PDF file contents as bytes, or None if the order_sn is not in our
      fake data (which would indicate a bug in the test setup).
    """

    # STEP 1: Look up the order in our canned data.
    order = next((o for o in _FAKE_ORDERS if o["order_sn"] == order_sn), None)
    if order is None:
        print(f"  [FAKE MODE] Unknown order_sn: {order_sn}")
        return None

    print(f"  [FAKE MODE] Generating dummy PDF for {order_sn}")

    # STEP 2: Create an in-memory PDF using reportlab.
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A6)
    width, height = A6  # A6 is roughly 105mm x 148mm

    # STEP 3: Draw a header bar at the top.
    pdf.setFillColorRGB(0.93, 0.30, 0.18)  # Shopee orange
    pdf.rect(0, height - 40, width, 40, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(15, height - 27, "ITBisa - Shipping Label (FAKE)")

    # STEP 4: Draw order details.
    pdf.setFillColorRGB(0, 0, 0)
    y = height - 65
    line_height = 14

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(15, y, "No. Pesanan:")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(15, y - line_height, order["order_sn"])
    y -= line_height * 2 + 8

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(15, y, "Penerima:")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(15, y - line_height, order["recipient_address"]["name"])
    y -= line_height * 2 + 8

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(15, y, "Alamat:")
    pdf.setFont("Helvetica", 8)
    # STEP 5: Wrap the address across multiple lines because it is long.
    address = order["recipient_address"]["full_address"]
    wrapped_lines = _wrap_text(address, max_chars=42)
    for line in wrapped_lines:
        y -= line_height - 2
        pdf.drawString(15, y, line)
    y -= line_height + 8

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(15, y, "Kurir:")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(15, y - line_height, order["shipping_carrier"])
    y -= line_height * 2 + 8

    # STEP 6: Draw a fake barcode area (just a black rectangle for now).
    pdf.setFillColorRGB(0, 0, 0)
    pdf.rect(15, y - 50, width - 30, 40, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(width / 2, y - 33, f"|||| {order['order_sn']} ||||")

    # STEP 7: Footer with timestamp so we can verify the PDF was freshly generated.
    pdf.setFillColorRGB(0.5, 0.5, 0.5)
    pdf.setFont("Helvetica-Oblique", 7)
    pdf.drawString(
        15, 15,
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
    )

    # STEP 8: Finalize the PDF and return its bytes.
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


# ============================================================
# Internal helpers
# ============================================================

def _wrap_text(text, max_chars):
    """
    Splits a long string into lines no longer than max_chars.
    Used for the address field which can be quite long.
    """
    words = text.split(" ")
    lines = []
    current = ""

    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = (current + " " + word).strip()
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines
