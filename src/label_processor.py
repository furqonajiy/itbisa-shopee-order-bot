"""
label_processor.py
------------------
Converts a shipping label PDF into Telegram-ready PNG image bytes.

Why this file exists:
  The employee prefers images over PDFs because they can swipe through them
  in Telegram and Epson iPrint handles them well. This module is the only
  place that knows about PDF rendering, page pairing, image format, and DPI.

Telegram send behavior:
  - A 1-page PDF becomes 1 image.
  - A 2-page PDF becomes 1 merged image.
  - A 3-page PDF becomes 2 images: pages 1-2 merged, page 3 alone.
  - A 4-page PDF becomes 2 merged images, and so on.

This module is pure local computation. It does not call any external API.
"""

import io

from PIL import Image
from pdf2image import convert_from_bytes

from src import config


_MERGED_PAGE_GAP_PX = 12


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


def _merge_page_pair(images):
    """
    Stacks up to two rendered PDF pages into one Telegram image.

    We merge vertically so both labels remain readable on mobile and printable
    from Epson iPrint. Width is centered when the two pages have different
    sizes. A small white gap separates the labels visually.
    """

    if len(images) == 1:
        return images[0]

    width = max(image.width for image in images)
    height = sum(image.height for image in images) + _MERGED_PAGE_GAP_PX
    merged = Image.new("RGB", (width, height), "white")

    y_offset = 0
    for image in images:
        image = image.convert("RGB")
        x_offset = (width - image.width) // 2
        merged.paste(image, (x_offset, y_offset))
        y_offset += image.height + _MERGED_PAGE_GAP_PX

    return merged


def _merge_pages_every_two(images):
    """Groups rendered PDF pages into Telegram images, two pages per image."""
    merged_images = []

    for start_index in range(0, len(images), 2):
        merged_images.append(_merge_page_pair(images[start_index:start_index + 2]))

    return merged_images


def _image_to_png_bytes(image):
    """Converts one PIL Image into raw PNG bytes."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_to_png(pdf_bytes):
    """
    Converts the first page of a PDF into a PNG image.

    Kept for backward compatibility with older scripts that still expect a
    single first-page image result.

    Args:
      pdf_bytes: the PDF file contents as bytes.

    Returns:
      PNG image contents as bytes for the first page.
    """

    images = convert_from_bytes(
        pdf_bytes,
        dpi=config.LABEL_IMAGE_DPI,
        first_page=1,
        last_page=1,
    )
    return _image_to_png_bytes(_crop_bottom_whitespace(images[0]))


def pdf_to_pngs(pdf_bytes):
    """
    Converts all pages of a PDF into Telegram-ready PNG images.

    Every 2 PDF pages are merged into 1 image before sending to Telegram.
    This reduces message count for multi-page labels without changing the
    original page order.

    Args:
      pdf_bytes: the PDF file contents as bytes.

    Returns:
      A list of PNG image contents as bytes, one entry per Telegram image.
    """

    # STEP 1: Render the PDF into PIL Image objects (one per page).
    # We pass the DPI from config so it is easy to tweak later.
    images = convert_from_bytes(pdf_bytes, dpi=config.LABEL_IMAGE_DPI)

    # STEP 2: Trim trailing blank space at the bottom only.
    cropped_images = [_crop_bottom_whitespace(image) for image in images]

    # STEP 3: Merge every two PDF pages into one Telegram image.
    merged_images = _merge_pages_every_two(cropped_images)

    # STEP 4: Convert every Telegram image into raw PNG bytes.
    return [_image_to_png_bytes(image) for image in merged_images]
