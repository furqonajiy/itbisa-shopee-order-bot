"""
fetch_label_sample.py
---------------------
Downloads the label PDF of already-labelled orders into out/, untouched, plus
the images /resi_shopee would send for it (out/<order>_<n>.png, packing names
on), so a rendering change can be checked without sending anything.

Why this file exists:
  Telegram only ever receives the rendered PNG, so the original PDF (its text
  layer and exact column positions) is otherwise never kept. Changing how a
  label is rendered needs real PDFs to test against. The workflow
  label_sample.yml runs this, encrypts each PDF and uploads it as a one-day
  artifact: the repo is public and a label carries the buyer's name, address
  and phone.

It never sends to Telegram and never touches processed_orders.json /
order_items.json, so the next /resi_shopee still sends these labels. A label
exists only once shipment is arranged and only until the courier picks the
package up. By default an order still READY_TO_SHIP is skipped; with
--atur-kirim it is arranged for dropoff first, exactly as /resi_shopee would
(same readiness precheck), which is a marketplace write: use it only with the
operator's go-ahead for those orders.

Usage:
  python scripts/fetch_label_sample.py [--atur-kirim] ORDER_SN [ORDER_SN ...]
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import label_processor, packing_names, main as bot, shopee_client  # noqa: E402

OUT_DIR = PROJECT_ROOT / "out"


def arrange(order_sns):
    """Arranges dropoff for each order still READY_TO_SHIP, as /resi_shopee does."""
    pending = {o["order_sn"]: o for o in shopee_client.get_pending_orders()}
    arranged = False
    for order_sn in order_sns:
        order = pending.get(order_sn)
        if not order or order.get("order_status") != "READY_TO_SHIP":
            continue
        ready, reason = bot._is_ready_to_ship(order)
        if not ready:
            print(f"✗ {order_sn}: {reason}")
            continue
        shopee_client.ship_order_to_dropoff(order_sn)
        print(f"  {order_sn}: pengiriman diatur (dropoff)")
        arranged = True
    if arranged:
        time.sleep(10)  # let Shopee issue the tracking number


def main(order_sns, atur_kirim=False):
    OUT_DIR.mkdir(exist_ok=True)
    if atur_kirim:
        arrange(order_sns)
    names, names_error = packing_names.load()
    print(f"Nama packing: {len(names)} SKU" + (f" (gagal dimuat: {names_error})" if names_error else ""))
    failed = 0
    for order_sn in order_sns:
        try:
            pdf_bytes = shopee_client.get_shipping_label_pdf(order_sn)
        except Exception as e:                              # noqa: BLE001
            print(f"✗ {order_sn}: {e}")
            failed += 1
            continue
        if pdf_bytes is None:
            print(f"✗ {order_sn}: label belum siap")
            failed += 1
            continue
        (OUT_DIR / f"{order_sn}.pdf").write_bytes(pdf_bytes)
        label_pdf, renamed = label_processor.with_packing_names(pdf_bytes, names)
        pngs = label_processor.pdf_to_pngs(label_pdf)
        for n, png in enumerate(pngs, 1):
            (OUT_DIR / f"{order_sn}_{n}.png").write_bytes(png)
        print(f"✓ {order_sn}: {len(pdf_bytes)} bytes, {renamed} nama packing, {len(pngs)} gambar")
    return 1 if failed == len(order_sns) else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    atur = "--atur-kirim" in args
    args = [a for a in args if a != "--atur-kirim"]
    if not args:
        sys.exit(__doc__)
    sys.exit(main(args, atur_kirim=atur))
