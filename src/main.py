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
     a. If READY_TO_SHIP, call ship_order_to_dropoff to arrange shipment
        (equivalent to clicking "Atur Pengiriman" -> "Antar ke Counter"
        in the Shopee Seller app). This moves the order to PROCESSED.
     b. Fetch the shipping label PDF (with retries while Shopee generates it).
     c. Convert PDF to PNG, send to Telegram, mark as processed only AFTER
        Telegram confirms delivery.
  6. Save the updated state file so future runs remember.
  7. Send a heartbeat summary to Telegram so the employee knows the bot
     is alive, even if there were no orders this run.

The GitHub Actions workflow runs this script on a schedule.
"""

import sys
from datetime import datetime, timezone, timedelta

from src import (
    config,
    shopee_client,
    shopee_auth,
    label_processor,
    telegram_sender,
    state_manager,
)


# Jakarta is UTC+7. We use this to format the time in summaries.
JAKARTA_TZ = timezone(timedelta(hours=7))


def _now_jakarta_hhmm():
    """Returns the current time in Jakarta as a HH:MM string."""
    return datetime.now(JAKARTA_TZ).strftime("%H:%M")


def run():
    """Runs one full cycle. Returns nothing. Prints progress to stdout."""

    print("=" * 60)
    print("ITBisa Shopee Order Bot - starting run")
    print("=" * 60)

    # STEP 0: Wrap the whole run so operational failures are visible in Telegram.
    # Refresh-token expiry gets a specific message because it needs manual
    # re-authorization. Other errors are also reported so the operator does
    # not need to discover failures only from the GitHub Actions tab.
    try:
        _do_run()
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
        sys.exit(1)


def _do_run():
    """The actual run logic. Separated so run() can wrap it in error handling."""

    # STEP 1: Load the dictionary of orders we already processed.
    processed = state_manager.load()
    print(f"Loaded state: {len(processed)} previously processed orders remembered")

    # STEP 2: Fetch all orders that need a label printed. This includes both
    # READY_TO_SHIP (need shipment arrangement first) and PROCESSED (label
    # is being generated or ready). The token will be refreshed automatically
    # by shopee_client if needed.
    print("Fetching pending orders from Shopee...")
    orders = shopee_client.get_pending_orders()
    print(f"Shopee returned {len(orders)} pending orders")

    # STEP 3: Filter out orders we already processed in a previous run,
    # then sort them by order_sn ascending so Telegram receives labels in a
    # stable oldest-to-newest order.
    new_orders = sorted(
        (o for o in orders if o["order_sn"] not in processed),
        key=lambda o: str(o["order_sn"]),
    )
    print(f"Of those, {len(new_orders)} are new and need processing")

    # STEP 4: If there are no new orders, send a heartbeat and exit.
    # We still send a message so the employee knows the bot is healthy.
    if not new_orders:
        summary = telegram_sender.build_summary(_now_jakarta_hhmm(), 0, 0)
        telegram_sender.send_summary(summary)
        print(f"Sent heartbeat: {summary}")
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
        if order_status == "READY_TO_SHIP":
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

        # STEP 6c: Convert the PDF to one or more PNG images.
        png_pages = label_processor.pdf_to_pngs(pdf_bytes)
        print(f"  Rendered {len(png_pages)} label page(s) from PDF")

        # STEP 6d: Build the caption and send all pages to Telegram.
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
        else:
            print(f"  ✗ Telegram delivery failed. Will retry next run.")
            skipped_count += 1

    # STEP 7: Save the updated state file so the next run remembers what we did.
    state_manager.save(processed)
    print(f"\nState saved.")

    # STEP 8: Send a summary heartbeat so the employee knows what happened.
    summary = telegram_sender.build_summary(
        _now_jakarta_hhmm(), success_count, skipped_count
    )
    telegram_sender.send_summary(summary)

    # STEP 9: Print a summary so the GitHub Actions log is easy to scan.
    print("=" * 60)
    print(f"Run complete: {success_count} sent, {skipped_count} skipped")
    print("=" * 60)


if __name__ == "__main__":
    run()
