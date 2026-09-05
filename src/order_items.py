"""
order_items.py
--------------
Record WHAT was ordered — SKU and quantity per order — into
`data/order_items.json` on the `bot-state` branch.

**Why this exists.** The bot already asks Shopee for `item_list`
(`shopee_client._get_order_details`), uses the response's `package_list` to
arrange the shipment, and then throws the item lines away. So the shop knew
exactly which goods were leaving the shelf and kept only the order number.

That gap has a concrete cost. A stock opname taken while orders are
`READY_TO_SHIP` counts a shelf whose goods are already picked and packed, while
the book still counts them as on hand because the order has not reached the Jual
export yet. The difference looks like **theft**. On 2026-09-05 that was 583 pcs
across three SKUs — 174 buzzers, 391 relays, 18 ICs — and there was no way to
tell a write-off from a shipment without opening six orders by hand in Seller
Center.

With this file the report side can subtract in-flight orders and reconcile
honestly.

**Contract**
- **Append-only, and it never forgets.** `processed_orders.json` prunes after
  `STATE_RETENTION_DAYS` (3) because a label is only needed once; this file is
  evidence for a reconciliation that may happen weeks later, so pruning it would
  recreate the very blind spot it closes.
- **Records every order SEEN, not only the newly processed ones.** An order can
  be seen, skipped because its label is not ready, and processed on a later run.
  Goods leave the shelf when the order is picked, not when the label prints, so
  recording only successes would miss exactly the orders a same-day opname trips
  over.
- **Upsert, never duplicate.** Re-running on the same order refreshes its status
  and leaves one row.
- Values are stored as plain ints/strings — no marketplace objects — so the
  report side can read this file without knowing Shopee's schema.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "data" / "order_items.json"


def _sku_of(item: dict) -> str:
    """The base SKU a line refers to.

    `model_sku` is the variation's seller SKU and is what the ledger keys on;
    `item_sku` is the product-level fallback for a listing with no variations.
    Both are normalized `UPPER().strip()` to the repo-wide SKU contract, or the
    report side would fail to match its own ledger.
    """
    for key in ("model_sku", "item_sku"):
        val = (item.get(key) or "").strip()
        if val:
            return val.upper()
    return ""


def extract(order: dict) -> list[dict]:
    """[{sku, qty}] from one order detail, blank/zero lines dropped.

    Quantities are summed per SKU: Shopee can return the same variation on more
    than one line (a split package), and two lines of 50 are one shipment of 100
    as far as the shelf is concerned.
    """
    totals: dict[str, int] = {}
    for item in order.get("item_list") or []:
        sku = _sku_of(item)
        try:
            qty = int(item.get("model_quantity_purchased") or 0)
        except (TypeError, ValueError):
            qty = 0
        if not sku or qty <= 0:
            continue
        totals[sku] = totals.get(sku, 0) + qty
    return [{"sku": s, "qty": q} for s, q in sorted(totals.items())]


def load(path: Path | str = _PATH) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        # Never let a corrupt cache stop a shipment: labels are the bot's job,
        # this file is bookkeeping. Report and carry on with an empty view.
        print(f"  ⚠ order_items.json tidak terbaca ({e}); mulai dari kosong")
        return {}


def save(state: dict, path: Path | str = _PATH) -> None:
    path = Path(path)
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True, ensure_ascii=False)


def record(orders: list[dict], state: dict | None = None,
           now: datetime | None = None) -> dict:
    """Upsert every order's item lines into the state and return it.

    Takes the FULL pending list, not just the new orders — see the module
    docstring. An order whose `item_list` is empty (Shopee occasionally returns
    the detail before the lines are ready) is recorded with an empty `items` and
    a `pending_items` flag rather than skipped, so a later run can fill it in and
    the report side can tell "nothing ordered" from "not known yet".
    """
    state = dict(state or {})
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    for order in orders or []:
        sn = str(order.get("order_sn") or "").strip()
        if not sn:
            continue
        items = extract(order)
        prev = state.get(sn) or {}
        state[sn] = {
            "order_status": order.get("order_status") or prev.get("order_status") or "",
            # Keep the first sighting: it is when the goods started moving.
            "first_seen": prev.get("first_seen") or stamp,
            "last_seen": stamp,
            "items": items or prev.get("items") or [],
            "pending_items": not items and not prev.get("items"),
        }
    return state


def summarize(state: dict) -> dict[str, int]:
    """Total qty per SKU across every recorded order — the in-flight position."""
    totals: dict[str, int] = {}
    for rec in (state or {}).values():
        for line in rec.get("items") or []:
            sku = str(line.get("sku") or "")
            try:
                qty = int(line.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            if sku and qty:
                totals[sku] = totals.get(sku, 0) + qty
    return totals
