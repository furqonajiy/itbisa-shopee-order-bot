"""
balance_throttle.py
-------------------
Tracks this order bot's `/stock_balance` dispatches and guarantees no touched
SKU is ever dropped. `MIN_INTERVAL_HOURS` is the minimum spacing between
dispatches: it caps balance to one workflow run per window to conserve GitHub
Actions minutes. With the current 1-hour window, a burst of order runs in the
same hour produces a single balance run instead of one per run. Set it to 0 to
dispatch on every run (immediate rebalance, most Actions minutes); raise it to
trade rebalance freshness for fewer runs.

Because orders are marked processed as soon as their labels are delivered, any
run that withholds its balance dispatch — the throttle window, or a failed
dispatch — would otherwise lose the SKUs it touched (the next run won't re-see
those orders). To avoid that, the base SKUs touched while a dispatch is withheld
are accumulated in `pending_skus` and flushed in a single dispatch once the
window reopens. `/stock_balance` is idempotent (it re-reads current stock and
re-splits), so deferring is safe.

State lives in `data/balance_throttle.json` on the `bot-state` branch:
  {"last_dispatch_at": "<iso-utc>" | null, "pending_skus": ["BASE-SKU", ...]}

Duplicated across both order bots on purpose (self-contained repos); do not
factor it out.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Minimum spacing between balance dispatches, in hours. 1 = at most one balance
# run per hour: a burst of order runs in the same hour collapses to a single
# dispatch (conserves GitHub Actions minutes). Set to 0 for immediate rebalance
# on every run (most minutes); raise it to trade freshness for fewer runs.
MIN_INTERVAL_HOURS = 1

_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "balance_throttle.json"

_EMPTY = {"last_dispatch_at": None, "pending_skus": []}


def load(path=_STATE_PATH):
    """Loads throttle state. Returns a fresh empty state if the file is
    missing or unreadable (first run, or bot-state not yet seeded)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return dict(_EMPTY)
    return {
        "last_dispatch_at": data.get("last_dispatch_at"),
        "pending_skus": list(data.get("pending_skus", [])),
    }


def save(state, path=_STATE_PATH):
    """Atomically writes throttle state. pending_skus is de-duplicated/sorted."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    payload = {
        "last_dispatch_at": state.get("last_dispatch_at"),
        "pending_skus": sorted({s for s in state.get("pending_skus", []) if s}),
    }
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def merge_pending(state, touched_skus):
    """Union of already-pending SKUs and this run's touched base SKUs (sorted,
    empties dropped). Pure."""
    pending = set(state.get("pending_skus", []))
    pending |= {s for s in (touched_skus or []) if s}
    return sorted(pending)


def window_open(state, now=None, min_interval_hours=MIN_INTERVAL_HOURS):
    """True if at least `min_interval_hours` have elapsed since
    `last_dispatch_at` (or there has never been a dispatch). Pure given `now`."""
    last = state.get("last_dispatch_at")
    if not last:
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        last_dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return True
    return (now - last_dt) >= timedelta(hours=min_interval_hours)
