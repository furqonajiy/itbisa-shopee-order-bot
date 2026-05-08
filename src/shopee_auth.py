"""
shopee_auth.py
--------------
Manages the Shopee access_token and refresh_token lifecycle.

Why this file exists:
  Shopee tokens expire on a schedule:
    - access_token: valid for 4 hours
    - refresh_token: valid for 30 days

  Since the bot runs several times per day, the access_token often needs
  refreshing before an API call. This module handles that transparently:
  callers just ask for "a valid access token" and this module takes care of
  checking expiry, refreshing if needed, and saving the updated tokens back to
  disk immediately.

  Tokens are stored in data/shopee_tokens.json. The GitHub Actions workflow
  later commits that file back to the bot-state branch together with
  processed_orders.json.

Public functions (used by shopee_client.py):
  - get_valid_access_token() -> string (an access token known to be fresh)

When refresh_token itself expires (every 30 days):
  The refresh call will fail with an error from Shopee. This module raises
  RefreshTokenExpiredError, which main.py catches and reports via Telegram
  so the shop owner can manually re-authorize through the Shopee Console.
"""

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from src import config


# ============================================================
# Custom exception for the one case that needs human intervention
# ============================================================

class RefreshTokenExpiredError(Exception):
    """
    Raised when the refresh_token itself has expired and a human must
    re-authorize the app through the Shopee Open Platform Console.
    """
    pass


# ============================================================
# Public function (used by shopee_client.py)
# ============================================================

def get_valid_access_token():
    """
    Returns an access token that is guaranteed to be fresh.

    If the stored access token is still valid (and will remain valid for at
    least TOKEN_REFRESH_BUFFER_MINUTES), returns it directly. Otherwise,
    refreshes it using the refresh_token and returns the new access token.

    Returns:
      A valid access_token string.

    Raises:
      RefreshTokenExpiredError: if the refresh_token has also expired and
        a human must manually re-authorize the app in Shopee Console.
      FileNotFoundError: if the tokens file does not exist (first-run
        bootstrap not done yet).
    """

    # STEP 1: Load the tokens from disk.
    tokens = _load_tokens()

    # STEP 2: Check if the current access token is still fresh enough.
    if _is_access_token_fresh(tokens):
        print(f"  Access token is still fresh, using it directly")
        return tokens["access_token"]

    # STEP 3: Token is expired or about to expire. Refresh it.
    print(f"  Access token is expired or expiring soon, refreshing...")
    new_tokens = _refresh_tokens(tokens["refresh_token"])

    # STEP 4: Save the new tokens to disk for future runs.
    # The workflow commits this file back to bot-state after the run.
    _save_tokens(new_tokens)
    print(f"  New tokens saved to {config.TOKENS_FILE_PATH}")

    return new_tokens["access_token"]


# ============================================================
# Internal helpers (file I/O)
# ============================================================

def _load_tokens():
    """
    Loads the tokens dictionary from disk.

    The tokens file has this format:
      {
        "access_token": "...",
        "refresh_token": "...",
        "access_token_expires_at": "2026-04-19T14:00:00+00:00"
      }

    Raises:
      FileNotFoundError: if the file does not exist. This means the very
        first-run bootstrap has not been done yet and a human must seed
        the file with initial tokens from the Shopee Console.
    """

    # STEP 1: Check that the file exists. If not, give a clear error message
    # so the developer knows exactly what to do.
    if not os.path.exists(config.TOKENS_FILE_PATH):
        raise FileNotFoundError(
            f"Tokens file not found at {config.TOKENS_FILE_PATH}. "
            f"You need to bootstrap initial tokens by authorizing the app "
            f"in the Shopee Open Platform Console and saving the resulting "
            f"access_token and refresh_token to this file."
        )

    # STEP 2: Read and parse the JSON file.
    with open(config.TOKENS_FILE_PATH, "r") as f:
        return json.load(f)


def _save_tokens(tokens):
    """
    Writes the tokens dictionary to disk with nice formatting.

    Args:
      tokens: dict with access_token, refresh_token, access_token_expires_at.
    """

    # STEP 1: Make sure the data folder exists.
    os.makedirs(os.path.dirname(config.TOKENS_FILE_PATH), exist_ok=True)

    # STEP 2: Write the JSON file with indentation so git diffs are readable.
    _atomic_write_json(config.TOKENS_FILE_PATH, tokens)


# ============================================================
# Internal helpers (freshness check)
# ============================================================

def _is_access_token_fresh(tokens):
    """
    Returns True if the access token will still be valid after the
    configured refresh buffer, False if it needs to be refreshed.

    We refresh a few minutes BEFORE actual expiry so that we never attempt
    an API call with a token that expires mid-flight.
    """

    # STEP 1: Parse the expiry timestamp from the tokens file.
    expires_at_iso = tokens.get("access_token_expires_at")
    if not expires_at_iso:
        # No expiry info means we cannot trust the token, refresh to be safe.
        return False
    expires_at = datetime.fromisoformat(expires_at_iso)

    # STEP 2: Compute the "must refresh before" cutoff time.
    # If the token expires within the next N minutes, treat it as stale.
    buffer = timedelta(minutes=config.TOKEN_REFRESH_BUFFER_MINUTES)
    cutoff = datetime.now(timezone.utc) + buffer

    # STEP 3: Token is fresh if it expires AFTER the cutoff.
    return expires_at > cutoff


# ============================================================
# Internal helpers (the actual refresh API call)
# ============================================================

def _refresh_tokens(refresh_token):
    """
    Calls Shopee's refresh endpoint to exchange the refresh_token for a
    fresh access_token and a fresh refresh_token.

    Important: Shopee rotates the refresh_token on every refresh. We MUST
    save the new refresh_token returned by this call, otherwise the next
    refresh will fail.

    Args:
      refresh_token: the current refresh_token string.

    Returns:
      A dict with access_token, refresh_token, access_token_expires_at.

    Raises:
      RefreshTokenExpiredError: if Shopee says the refresh_token is expired.
    """

    # STEP 1: Build the request. The refresh endpoint uses a slightly different
    # signature format than shop-level calls: only partner_id + path + timestamp.
    path = "/api/v2/auth/access_token/get"
    timestamp = int(time.time())

    base_string = f"{config.SHOPEE_PARTNER_ID}{path}{timestamp}"
    signature = hmac.new(
        config.SHOPEE_PARTNER_KEY.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    url = (
        f"{config.SHOPEE_API_BASE_URL}{path}"
        f"?partner_id={config.SHOPEE_PARTNER_ID}"
        f"&timestamp={timestamp}"
        f"&sign={signature}"
    )

    body = {
        "partner_id": int(config.SHOPEE_PARTNER_ID),
        "refresh_token": refresh_token,
        "shop_id": int(config.SHOPEE_SHOP_ID),
    }

    # STEP 2: Call the refresh endpoint.
    response = requests.post(url, json=body, timeout=30)
    response.raise_for_status()
    data = response.json()

    # STEP 3: Check for the "refresh token expired" error. Shopee returns this
    # with an error field in the JSON body. The exact error string may vary
    # but will contain either "expired" or "invalid" related to the token.
    error = data.get("error", "")
    if error:
        if "token" in error.lower() and ("expired" in error.lower() or "invalid" in error.lower()):
            raise RefreshTokenExpiredError(
                f"Shopee refresh_token is expired or invalid. Please re-authorize "
                f"the app in Shopee Open Platform Console. Error: {error}"
            )
        # Some other error from Shopee. Let it bubble up.
        raise RuntimeError(f"Shopee refresh failed: {data}")

    # STEP 4: Extract the new tokens and compute the absolute expiry timestamp.
    # Shopee returns expire_in as seconds-from-now, we store absolute time
    # because it's easier to compare against "now" on the next run.
    new_access_token = data["access_token"]
    new_refresh_token = data["refresh_token"]
    expire_in_seconds = data["expire_in"]

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expire_in_seconds)

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "access_token_expires_at": expires_at.isoformat(),
    }


def _atomic_write_json(path, data):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)
