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


def test_crop_ignores_margin_watermark_tail():
    # Shopee tiles the SPX resi watermark ("SPXID...") down both page margins.
    # Watermark stripes at the far left/right edges deep in the tail must NOT
    # keep the whole blank tail attached (the "long resi" bug) — only the real
    # centred content bounds the crop.
    img = _white(400, 1200)
    for y in range(0, 60):
        _fill_row(img, y)  # genuine full-width content at the top
    # Watermark only at the outer margins, far below the label.
    for y in range(900, 950):
        for x in list(range(0, 30)) + list(range(370, 400)):
            img.putpixel((x, y), (0, 0, 0))

    out = label_processor._crop_bottom_whitespace(img)

    assert out.height <= 60 + 8 + 2  # cropped to the real content
    assert out.height < 900          # the margin watermark tail is gone


def test_crop_all_white_returns_unchanged():
    img = _white(400, 1200)
    out = label_processor._crop_bottom_whitespace(img)
    assert out.height == 1200


def test_content_row_min_dark_scales_with_width_and_has_floor():
    assert label_processor._content_row_min_dark(400) == 6      # floor wins
    assert label_processor._content_row_min_dark(2000) == 20     # 1% of width


# ----------------------------------------------------------------------
# The margin watermark — this bot kept scanning the FULL width after the
# sibling Shopee bot was fixed, so a tiled resi watermark down the page edges
# bound the crop and the whole blank A4 tail stayed attached.
# ----------------------------------------------------------------------
def _fill_margins(img, y, band_px=30):
    """Ink at the far left and right only — where the resi watermark tiles."""
    for x in range(0, band_px):
        img.putpixel((x, y), (0, 0, 0))
    for x in range(img.width - band_px, img.width):
        img.putpixel((x, y), (0, 0, 0))


def test_crop_ignores_margin_watermark_below_label():
    img = _white(400, 1200)
    for y in range(0, 60):          # the real label
        _fill_row(img, y)
    for y in range(900, 1100, 8):   # watermark tiled down BOTH margins
        _fill_margins(img, y)

    out = label_processor._crop_bottom_whitespace(img)
    assert out.height < 200, (
        f"margin watermark still bound the crop: height {out.height} of {img.height}")


def test_crop_ignores_scattered_dots_that_clear_the_total():
    # A sparse watermark can hold MORE dark pixels than the total threshold
    # while being only isolated dots. Counting the total alone (what the code
    # did while its comment claimed a run) let this bind the crop.
    img = _white(400, 1200)
    for y in range(0, 60):
        _fill_row(img, y)
    for x in range(100, 300, 7):    # 29 scattered dots, well over min_dark
        img.putpixel((x, 1000), (0, 0, 0))

    out = label_processor._crop_bottom_whitespace(img)
    assert out.height < 200, (
        f"scattered dots still bound the crop: height {out.height}")


def test_crop_keeps_a_thin_but_real_centre_line():
    # The guard must not over-crop. A 6px-wide mark in the centre is real ink
    # (0.8 mm at 200 DPI) and must still bind the crop -- losing label content
    # is worse than leaving a tail attached.
    img = _white(400, 1200)
    for y in range(0, 60):
        _fill_row(img, y)
    for y in range(1000, 1004):
        _fill_row(img, y, x0=197, x1=203)

    out = label_processor._crop_bottom_whitespace(img)
    assert out.height >= 1004, f"real centre content was cropped away: {out.height}"


def test_barcode_like_row_still_binds_the_crop():
    # A row crossing a barcode is alternating bars, not one long run. Requiring
    # a LONG run would reject it and cut the barcode off the label.
    img = _white(400, 1200)
    for y in range(0, 60):
        _fill_row(img, y)
    for y in range(1000, 1010):
        for x0 in range(150, 260, 10):
            _fill_row(img, y, x0=x0, x1=x0 + 4)   # 4px bars, 6px gaps

    out = label_processor._crop_bottom_whitespace(img)
    assert out.height >= 1010, f"barcode row was cropped away: {out.height}"
