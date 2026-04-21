"""
label_processor.py
------------------
Converts a shipping label PDF into PNG image bytes.

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


def _crop_bottom_whitespace(image, white_threshold=250, bottom_padding_px=8):
    """
    Removes trailing blank space at the bottom of a rendered label image.
    Keeps top/left/right unchanged for safety.
    """

    grayscale = image.convert("L")
    width, height = grayscale.size
    pixels = grayscale.load()

    last_content_row = None

    for y in range(height - 1, -1, -1):
        for x in range(width):
            if pixels[x, y] < white_threshold:
                last_content_row = y
                break
        if last_content_row is not None:
            break

    if last_content_row is None:
        return image

    crop_bottom = min(height, last_content_row + 1 + bottom_padding_px)

    if crop_bottom >= height:
        return image

    return image.crop((0, 0, width, crop_bottom))


def _image_to_png_bytes(image):
    """Converts one PIL Image page into raw PNG bytes."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_to_png(pdf_bytes):
    """
    Converts the first page of a PDF into a PNG image.

    Kept for backward compatibility with older scripts that still expect a
    single image result.

    Args:
      pdf_bytes: the PDF file contents as bytes.

    Returns:
      PNG image contents as bytes for the first page.
    """

    return pdf_to_pngs(pdf_bytes)[0]


def pdf_to_pngs(pdf_bytes):
    """
    Converts all pages of a PDF into PNG images.

    Args:
      pdf_bytes: the PDF file contents as bytes.

    Returns:
      A list of PNG image contents as bytes, one entry per PDF page, in order.
    """

    # STEP 1: Render the PDF into PIL Image objects (one per page).
    # We pass the DPI from config so it is easy to tweak later.
    images = convert_from_bytes(pdf_bytes, dpi=config.LABEL_IMAGE_DPI)

    # STEP 2: Trim trailing blank space at the bottom only.
    # STEP 3: Convert every rendered page into raw PNG bytes.
    return [_image_to_png_bytes(_crop_bottom_whitespace(image)) for image in images]