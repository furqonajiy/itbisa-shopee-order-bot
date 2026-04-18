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

The GitHub Actions workflow runs this script on a schedule.
"""

import sys

from src import config, shopee_client, label_processor, telegram_sender, state_manager


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

    if not new_orders:
        print("Nothing to do. Exiting.")
        return

    # STEP 4: Safety check. If we suddenly see too many orders, something is
    # probably wrong (e.g. state file got deleted). Stop and alert instead of
    # flooding the employee with hundreds of Telegram messages.
    if len(new_orders) > config.MAX_ORDERS_PER_RUN:
        print(
            f"⚠️  SAFETY STOP: {len(new_orders)} new orders exceeds the cap "
            f"of {config.MAX_ORDERS_PER_RUN}. Investigate before continuing."
        )
        sys.exit(1)

    # STEP 5: Process each new order one at a time.
    success_count = 0
    skipped_count = 0
    for order in new_orders:
        order_sn = order["order_sn"]
        print(f"\nProcessing order {order_sn}...")

        # STEP 5a: Get the shipping label PDF from Shopee.
        pdf_bytes = shopee_client.get_shipping_label_pdf(order_sn)
        if pdf_bytes is None:
            print(f"  Skipping {order_sn} (label not ready). Will retry next run.")
            skipped_count += 1
            continue

        # STEP 5b: Convert the PDF to a PNG image.
        png_bytes = label_processor.pdf_to_png(pdf_bytes)

        # STEP 5c: Build the caption and send to Telegram.
        caption = telegram_sender.build_caption(order)
        delivered = telegram_sender.send_label(png_bytes, caption)

        # STEP 5d: Only mark as processed if Telegram confirmed delivery.
        # This is the safety rule: if Telegram fails, we want the next run
        # to retry this order, not silently skip it forever.
        if delivered:
            processed[order_sn] = state_manager.now_iso()
            success_count += 1
            print(f"  ✓ Sent to Telegram and marked as processed")
        else:
            print(f"  ✗ Telegram delivery failed. Will retry next run.")
            skipped_count += 1

    # STEP 6: Save the updated state file so the next run remembers what we did.
    state_manager.save(processed)
    print(f"\nState saved.")

    # STEP 7: Print a summary so the GitHub Actions log is easy to scan.
    print("=" * 60)
    print(f"Run complete: {success_count} sent, {skipped_count} skipped")
    print("=" * 60)


if __name__ == "__main__":
    run()
