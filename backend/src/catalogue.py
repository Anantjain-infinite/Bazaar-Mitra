"""
catalogue.py — Bazaar Mitra's product catalogue and order-total tool.

DATA SOURCE NOTE (for your README):
There is no public, real-time API for neighbourhood/kirana shop inventory in India — this
is a hand-built local dataset covering a few example shops and products, not a live feed.
In a real deployment, `_fetch_catalogue()` below is the one function you'd swap for a real
vendor inventory API / POS integration. Everything that calls it — timeout handling, the
"as of" date, the failure path — is already written against that swap happening later.

To demo the graceful-failure path (Day 5, step 7), run the agent with:
    CATALOGUE_SIMULATE_DOWN=true python agent.py dev     (macOS/Linux)
    set CATALOGUE_SIMULATE_DOWN=true && python agent.py dev   (Windows cmd)
    $env:CATALOGUE_SIMULATE_DOWN="true"; python agent.py dev  (PowerShell)
then ask about a product — the tool will time out and the agent should say so instead of
guessing a price. Unset it (or set to "false") to go back to normal.
"""

import asyncio
import os

# Update this whenever you edit the data below — the agent tells callers prices/stock
# are "as of" this date, so it should reflect when the dataset was last curated.
CATALOGUE_LAST_UPDATED = "2026-08-09"

_CATALOGUE = [
    {"shop": "Sharma Kirana", "product": "Atta (Wheat Flour)", "category": "groceries", "unit": "kg", "price_per_unit": 42, "stock_qty": 80},
    {"shop": "Sharma Kirana", "product": "Basmati Rice", "category": "groceries", "unit": "kg", "price_per_unit": 95, "stock_qty": 40},
    {"shop": "Sharma Kirana", "product": "Mustard Oil", "category": "groceries", "unit": "litre", "price_per_unit": 165, "stock_qty": 25},
    {"shop": "Sharma Kirana", "product": "Sugar", "category": "groceries", "unit": "kg", "price_per_unit": 48, "stock_qty": 60},
    {"shop": "Patel Electronics", "product": "Wireless Mouse", "category": "electronics", "unit": "piece", "price_per_unit": 449, "stock_qty": 12},
    {"shop": "Patel Electronics", "product": "Wired Mouse", "category": "electronics", "unit": "piece", "price_per_unit": 199, "stock_qty": 20},
    {"shop": "Patel Electronics", "product": "USB Keyboard", "category": "electronics", "unit": "piece", "price_per_unit": 549, "stock_qty": 8},
    {"shop": "Patel Electronics", "product": "HDMI Cable 1.5m", "category": "electronics", "unit": "piece", "price_per_unit": 149, "stock_qty": 30},
    {"shop": "Gupta General Store", "product": "Notebook 200 Pages", "category": "stationery", "unit": "piece", "price_per_unit": 35, "stock_qty": 100},
    {"shop": "Gupta General Store", "product": "Ballpoint Pen (Pack of 5)", "category": "stationery", "unit": "pack", "price_per_unit": 25, "stock_qty": 70},
    {"shop": "Gupta General Store", "product": "Wireless Mouse", "category": "electronics", "unit": "piece", "price_per_unit": 429, "stock_qty": 5},
]

# Flip this on (env var, not code) to rehearse the graceful-failure path for your demo video.
_SIMULATE_DOWN = os.getenv("CATALOGUE_SIMULATE_DOWN", "false").strip().lower() == "true"
_FETCH_TIMEOUT_SECONDS = 3.0


class CatalogueUnavailableError(Exception):
    """Raised when the catalogue/stock service can't be reached in time."""


async def _fetch_catalogue() -> list[dict]:
    """
    Stand-in for a real network call to a vendor inventory / catalogue API.
    Swap this function's body for a real HTTP call when you have one — every caller
    already handles timeouts and unavailability, so nothing else needs to change.
    """
    if _SIMULATE_DOWN:
        # Simulate the kind of slow, doomed request real users hit on a bad connection.
        await asyncio.sleep(_FETCH_TIMEOUT_SECONDS + 1)
    await asyncio.sleep(0.05)  # small simulated latency even on the happy path
    return _CATALOGUE


async def _get_catalogue_or_raise() -> list[dict]:
    try:
        return await asyncio.wait_for(_fetch_catalogue(), timeout=_FETCH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
        raise CatalogueUnavailableError("catalogue service timed out") from e


def _matches(item: dict, query: str, shop: str | None) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return False
    if shop and item["shop"].strip().lower() != shop.strip().lower():
        return False
    return q in item["product"].lower() or q in item["category"].lower()


async def lookup_products(query: str, shop: str | None = None) -> dict:
    """
    Look up products matching `query` (substring match against product name/category),
    optionally filtered to one shop.

    Returns on success:
        {"ok": True, "as_of": "2026-08-09", "matches": [ {shop, product, unit,
         price_per_unit, stock_qty, category}, ... ] }
        (an empty `matches` list means the lookup worked but nothing matched)
    Returns on failure:
        {"ok": False, "error": "..."}
    """
    try:
        catalogue = await _get_catalogue_or_raise()
    except CatalogueUnavailableError as e:
        return {"ok": False, "error": str(e)}

    matches = [item for item in catalogue if _matches(item, query, shop)]
    return {"ok": True, "as_of": CATALOGUE_LAST_UPDATED, "matches": matches}


async def compute_order_total(items: list[dict]) -> dict:
    """
    items: list of {"product": str, "quantity": number, "shop": optional str}

    Returns on success:
        {"ok": True, "as_of": "2026-08-09", "line_items": [...], "total": number,
         "unresolved": [ {product, reason}, ... ]}
        (`unresolved` lists requested items that couldn't be priced — not found, or not
        enough stock — so the caller can be told, instead of silently dropped or guessed)
    Returns on failure:
        {"ok": False, "error": "..."}
    """
    try:
        catalogue = await _get_catalogue_or_raise()
    except CatalogueUnavailableError as e:
        return {"ok": False, "error": str(e)}

    line_items = []
    unresolved = []
    total = 0.0

    for requested in items:
        product = requested.get("product", "")
        shop = requested.get("shop")
        try:
            quantity = float(requested.get("quantity", 0))
        except (TypeError, ValueError):
            quantity = 0

        if quantity <= 0:
            unresolved.append({"product": product, "reason": "no valid quantity given"})
            continue

        matches = [item for item in catalogue if _matches(item, product, shop)]
        if not matches:
            unresolved.append({"product": product, "reason": "not found in catalogue"})
            continue

        # If multiple shops carry it and none was specified, prefer the cheapest.
        chosen = min(matches, key=lambda i: i["price_per_unit"])

        if quantity > chosen["stock_qty"]:
            unresolved.append({
                "product": product,
                "reason": (
                    f"only {chosen['stock_qty']} {chosen['unit']} in stock at "
                    f"{chosen['shop']}, {quantity:g} requested"
                ),
            })
            continue

        line_total = chosen["price_per_unit"] * quantity
        total += line_total
        line_items.append({
            "shop": chosen["shop"],
            "product": chosen["product"],
            "unit": chosen["unit"],
            "quantity": quantity,
            "price_per_unit": chosen["price_per_unit"],
            "line_total": round(line_total, 2),
        })

    return {
        "ok": True,
        "as_of": CATALOGUE_LAST_UPDATED,
        "line_items": line_items,
        "total": round(total, 2),
        "unresolved": unresolved,
    }