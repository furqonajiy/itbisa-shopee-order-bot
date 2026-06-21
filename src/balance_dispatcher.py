"""End-of-run dispatcher for /stock_balance.

Collects base SKUs touched in this run, then fires a single
workflow_dispatch on itbisa-shop-stock-bot/balance.yml with all
collected base SKUs joined by space. Best-effort: failure is logged
and reported via the result dict; never raised.

Duplicated intentionally across Shopee and TikTok Shop order bots
to keep each repo self-contained.
"""

import json
import logging
import os
import re
from urllib import error, request

logger = logging.getLogger(__name__)

GH_OWNER = "furqonajiy"
STOCK_REPO = "itbisa-shop-stock-bot"
BALANCE_WORKFLOW = "balance.yml"
BALANCE_REF = "main"
GH_API_TIMEOUT_SECONDS = 20

_PCS_PREFIX = re.compile(r"^\d+PCS-", re.IGNORECASE)


def to_base_sku(sku):
    """Strip leading ^\\d+PCS- and uppercase. Returns None for empty input."""
    if sku is None:
        return None
    s = str(sku).strip().upper()
    if not s:
        return None
    return _PCS_PREFIX.sub("", s, count=1)


class BalanceDispatcher:
    def __init__(self):
        self._skus = set()

    def record(self, sku):
        base = to_base_sku(sku)
        if base:
            self._skus.add(base)

    def collected(self):
        """Returns the sorted base SKUs recorded so far (read-only view)."""
        return sorted(self._skus)

    def dispatch_all(self):
        skus = sorted(self._skus)
        result = {
            "requested": len(skus),
            "dispatched": 0,
            "failed": 0,
            "skus": skus,
        }

        if not skus:
            return result

        token = os.environ.get("STOCK_DISPATCH_TOKEN")
        if not token:
            logger.warning(
                "STOCK_DISPATCH_TOKEN not set; %d SKU(s) reported as failed",
                len(skus),
            )
            result["failed"] = len(skus)
            return result

        url = (
            f"https://api.github.com/repos/{GH_OWNER}/{STOCK_REPO}"
            f"/actions/workflows/{BALANCE_WORKFLOW}/dispatches"
        )
        payload = json.dumps(
            {
                "ref": BALANCE_REF,
                "inputs": {
                    "sku": " ".join(skus),
                    "dry_run": "false",
                },
            }
        ).encode("utf-8")

        req = request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "itbisa-balance-dispatcher",
            },
        )

        try:
            with request.urlopen(req, timeout=GH_API_TIMEOUT_SECONDS) as resp:
                status = getattr(resp, "status", resp.getcode())
                if 200 <= status < 300:
                    logger.info(
                        "Balance dispatch OK for %d SKU(s): %s",
                        len(skus),
                        ", ".join(skus),
                    )
                    result["dispatched"] = len(skus)
                else:
                    logger.warning("Balance dispatch HTTP %s", status)
                    result["failed"] = len(skus)
        except error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            logger.warning("Balance dispatch HTTPError %s: %s %s", e.code, e.reason, body)
            result["failed"] = len(skus)
        except Exception as e:
            logger.warning("Balance dispatch failed: %s", e)
            result["failed"] = len(skus)

        return result