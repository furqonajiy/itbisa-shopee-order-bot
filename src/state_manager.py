"""
state_manager.py
----------------
Loads and saves the JSON file that remembers which orders we already processed.

Why this file exists:
  Every GitHub Actions run starts on a fresh machine with no memory of previous
  runs. Before the bot starts, the workflow overlays data/ files from the
  bot-state branch into the working directory. To avoid sending the same
  shipping label twice, we keep a small JSON file that maps each processed
  order_sn to the timestamp when Telegram confirmed delivery.

  This module only reads and writes the local JSON file. The GitHub Actions
  workflow is responsible for committing that file back to the bot-state branch.

The file format looks like this:
  {
    "2604186D4MY0Y0": "2026-04-18T10:00:00+00:00",
    "2604186D4MY0Y1": "2026-04-18T11:00:00+00:00"
  }
"""

import json
import os
from datetime import datetime, timedelta, timezone

from src import config


def load():
    """
    Loads the processed orders dictionary from disk.

    Also returns a pruned view without entries older than STATE_RETENTION_DAYS,
    because airway bills are no longer useful after that window. The pruned
    result is persisted when save() is called later in the run.

    Returns:
      dict mapping order_sn (str) -> processed_at_iso_timestamp (str)
    """

    # STEP 1: If the file does not exist yet (first ever run), return empty dict.
    if not os.path.exists(config.STATE_FILE_PATH):
        return {}

    # STEP 2: Read the JSON file from disk.
    with open(config.STATE_FILE_PATH, "r") as f:
        state = json.load(f)

    # STEP 3: Remove entries older than the retention period.
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.STATE_RETENTION_DAYS)
    pruned_state = {}
    for order_id, processed_at_iso in state.items():
        processed_at = datetime.fromisoformat(processed_at_iso)
        if processed_at >= cutoff:
            pruned_state[order_id] = processed_at_iso

    # STEP 4: Return the cleaned-up dictionary.
    return pruned_state


def save(state):
    """
    Writes the processed orders dictionary to disk.

    Note: this function only writes the file. The git commit and push
    happens later in the GitHub Actions workflow, not here.

    Args:
      state: dict mapping order_sn -> processed_at_iso_timestamp
    """

    # STEP 1: Make sure the data folder exists.
    os.makedirs(os.path.dirname(config.STATE_FILE_PATH), exist_ok=True)

    # STEP 2: Write the JSON file with nice indentation so it is easy to read
    # in the git history when we look at commit diffs.
    _atomic_write_json(config.STATE_FILE_PATH, state)


def now_iso():
    """
    Returns the current UTC time as an ISO 8601 string.

    We put this here because state_manager is the only module that cares
    about timestamps for processed orders.
    """
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path, data):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)
