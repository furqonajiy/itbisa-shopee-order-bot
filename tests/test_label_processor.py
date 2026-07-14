"""Regression tests for the bottom-whitespace crop.

The intermittent "waybill / resi not successfully cropped" bug: a single
sub-visible render speck in the blank area below the label used to defeat the
crop, because the scan stopped at the first dark pixel from the bottom. The
crop now requires a short run of dark pixels per row, so a lone speck is
ignored while genuine text/barcode rows still bound the label.
"""

from PIL import Image

from src import label_processor


def _white(w, h):
    return Image.new("RGB", (w, h), "white")


def _fill_row(img, y, x0=0, x1=None):
    x1 = img.width if x1 is None else x1
    for x in range(x0, x1):
        img.putpixel((x, y), (0, 0, 0))


def test_crop_ignores_isolated_speck_below_label():
    # Real content: a solid black bar across the top rows; then a lone speck
    # deep in the otherwise-blank area (the exact failure mode).
    img = _white(400, 1200)
    for y in range(0, 60):
        _fill_row(img, y)
    img.putpixel((200, 1000), (180, 180, 180))  # single sub-visible speck

    out = label_processor._crop_bottom_whitespace(img)

    # Cropped to just below the bar (+padding); the speck is ignored.
    assert out.height <= 60 + 8 + 2
    assert out.height < 1000


def test_crop_keeps_genuine_bottom_content():
    # A real text/barcode-like row near the bottom must NOT be trimmed away.
    img = _white(400, 1200)
    for y in range(0, 60):
        _fill_row(img, y)
    _fill_row(img, 800)  # full-width dark row = genuine content

    out = label_processor._crop_bottom_whitespace(img)

    assert 800 < out.height <= 800 + 8 + 2


def test_crop_all_white_returns_unchanged():
    img = _white(400, 1200)
    out = label_processor._crop_bottom_whitespace(img)
    assert out.height == 1200


def test_content_row_min_dark_scales_with_width_and_has_floor():
    assert label_processor._content_row_min_dark(400) == 6      # floor wins
    assert label_processor._content_row_min_dark(2000) == 20     # 1% of width
