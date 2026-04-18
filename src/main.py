"""
main.py
-------
The entry point. Run this file to do one full processing cycle.

What this script does, in order:
  1. Load the list of order IDs we already processed.
  2. Ask Shopee for orders that are ready to ship.
  3. Filter out the ones we already handled.
  4. Stop if there are too many (safety check).
  5. For each new order: fetch the label, convert it, send to Telegram,
     and only mark it as processed AFTER Telegram confirms delivery.
  6. Save the updated state file so future runs remember.
  7. Send a heartbeat summary to Telegram so the employee knows the bot
     is alive, even if there were no orders this run.

The GitHub Actions workflow runs this script on a schedule.
"""

import sys
from datetime import datetime, timezone, timedelta

from src import config, shopee_client, label_processor, telegram_sender, state_manager


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

    # STEP 1: Load the dictionary of orders we already processed.
    processed = state_manager.load()
    print(f"Loaded state: {len(processed)} previously processed orders remembered")

    # STEP 2: Fetch the current list of ready-to-ship orders from Shopee.
    print("Fetching orders from Shopee...")
    orders = shopee_client.get_ready_to_ship_orders()
    print(f"Shopee returned {len(orders)} ready-to-ship orders")

    # STEP 3: Filter out orders we already processed in a previous run.
    new_orders = [o for o in orders if o["order_sn"] not in processed]
    print(f"Of those, {len(new_orders)} are new and need processing")

    # STEP 4: If there are no new orders, send a heartbeat and exit.
    # We still send a message so the employee knows the bot is healthy.
    if not new_orders:
        summary = f"✅ {_now_jakarta_hhmm()} - 0 new orders"
        telegram_sender.send_summary(summary)
        print(f"Sent heartbeat: {summary}")
        return

    # STEP 5: Safety check. If we suddenly see too many orders, something is
    # probably wrong (e.g. state file got deleted). Stop and alert instead of
    # flooding the employee with hundreds of Telegram messages.
    if len(new_orders) > config.MAX_ORDERS_PER_RUN:
        warning = (
            f"⚠️ {_now_jakarta_hhmm()} - SAFETY STOP: {len(new_orders)} new "
            f"orders exceeds the cap of {config.MAX_ORDERS_PER_RUN}. "
            f"Investigate before continuing."
        )
        telegram_sender.send_summary(warning)
        print(warning)
        sys.exit(1)

    # STEP 6: Process each new order one at a time.
    success_count = 0
    skipped_count = 0
    for order in new_orders:
        order_sn = order["order_sn"]
        print(f"\nProcessing order {order_sn}...")

        # STEP 6a: Get the shipping label PDF from Shopee.
        pdf_bytes = shopee_client.get_shipping_label_pdf(order_sn)
        if pdf_bytes is None:
            print(f"  Skipping {order_sn} (label not ready). Will retry next run.")
            skipped_count += 1
            continue

        # STEP 6b: Convert the PDF to a PNG image.
        png_bytes = label_processor.pdf_to_png(pdf_bytes)

        # STEP 6c: Build the caption and send to Telegram.
        caption = telegram_sender.build_caption(order)
        delivered = telegram_sender.send_label(png_bytes, caption)

        # STEP 6d: Only mark as processed if Telegram confirmed delivery.
        # This is the safety rule: if Telegram fails, we want the next run
        # to retry this order, not silently skip it forever.
        if delivered:
            processed[order_sn] = state_manager.now_iso()
            success_count += 1
            print(f"  ✓ Sent to Telegram and marked as processed")
        else:
            print(f"  ✗ Telegram delivery failed. Will retry next run.")
            skipped_count += 1

    # STEP 7: Save the updated state file so the next run remembers what we did.
    state_manager.save(processed)
    print(f"\nState saved.")

    # STEP 8: Send a summary heartbeat so the employee knows what happened.
    if skipped_count == 0:
        summary = f"✅ {_now_jakarta_hhmm()} - {success_count} labels sent"
    else:
        summary = (
            f"⚠️ {_now_jakarta_hhmm()} - {success_count} sent, "
            f"{skipped_count} skipped (will retry next run)"
        )
    telegram_sender.send_summary(summary)

    # STEP 9: Print a summary so the GitHub Actions log is easy to scan.
    print("=" * 60)
    print(f"Run complete: {success_count} sent, {skipped_count} skipped")
    print("=" * 60)


if __name__ == "__main__":
    run()
