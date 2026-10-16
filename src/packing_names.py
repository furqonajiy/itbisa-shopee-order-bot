"""
packing_names.py
----------------
The packer's short name for each SKU, printed on the label in place of the
marketplace's long product title.

Why this file exists:
  A marketplace title is written for search ("ITBisa - Relay 5V 4 Pin
  SRD-05VDC-SL-A SPST-NO 10A | Relay Songle Biru 4P PCB THT ..."), so the packer
  has to read four lines to find the one word that tells two products apart.
  The operator keeps one simple name per base SKU in packing_names.txt in
  itbisa-shop-stock-bot (main), where the [bracketed] part is the word that
  tells a product from its siblings and is printed red. The list is read fresh
  on every run, so an edit there reaches the next label with no deploy here.

File format (one line per base SKU; '#' lines are comments):
  <BASE_SKU> = <name with [red part]> [| <pack count>=<text>]
  "| 6=(4+2)" prints a 6-pack as "(4+2)PCS" instead of "6PCS".

This file is duplicated across both order bots on purpose (like
balance_dispatcher.py): keep the two copies identical.
"""

import base64
import json
import os
import re
from urllib import request

GH_OWNER = "furqonajiy"
STOCK_REPO = "itbisa-shop-stock-bot"
NAMES_PATH = "packing_names.txt"
NAMES_REF = "main"

RED = "red"
BLACK = "black"

# "10PCS-ITBISA-..." is a pack of 10 of the base SKU "ITBISA-...".
PACK_SKU = re.compile(r"^(\d+)PCS-(.+)$")


def parse(text):
    """{BASE_SKU: line} from the file text."""
    names = {}
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and " = " in line:
            sku, value = line.split(" = ", 1)
            names[sku.strip().upper()] = value.strip()
    return names


def load():
    """Reads the list from the stock bot's main branch. Never raises.

    Returns ({BASE_SKU: line}, None) or ({}, short reason). An empty list
    leaves every label exactly as the marketplace printed it.
    """
    token = os.environ.get("STOCK_DISPATCH_TOKEN")
    if not token:
        return {}, "STOCK_DISPATCH_TOKEN tidak ada"
    url = (f"https://api.github.com/repos/{GH_OWNER}/{STOCK_REPO}"
           f"/contents/{NAMES_PATH}?ref={NAMES_REF}")
    req = request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "itbisa-packing-names",
    })
    try:
        with request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = base64.b64decode(body["content"]).decode("utf-8")
    except Exception as e:                                  # noqa: BLE001
        return {}, f"{type(e).__name__}: {e}"[:160]
    names = parse(text)
    return names, None if names else "daftar kosong"


def runs(sku, names):
    """The packing name of a SKU as [(text, colour)], or None if not listed.

    A pack SKU gets its count first, in red: "10PCS Relay [5V 4 Pin] SL-A".
    """
    m = PACK_SKU.match(sku)
    base, count = (m.group(2), int(m.group(1))) if m else (sku, None)
    line = names.get(base.upper())
    if line is None:
        return None
    name, _, options = line.partition(" | ")
    out = []
    if count:
        alias = dict(o.strip().split("=", 1) for o in options.split(";") if "=" in o)
        out.append((f"{alias.get(str(count), count)}PCS ", RED))
    for i, part in enumerate(re.split(r"\[|\]", name)):
        if part:
            out.append((part, RED if i % 2 else BLACK))
    return out


def wrap(segments, width, text_length):
    """Breaks [(text, colour)] into lines no wider than width.

    A red part is never split across lines, and a "(...)" group stays on the
    line of the word before it. text_length(text) measures one piece.
    """
    words = [(w, c) for t, c in segments
             for w in ([t] if c == RED else re.findall(r"\s*\([^)]*\)\s*|\s*\S+\s*", t))]
    groups = []
    for w, c in words:
        if groups and w.strip().startswith("("):
            groups[-1].append((w, c))
        else:
            groups.append([(w, c)])
    lines, current, x = [], [], 0.0
    for group in groups:
        length = sum(text_length(w) for w, _ in group)
        lead = text_length(group[0][0]) - text_length(group[0][0].lstrip())
        trail = text_length(group[-1][0]) - text_length(group[-1][0].rstrip())
        if current and x + length - lead - trail > width:
            lines.append(current)
            current, x = [], 0.0
            group = [(group[0][0].lstrip(), group[0][1])] + group[1:]
        for w, c in group:
            current.append((w, c))
            x += text_length(w)
    if current:
        lines.append(current)
    return lines
