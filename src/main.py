"""
main.py
-------
The entry point. Run this file to do one full processing cycle.

What this script does, in order:
  1. Load the list of order IDs we already processed.
  2. Ask Shopee for orders in READY_TO_SHIP or PROCESSED status.
  3. Filter out the ones we already handled.
  4. Stop if there are too many (safety check).
  5. For each new order:
     a. If READY_TO_SHIP, pre-check the package with get_package_detail.
        Only when fulfillment_status=LOGISTICS_READY and
        is_shipment_arranged=false do we call ship_order_to_dropoff. This
        avoids calling v2.logistics.ship_order on packages that are still
        being allocated or already arranged, which keeps the API call
        success rate above Shopee's 90% threshold (Shopee Open Platform
        task "Improve API Call Success Rate for v2.logistics.ship_order").
     b. Fetch the shipping label PDF (with retries while Shopee generates it).
     c. Convert PDF pages to Telegram-ready PNG images, merged two pages per
        image, then send to Telegram and mark as processed only AFTER
        Telegram confirms delivery.
     d. Record each shipped variant SKU into the balance dispatcher so the
        affected base SKUs can be rebalanced after this run.
  6. Save the updated state file so future runs remember.
  7. Dispatch /stock_balance for every base SKU touched this run. Best-effort,
     never fails the order run.
  8. Send a heartbeat summary to Telegram so the employee knows the bot is
     alive, even if there were no orders this run.

The GitHub Actions workflow runs this script on cron, manual dispatch, or
Telegram Worker dispatch.
"""

import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

from src import (
    config,
    shopee_client,
    shopee_auth,
    label_processor,
    telegram_sender,
    state_manager,
    balance_dispatcher,
    balance_throttle,
)

JAKARTA_TZ = timezone(timedelta(hours=7))


def _now_jakarta_hhmm():
    """Returns the current time in Jakarta as a HH:MM string."""
    return datetime.now(JAKARTA_TZ).strftime("%H:%M")


def _format_balance_line(balance_result):
    """Formats the balance-dispatch line appended to the heartbeat.

    Returns an empty string when there is nothing pending, so heartbeats on
    zero-order runs are unchanged.
    """
    requested = balance_result["requested"]
    if requested == 0:
        return ""

    if balance_result.get("throttled"):
        # Dispatch was deferred to stay under one balance run per window.
        return (
            f"\n⏳ Stock Balance: {requested} SKU menunggu "
            f"(maks. 1× / {balance_throttle.MIN_INTERVAL_HOURS} jam)"
        )

    dispatched = balance_result["dispatched"]
    failed = balance_result["failed"]

    line = f"\n⚖️ Stock Balance: {dispatched}/{requested} SKU dipicu"
    if failed:
        line += f"\n⚠️ {failed} SKU gagal dipicu (akan dicoba lagi)"
    return line


def _run_throttled_balance(balance):
    """Dispatch /stock_balance at most once per balance_throttle.MIN_INTERVAL_HOURS.

    Base SKUs touched while throttled are accumulated in the throttle state and
    flushed together when the window reopens, so no SKU is dropped even though
    orders are marked processed immediately. Best-effort: never raises.
    """
    touched = balance.collected()
    state = balance_throttle.load()
    pending = balance_throttle.merge_pending(state, touched)
    last = state.get("last_dispatch_at")

    if not pending:
        balance_throttle.save({"last_dispatch_at": last, "pending_skus": []})
        return {"requested": 0, "dispatched": 0, "failed": 0, "skus": [], "throttled": False}

    if not balance_throttle.window_open(state):
        balance_throttle.save({"last_dispatch_at": last, "pending_skus": pending})
        return {
            "requested": len(pending),
            "dispatched": 0,
            "failed": 0,
            "skus": pending,
            "throttled": True,
        }

    # Window is open: flush all pending SKUs in a single dispatch.
    flush = balance_dispatcher.BalanceDispatcher()
    for sku in pending:
        flush.record(sku)
    result = flush.dispatch_all()

    if result["dispatched"] > 0:
        # Success: reset the window and clear the pending queue.
        balance_throttle.save({"last_dispatch_at": state_manager.now_iso(), "pending_skus": []})
    else:
        # Dispatch failed (e.g. missing token): keep pending, retry next window.
        balance_throttle.save({"last_dispatch_at": last, "pending_skus": pending})
    result["throttled"] = False
    return result



def _pick_balance_sku(item):
    """
    Returns the SKU to record into the balance dispatcher for one order item.

    Mirrors telegram_sender._pick_sku for the first two tiers (variant SKU,
    then parent SKU), so /stock_balance receives the exact same SKU the
    operator sees in the Telegram label caption. The item_name fallback
    used by the caption is deliberately omitted here — an item name is not
    a valid stock-bot catalog key, so we'd rather skip the recording (and
    miss a balance dispatch for that item) than feed garbage to the stock
    bot and trigger a spurious "tidak ditemukan" alert.

    Returns an empty string when both SKUs are missing; the caller should
    skip recording in that case.
    """
    model_sku = (item.get("model_sku") or "").strip()
    if model_sku:
        return model_sku
    return (item.get("item_sku") or "").strip()


def _is_ready_to_ship(order):
    """Pre-check before v2.logistics.ship_order per Shopee Open Platform.

    Returns True only when the package's fulfillment_status is
    LOGISTICS_READY and is_shipment_arranged is False. Any other
    state — still allocating, already arranged, detail call error,
    or missing package_number — returns False so the caller skips
    this order and retries on the next scheduled run.

    This protects the v2.logistics.ship_order daily success rate
    (>90% required, monitored over 7 consecutive days) by skipping
    the three failure modes Shopee documents:
      1. Package not in LOGISTICS_READY status ("Package is not
         ready to ship").
      2. Package already arranged ("This parcel has already been
         shipped").
      3. Order still in logistics-channel allocation ("The order
         is being allocated, please wait").

    Args:
      order: an order dict from shopee_client.get_pending_orders().
        Must include "order_sn" and "package_list".

    Returns:
      True if safe to call ship_order_to_dropoff(order_sn), else False.
    """
    order_sn = order["order_sn"]

    packages = order.get("package_list") or []
    if not packages:
        print(f"  ⏭️ {order_sn}: package_list kosong di order detail; "
              f"skip, akan dicoba lagi next run.")
        return False

    package_number = (packages[0].get("package_number") or "").strip()
    if not package_number:
        print(f"  ⏭️ {order_sn}: package_number kosong; "
              f"skip, akan dicoba lagi next run.")
        return False

    try:
        resp = shopee_client.get_package_detail(order_sn, package_number)
    except Exception as e:
        print(f"  ⏭️ {order_sn}/{package_number}: "
              f"get_package_detail gagal: {e}; skip.")
        return False

    body = (resp or {}).get("response") or {}
    returned_packages = body.get("package_list") or []
    if not returned_packages:
        print(f"  ⏭️ {order_sn}/{package_number}: "
              f"response.package_list kosong; skip, akan dicoba lagi next run.")
        return False

    pkg = returned_packages[0]
    fulfillment_status = pkg.get("fulfillment_status")
    is_shipment_arranged = bool(pkg.get("is_shipment_arranged", False))

    if fulfillment_status != "LOGISTICS_READY" or is_shipment_arranged:
        print(
            f"  ⏭️ {order_sn}/{package_number} belum siap dikirim "
            f"(fulfillment_status={fulfillment_status}, "
            f"is_shipment_arranged={is_shipment_arranged}); "
            f"skip, akan dicoba lagi next run."
        )
        return False

    return True


def _emit_has_work(value: bool) -> None:
    """Write the precheck result to GITHUB_OUTPUT (and the log).

    The workflow installs poppler and runs the full bot unless this is
    explicitly `false`, so a missing/garbled output fails safe to "run".
    """
    text = "true" if value else "false"
    print(f"[precheck] has_work={text}")
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        try:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(f"has_work={text}\n")
        except OSError as e:
            print(f"[precheck] could not write GITHUB_OUTPUT: {e}")


def run_precheck():
    """Lightweight 'is there work?' probe that needs no poppler.

    Reuses the exact order-detection of `_do_run`. On a clean zero-new-orders
    result it sends the heartbeat + saves state (identical to an empty full
    run) and emits `has_work=false` so the workflow can skip poppler and the
    full run. On ANY work, error, or uncertainty it emits `has_work=true` so
    the full run proceeds as today. Always exits 0 so it never fails the job.
    """
    try:
        _do_run(precheck=True)
    except Exception as e:  # fail safe: defer to the full run
        print(f"[precheck] error; deferring to full run: {e}")
        _emit_has_work(True)


def run(precheck=False):
    """Runs one full cycle. Returns nothing. Prints progress to stdout."""

    print("=" * 60)
    print("ITBisa Shopee Order Bot - starting run")
    print("=" * 60)

    # STEP 0: Wrap the whole run so operational failures are visible in Telegram.
    # Refresh-token expiry gets a specific message because it needs manual
    # re-authorization. Other errors are also reported so the operator does
    # not need to discover failures only from the GitHub Actions tab.
    try:
        _do_run(precheck=precheck)
    except shopee_auth.RefreshTokenExpiredError as e:
        # This happens roughly once every 30 days. The bot cannot recover
        # on its own, so we notify the shop owner via Telegram and exit.
        alert = (
            f"🔐 {_now_jakarta_hhmm()} - Otorisasi Shopee kadaluarsa. "
            f"Mohon otorisasi ulang aplikasi di Shopee Open Platform Console, "
            f"lalu update file data/shopee_tokens.json dengan token baru."
        )
        telegram_sender.send_summary(alert)
        print(f"\n{alert}")
        print(f"Details: {e}")
        sys.exit(1)
    except Exception as e:
        alert = f"❌ {_now_jakarta_hhmm()} - Error bot Shopee: {e}"
        telegram_sender.send_summary(alert)
        print(f"\n{alert}")
        traceback.print_exc()
        sys.exit(1)


def _do_run(precheck=False):
    """The actual run logic. Separated so run() can wrap it in error handling.

    When `precheck=True`, stop after deciding whether there is work: on no new
    orders, send the heartbeat + save state (as on an empty run) and emit
    `has_work=false`; otherwise emit `has_work=true` and return without doing
    the (poppler-dependent) label work — the full run handles that.
    """

    # STEP 1: Load the dictionary of orders we already processed.
    processed = state_manager.load()
    print(f"Loaded state: {len(processed)} previously processed orders remembered")

    # Per-run collector for shipped base SKUs. Populated only when Telegram
    # confirms label delivery. Dispatched after the order loop finishes.
    balance = balance_dispatcher.BalanceDispatcher()

    # STEP 2: Fetch all orders that need a label printed. This includes both
    # READY_TO_SHIP (need shipment arrangement first) and PROCESSED (label
    # is being generated or ready). The token will be refreshed automatically
    # by shopee_client if needed.
    print("Fetching pending orders from Shopee...")
    orders = shopee_client.get_pending_orders()
    print(f"Shopee returned {len(orders)} pending orders")

    # STEP 3: Filter out orders we already processed in a previous run,
    # then sort them by order_sn ascending so Telegram receives labels in a
    # stable deterministic order.
    new_orders = sorted(
        (o for o in orders if o["order_sn"] not in processed),
        key=lambda o: str(o["order_sn"]),
    )
    print(f"Of those, {len(new_orders)} are new and need processing")

    # STEP 4: If there are no new orders, send a heartbeat and exit.
    # No balance dispatch because nothing shipped.
    if not new_orders:
        # Persist pruning from state_manager.load() even on heartbeat-only runs.
        state_manager.save(processed)

        summary = telegram_sender.build_summary(_now_jakarta_hhmm(), 0, 0)
        telegram_sender.send_summary(summary)
        print(f"Sent heartbeat: {summary}")
        if precheck:
            _emit_has_work(False)
        return

    # In precheck mode there IS work: tell the workflow to install poppler and
    # run the full processor (which re-fetches and does the actual work).
    if precheck:
        _emit_has_work(True)
        return

    # STEP 5: Safety check. If we suddenly see too many orders, something is
    # probably wrong (e.g. state file got deleted). Stop and alert instead of
    # flooding the employee with hundreds of Telegram messages.
    if len(new_orders) > config.MAX_ORDERS_PER_RUN:
        warning = telegram_sender.build_safety_stop_message(
            _now_jakarta_hhmm(), len(new_orders), config.MAX_ORDERS_PER_RUN
        )
        telegram_sender.send_summary(warning)
        print(warning)
        sys.exit(1)

    # STEP 6: Process each new order one at a time.
    success_count = 0
    skipped_count = 0
    for order in new_orders:
        order_sn = order["order_sn"]
        order_status = order.get("order_status", "UNKNOWN")
        print(f"\nProcessing order {order_sn} (status: {order_status})...")

        # STEP 6a: If the order is still in READY_TO_SHIP, we need to tell
        # Shopee to arrange shipment first. We always use dropoff because
        # the warehouse drops packages at the courier counter at end of day.
        # After this call, the order moves to PROCESSED and Shopee starts
        # generating the shipping label.
        #
        # Pre-check the package with get_package_detail before calling
        # v2.logistics.ship_order. Skip silently when not ready so we
        # don't burn the API call success rate on predictable failures
        # (still allocating / already arranged / package missing).
        if order_status == "READY_TO_SHIP":
            if not _is_ready_to_ship(order):
                skipped_count += 1
                continue

            try:
                print(f"  Arranging dropoff shipment for {order_sn}...")
                shopee_client.ship_order_to_dropoff(order_sn)
                print(f"  Shipment arranged. Label generation will start.")
            except Exception as e:
                print(f"  ✗ Failed to arrange shipment for {order_sn}: {e}")
                print(f"    Will retry next run.")
                skipped_count += 1
                continue

        # STEP 6b: Get the shipping label PDF from Shopee. The retry logic
        # inside get_shipping_label_pdf handles the case where Shopee is
        # still generating the label when we ask for it.
        pdf_bytes = shopee_client.get_shipping_label_pdf(order_sn)
        if pdf_bytes is None:
            print(f"  Skipping {order_sn} (label not ready). Will retry next run.")
            skipped_count += 1
            continue

        # STEP 6c: Convert the PDF into Telegram-ready PNG images.
        # Multiple PDF pages are merged two pages per image to reduce
        # Telegram messages while keeping the label order unchanged.
        png_pages = label_processor.pdf_to_pngs(pdf_bytes)
        print(f"  Rendered {len(png_pages)} Telegram label image(s) from PDF")

        # STEP 6d: Build the caption and send all label images to Telegram.
        caption = telegram_sender.build_caption(order)
        delivered = telegram_sender.send_label(png_pages, caption)

        # STEP 6e: Only mark and save as processed if Telegram confirmed delivery.
        # This is the safety rule: if Telegram fails, we want the next run
        # to retry this order, not silently skip it forever.
        #
        # Save immediately after each successful label so partial progress
        # survives even if a later order crashes before the final save.
        if delivered:
            processed[order_sn] = state_manager.now_iso()
            state_manager.save(processed)
            success_count += 1
            print(f"  ✓ Sent to Telegram, saved state, and marked as processed")

            # STEP 6f: Record each shipped variant SKU from this delivered
            # order so the affected base SKU(s) get a balance dispatch after
            # the loop. _pick_balance_sku mirrors the caption's SKU choice
            # (variant SKU first, parent SKU fallback) so /stock_balance
            # receives the exact same SKU the operator sees in the label
            # message — never the parent product SKU like "ITBISA-RESISTOR".
            # Recording happens only on successful delivery — failed Telegram
            # sends are excluded and will be picked up on the next /resi_* run.
            for item in order.get("item_list", []) or []:
                sku = _pick_balance_sku(item)
                if sku:
                    balance.record(sku)
        else:
            print(f"  ✗ Telegram delivery failed. Will retry next run.")
            skipped_count += 1

    # STEP 7: Save once more at the end. Successful orders are already saved
    # immediately after Telegram delivery; this final save keeps the file in
    # sync with the in-memory state before the workflow commits bot-state.
    state_manager.save(processed)
    print(f"\nState saved.")

    # STEP 8: Dispatch /stock_balance for the base SKUs touched this run,
    # throttled to at most one dispatch per balance_throttle.MIN_INTERVAL_HOURS.
    # SKUs touched while throttled are accumulated and flushed when the window
    # reopens. Best-effort: never fails the order run. Labels are the critical
    # path.
    balance_result = _run_throttled_balance(balance)

    # STEP 9: Send a summary heartbeat so the employee knows what happened,
    # including the balance dispatch outcome.
    summary = telegram_sender.build_summary(
        _now_jakarta_hhmm(), success_count, skipped_count
    )
    summary += _format_balance_line(balance_result)
    telegram_sender.send_summary(summary)

    # STEP 10: Print a summary so the GitHub Actions log is easy to scan.
    print("=" * 60)
    print(f"Run complete: {success_count} sent, {skipped_count} skipped")
    print(
        f"Balance: {balance_result['dispatched']}/{balance_result['requested']} "
        f"SKU dispatched"
    )
    print("=" * 60)


if __name__ == "__main__":
    if "--precheck" in sys.argv:
        run_precheck()
    else:
        run()
