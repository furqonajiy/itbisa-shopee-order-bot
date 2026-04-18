"""
label_processor.py
------------------
Converts a shipping label PDF into a PNG image.

Why this file exists:
  The employee prefers images over PDFs because they can swipe through them
  in Telegram and Epson iPrint handles them well. This module is the only
  place that knows about PDF rendering, so if we ever change the image
  format or DPI, only one file changes.

This module is pure local computation. It does not call any external API.
"""

import io

from pdf2image import convert_from_bytes

from src import config


def pdf_to_png(pdf_bytes):
    """
    Converts the first page of a PDF into a PNG image.

    Shopee shipping labels are always one page, so we only render the first
    page. If a label ever has multiple pages, we will revisit this.

    Args:
      pdf_bytes: the PDF file contents as bytes.

    Returns:
      PNG image contents as bytes, ready to send to Telegram.
    """

    # STEP 1: Render the PDF into PIL Image objects (one per page).
    # We pass the DPI from config so it is easy to tweak later.
    images = convert_from_bytes(pdf_bytes, dpi=config.LABEL_IMAGE_DPI)

    # STEP 2: Take only the first page. Shopee labels are single-page.
    first_page = images[0]

    # STEP 3: Save the image into an in-memory bytes buffer as PNG.
    # We use a buffer instead of a real file because we just need to send
    # the bytes to Telegram, not save anything to disk.
    buffer = io.BytesIO()
    first_page.save(buffer, format="PNG")

    # STEP 4: Return the raw bytes from the buffer.
    return buffer.getvalue()
