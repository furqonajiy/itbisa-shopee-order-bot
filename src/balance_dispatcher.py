"""
balance_dispatcher.py
---------------------
Collects shipped base SKUs during a /resi_shopee (or /resi_all) run and
triggers /stock_balance for each at end-of-run.

Why this exists:
  Every shipped Shopee order decrements Shopee's reported stock for that
  variant. The bot's 50:50 split between Shopee and TikTok Shop drifts
  out of balance after every label send. Re-running /stock_balance after
  shipping restores parity for the next cycle.

Design notes:
  - SKUs are recorded only after Telegram confirms label delivery. If
    delivery fails, the SKU is NOT recorded — the next /resi_* run will
    pick the order up again and record it then.
  - Item-level SKUs (e.g. 20PCS-ITBISA-LED-5MM) are normalized to the
    base SKU (ITBISA-LED-5MM) before dispatching, because /stock_balance
    accepts base SKU only.
  - Dispatch is best-effort. Failures are logged + reported in the
    heartbeat but never propagate as exceptions to the order loop.
  - One workflow_dispatch per base SKU. Acceptable today because the
    average /resi_* run touches a handful of SKUs.

GitHub auth:
  Requires STOCK_DISPATCH_TOKEN env var (PAT with actions:write scope on
  the itbisa-shop-stock-bot repo). If the env var is missing, dispatching
  is skipped and the heartbeat reports zero dispatches.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"
STOCK_OWNER = "furqonajiy"
STOCK_REPO = "itbisa-shop-stock-bot"
BALANCE_WORKFLOW = "balance.yml"
BALANCE_REF = "main"
DISPATCH_SPACING_SECONDS = 1.0

_PACK_PREFIX_RE = re.compile(r"^\d+PCS-")


def to_base_sku(sku):
    """Strips a leading <digits>PCS- pack-size prefix and uppercases the SKU.

    Empty / None inputs return "". The caller filters those out via record().
    """
    if not sku:
        return ""
    return _PACK_PREFIX_RE.sub("", sku.strip().upper())


class BalanceDispatcher:
    """Accumulates base SKUs and dispatches /stock_balance workflows.

    The order bot calls record() inside the success branch of the per-order
    loop, then calls dispatch_all() once after the loop finishes.
    """

    def __init__(self):
        self._base_skus = set()

    def record(self, sku):
        """Adds one SKU to the pending balance set. Pack-size prefix stripped."""
        base = to_base_sku(sku)
        if base:
            self._base_skus.add(base)

    def collected(self):
        """Returns the sorted list of base SKUs collected so far."""
        return sorted(self._base_skus)

    def dispatch_all(self):
        """Fires workflow_dispatch for every collected base SKU.

        Returns a dict suitable for the heartbeat:
          {
            "requested":  int,  # total SKUs we tried to dispatch
            "dispatched": int,  # successful dispatches
            "failed":     list, # base SKUs whose dispatch failed
            "skus":       list, # all base SKUs collected this run
          }
        """
        skus = self.collected()
        result = {
            "requested": len(skus),
            "dispatched": 0,
            "failed": [],
            "skus": skus,
        }

        if not skus:
            return result

        token = os.environ.get("STOCK_DISPATCH_TOKEN")
        if not token:
            print(
                "[balance] STOCK_DISPATCH_TOKEN not set — skipping balance "
                "dispatch for: " + ", ".join(skus)
            )
            result["failed"] = list(skus)
            return result

        for sku in skus:
            try:
                _dispatch_one(sku, token)
                result["dispatched"] += 1
                print(f"[balance] dispatched balance.yml for {sku}")
                time.sleep(DISPATCH_SPACING_SECONDS)
            except Exception as e:
                print(f"[balance] failed to dispatch {sku}: {e}")
                result["failed"].append(sku)

        return result


def _dispatch_one(sku, token):
    """POSTs one workflow_dispatch to balance.yml. Raises on non-2xx."""
    url = (
        f"{GITHUB_API}/repos/{STOCK_OWNER}/{STOCK_REPO}"
        f"/actions/workflows/{BALANCE_WORKFLOW}/dispatches"
    )
    payload = json.dumps({
        "ref": BALANCE_REF,
        "inputs": {
            "sku": sku,
            "dry_run": "false",
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "itbisa-shopee-order-bot",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status not in (201, 204):
                raise RuntimeError(f"unexpected status {resp.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from None