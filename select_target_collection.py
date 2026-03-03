#!/usr/bin/env python3
import json
import os
import statistics
import sys
import urllib.error
import urllib.request

API_URL = "https://api.tgmrkt.io/api/v1/gifts/saling"
PAGE_SIZE = 50


def post_page(token: str, cursor: str):
    payload = {
        "collectionNames": ["Instant Ramen"],
        "modelNames": [],
        "backdropNames": [],
        "symbolNames": [],
        "ordering": "Price",
        "lowToHigh": True,
        "maxPrice": None,
        "minPrice": None,
        "mintable": None,
        "number": None,
        "count": PAGE_SIZE,
        "cursor": cursor,
        "query": None,
        "promotedFirst": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://cdn.tgmrkt.io",
            "Referer": "https://cdn.tgmrkt.io/",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def detect_listings_and_cursor(page: dict):
    listings = []
    next_cursor = ""

    if isinstance(page, dict):
        for list_key in ["gifts", "items", "results", "data", "listings"]:
            value = page.get(list_key)
            if isinstance(value, list):
                listings = value
                break

        if not listings and isinstance(page.get("data"), dict):
            for list_key in ["gifts", "items", "results", "listings"]:
                value = page["data"].get(list_key)
                if isinstance(value, list):
                    listings = value
                    break

        for cursor_key in ["cursor", "nextCursor", "next_cursor"]:
            value = page.get(cursor_key)
            if isinstance(value, str):
                next_cursor = value
                break

        if not next_cursor and isinstance(page.get("data"), dict):
            for cursor_key in ["cursor", "nextCursor", "next_cursor"]:
                value = page["data"].get(cursor_key)
                if isinstance(value, str):
                    next_cursor = value
                    break

    return listings, next_cursor


def get_field(obj: dict, candidates, default=None):
    for key in candidates:
        if key in obj and obj[key] is not None:
            return obj[key]
    return default


def to_ton(value):
    if value is None:
        return None
    try:
        return float(value) / 1_000_000_000
    except (ValueError, TypeError):
        return None


def median(values):
    if not values:
        return None
    return statistics.median(values)


def main():
    token = os.getenv("MRKT_ACCESS_TOKEN") or os.getenv("ACCESS_TOKEN")
    if not token:
        print("Total listings fetched: 0", file=sys.stderr)
        print("Number of collections found: 0", file=sys.stderr)
        print(
            json.dumps(
                {
                    "error": "Missing token: set MRKT_ACCESS_TOKEN or ACCESS_TOKEN environment variable.",
                    "wait": True,
                },
                ensure_ascii=False,
            )
        )
        return 1

    combined = []
    cursor = ""

    while True:
        try:
            page = post_page(token, cursor)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(json.dumps({"error": f"HTTP {e.code}", "body": body, "wait": True}, ensure_ascii=False))
            return 1
        except urllib.error.URLError as e:
            print(json.dumps({"error": f"Network error: {e.reason}", "wait": True}, ensure_ascii=False))
            return 1

        listings, next_cursor = detect_listings_and_cursor(page)
        if not listings:
            break

        combined.extend(listings)
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    print(f"Total listings fetched: {len(combined)}", file=sys.stderr)

    analyzed = []
    prices = []

    for item in combined:
        listing_id = get_field(item, ["id", "listingId", "listing_id"], None)
        sale_price = get_field(item, ["salePrice", "price", "priceNano", "sale_price"], None)
        price_ton = to_ton(sale_price)
        if price_ton is not None:
            prices.append(price_ton)

        model_rarity = get_field(item, ["modelRarityPerMille"], None)
        backdrop_rarity = get_field(item, ["backdropRarityPerMille"], None)
        symbol_rarity = get_field(item, ["symbolRarityPerMille"], None)

        rarity_score = 0.0
        for rarity_value in (model_rarity, backdrop_rarity, symbol_rarity):
            try:
                rarity_num = float(rarity_value)
            except (ValueError, TypeError):
                rarity_num = 0.0
            if rarity_num > 0:
                rarity_score += 1.0 / rarity_num

        if rarity_score == 0 or price_ton is None:
            continue

        fair_ratio = price_ton / rarity_score

        analyzed.append(
            {
                "id": listing_id,
                "price_TON": price_ton,
                "rarity_score": rarity_score,
                "fair_ratio": fair_ratio,
                "modelRarityPerMille": model_rarity,
                "backdropRarityPerMille": backdrop_rarity,
                "symbolRarityPerMille": symbol_rarity,
            }
        )

    if not combined:
        print("Number of collections found: 0", file=sys.stderr)
        print(json.dumps({"error": "No active listings found.", "wait": True}, ensure_ascii=False))
        return 1

    print("Number of collections found: 1", file=sys.stderr)

    floor = min(prices) if prices else None
    fair_ratios = [x["fair_ratio"] for x in analyzed]
    avg_fair_ratio = (sum(fair_ratios) / len(fair_ratios)) if fair_ratios else None
    top_undervalued = sorted(analyzed, key=lambda x: x["fair_ratio"])[:15]

    output = {
        "collection": "Instant Ramen",
        "total_listings": len(combined),
        "average_fair_ratio": avg_fair_ratio,
        "floor_TON": floor,
        "top_undervalued": top_undervalued,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
