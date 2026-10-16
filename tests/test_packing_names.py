"""Packing names on the label: the list, the wrapping, and the PDF rewrite.

The PDFs here are synthetic copies of the real layouts (same header words and
column positions as a Shopee label and a TikTok Shop packing slip), so no buyer
data ever enters this public repo.
"""

import pymupdf

from src import label_processor, packing_names
from src.packing_names import BLACK, RED

NAMES = packing_names.parse(
    "# comment = ignored\n"
    "ITBISA-RELAY-5V-4PIN-BLUE = Relay [5V 4 Pin] SL-A\n"
    "ITBISA-ABC-BATERAI-AAA-ALKALINE = Baterai ABC Alkaline [A3 Kecil] | 6=(4+2)\n"
    "ITBISA-7SEGMENT-ANODE-RED-0.56-1BIT = 7 Segment 0.56\" [Anode] (Kode B)\n"
)


def test_parse_skips_comments_and_uppercases():
    assert set(NAMES) == {"ITBISA-RELAY-5V-4PIN-BLUE", "ITBISA-ABC-BATERAI-AAA-ALKALINE",
                          "ITBISA-7SEGMENT-ANODE-RED-0.56-1BIT"}


def test_runs_colours_the_bracket_and_the_pack_count():
    assert packing_names.runs("ITBISA-RELAY-5V-4PIN-BLUE", NAMES) == [
        ("Relay ", BLACK), ("5V 4 Pin", RED), (" SL-A", BLACK)]
    assert packing_names.runs("10PCS-ITBISA-RELAY-5V-4PIN-BLUE", NAMES)[0] == ("10PCS ", RED)


def test_runs_pack_alias():
    assert packing_names.runs("6PCS-ITBISA-ABC-BATERAI-AAA-ALKALINE", NAMES)[0] == ("(4+2)PCS ", RED)


def test_runs_unlisted_sku_is_none():
    assert packing_names.runs("ITBISA-UNKNOWN", NAMES) is None


def test_wrap_keeps_red_part_and_paren_group_whole():
    segs = packing_names.runs("ITBISA-7SEGMENT-ANODE-RED-0.56-1BIT", NAMES)
    lines = packing_names.wrap(segs, 12, len)
    texts = ["".join(t for t, _ in line) for line in lines]
    assert any(("Anode", RED) in line for line in lines)     # the red part is never split
    assert "Anode (Kode B)" in texts                         # "(...)" stays with the word before


def _label(rows, tiktok=False):
    """A one-page PDF with the marketplace's product table header and rows."""
    doc = pymupdf.open()
    page = doc.new_page(width=297, height=419)
    if tiktok:
        header = [(5.8, "Product"), (35.2, "Name"), (129.6, "SKU"), (172.2, "Seller"),
                  (194.1, "SKU"), (264.6, "Qty")]
        name_x, sku_x, qty_x, end = 5.8, 172.2, 268.6, "Total:"
    else:
        header = [(9.2, "#"), (23.4, "Nama"), (45.9, "Produk"), (162.6, "SKU"),
                  (212.0, "Variasi"), (269.6, "Qty")]
        name_x, sku_x, qty_x, end = 23.4, 162.6, 269.6, "Pesan:"
    for x, t in header:
        page.insert_text((x, 260), t, fontsize=7)
    y = 270
    for i, (name_lines, sku_lines, qty) in enumerate(rows, 1):
        if not tiktok:
            page.insert_text((9.2, y), str(i), fontsize=7)
        page.insert_text((qty_x, y), str(qty), fontsize=7)
        for j, t in enumerate(name_lines):
            page.insert_text((name_x, y + j * 7.5), t, fontsize=7)
        for j, t in enumerate(sku_lines):
            page.insert_text((sku_x, y + j * 7.5), t, fontsize=7)
        y += max(len(name_lines), len(sku_lines)) * 7.5 + 3
    page.draw_line((5, y - 2), (290, y - 2))                 # a table line
    page.insert_text((8.5, y + 8), end, fontsize=7)
    return doc.tobytes()


ROWS = [
    (["ITBisa - Relay 5V 4 Pin SRD-05VDC-SL-", "A SPST-NO 10A | Relay Songle", "PCB THT Koil"],
     ["ITBISA-", "RELAY-5V-", "4PIN-BLUE"], 5),
    (["ITBisa - Sesuatu Yang Tidak Ada", "Di Daftar Packing"], ["ITBISA-UNKNOWN"], 1),
    (["ITBisa - Baterai ABC AAA Alkaline", "Isi 6 Pcs Kecil"],
     ["6PCS-ITBISA-ABC-", "BATERAI-AAA-", "ALKALINE"], 2),
]


def _text(pdf):
    return pymupdf.open(stream=pdf, filetype="pdf")[0].get_text()


def _red_boxes(pdf):
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    return [d for d in page.get_drawings()
            if d.get("color") and abs(d["color"][0] - 0.8) < 0.01 and d["color"][1] == 0]


def test_shopee_names_replaced_unlisted_kept_pack_boxed():
    out, changed = label_processor.with_packing_names(_label(ROWS), NAMES)
    text = _text(out)
    assert changed == 2                                      # relay + battery; the unlisted row is left alone
    assert "SRD-05VDC" not in text and "SL-A" in text and "5V 4 Pin" in text
    assert "Sesuatu Yang Tidak Ada" in text                  # unlisted SKU keeps its name
    assert "(4+2)PCS" in text and "A3 Kecil" in text
    assert "ITBISA-UNKNOWN" in text and "4PIN-BLUE" in text  # SKU column untouched
    assert len(_red_boxes(out)) == 1                         # only the pack SKU


def test_tiktok_layout():
    out, changed = label_processor.with_packing_names(_label(ROWS[:1], tiktok=True), NAMES)
    assert changed == 1
    assert "SRD-05VDC" not in _text(out) and "5V 4 Pin" in _text(out)


def test_table_line_survives():
    out, _ = label_processor.with_packing_names(_label(ROWS), NAMES)
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    assert any(d["rect"].width > 250 for d in page.get_drawings())


def test_no_names_or_bad_pdf_returns_input_untouched():
    pdf = _label(ROWS)
    assert label_processor.with_packing_names(pdf, {}) == (pdf, 0)
    assert label_processor.with_packing_names(b"not a pdf", NAMES) == (b"not a pdf", 0)


def test_no_table_returns_input_untouched():
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "Qty tanpa tabel")
    pdf = doc.tobytes()
    assert label_processor.with_packing_names(pdf, NAMES) == (pdf, 0)


def test_load_reads_local_file(tmp_path):
    f = tmp_path / "packing_names.txt"
    f.write_text("ITBISA-X = Barang [X]" + chr(10), encoding="utf-8")
    assert packing_names.load(local_file=f) == ({"ITBISA-X": "Barang [X]"}, None)


def test_load_never_raises(tmp_path):
    names, error = packing_names.load(local_file=tmp_path / "none.txt", url="http://127.0.0.1:9/x")
    assert names == {} and error
