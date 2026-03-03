#!/usr/bin/env python3
import json
import math
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


def build_cluster(listings, top_n):
    prices = [item["price_TON"] for item in listings if item.get("price_TON") is not None]
    fair_ratios = [item["fair_ratio"] for item in listings if item.get("fair_ratio") is not None]
    cluster_top = sorted(
        [item for item in listings if item.get("fair_ratio") is not None],
        key=lambda x: x["fair_ratio"],
    )[:top_n]
    return {
        "count": len(listings),
        "floor_TON": min(prices) if prices else None,
        "median_TON": median(prices),
        "average_fair_ratio": (sum(fair_ratios) / len(fair_ratios)) if fair_ratios else None,
        "top_undervalued": cluster_top,
    }


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
    backdrop_groups = {}

    for item in combined:
        listing_id = get_field(item, ["id", "listingId", "listing_id"], None)
        sale_price = get_field(item, ["salePrice", "price", "priceNano", "sale_price"], None)
        price_ton = to_ton(sale_price)
        if price_ton is not None:
            prices.append(price_ton)

        model_rarity = get_field(item, ["modelRarityPerMille"], None)
        backdrop_rarity = get_field(item, ["backdropRarityPerMille"], None)
        symbol_rarity = get_field(item, ["symbolRarityPerMille"], None)

        backdrop_name = str(get_field(item, ["backdropName", "backdrop", "backdrop_name"], "unknown"))
        group = backdrop_groups.setdefault(backdrop_name, {"prices": [], "rarities": []})
        if price_ton is not None:
            group["prices"].append(price_ton)
        try:
            rarity_value = float(backdrop_rarity)
            group["rarities"].append(rarity_value)
        except (ValueError, TypeError):
            pass

        def safe_rarity(raw_value):
            try:
                rarity_num = float(raw_value)
            except (ValueError, TypeError):
                rarity_num = 0.0
            if rarity_num <= 0:
                rarity_num = 1.0
            return rarity_num

        model_component = 1.0 / math.sqrt(safe_rarity(model_rarity))
        symbol_component = 1.0 / math.sqrt(safe_rarity(symbol_rarity))
        rarity_score = model_component + symbol_component

        if rarity_score == 0 or price_ton is None:
            continue

        fair_ratio = price_ton / rarity_score

        analyzed.append(
            {
                "id": listing_id,
                "backdropName": backdrop_name,
                "price_TON": price_ton,
                "rarity_score": rarity_score,
                "base_fair_ratio": fair_ratio,
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

    backdrop_analysis = []
    for backdrop_name, values in backdrop_groups.items():
        group_prices = sorted(values["prices"])
        avg_price = (sum(group_prices) / len(group_prices)) if group_prices else None
        avg_backdrop_rarity = (sum(values["rarities"]) / len(values["rarities"])) if values["rarities"] else None
        backdrop_analysis.append(
            {
                "backdrop": backdrop_name,
                "count": len(group_prices),
                "average_price_TON": avg_price,
                "median_price_TON": median(group_prices),
                "floor_TON": group_prices[0] if group_prices else None,
                "max_TON": group_prices[-1] if group_prices else None,
                "average_backdropRarityPerMille": avg_backdrop_rarity,
            }
        )

    backdrop_analysis = sorted(
        backdrop_analysis,
        key=lambda x: (x["average_price_TON"] is None, -(x["average_price_TON"] or 0)),
    )

    collection_median_price = median(prices)
    premium_backdrops = set()
    if collection_median_price is not None:
        threshold = 2 * collection_median_price
        for entry in backdrop_analysis:
            backdrop_median = entry.get("median_price_TON")
            if backdrop_median is not None and backdrop_median > threshold:
                premium_backdrops.add(entry["backdrop"])

    black_median = None
    for entry in backdrop_analysis:
        if entry.get("backdrop") == "Black":
            black_median = entry.get("median_price_TON")
            break

    premium_multiplier = 1.0
    if (
        collection_median_price is not None
        and black_median is not None
        and collection_median_price > 0
        and black_median > collection_median_price
    ):
        premium_multiplier = 1.0 + math.log(black_median / collection_median_price)
        premium_multiplier = min(premium_multiplier, 3.5)

    for item in analyzed:
        base_fair_ratio = item["base_fair_ratio"]
        if item.get("backdropName") in premium_backdrops:
            item["fair_ratio"] = base_fair_ratio / math.sqrt(premium_multiplier)
        else:
            item["fair_ratio"] = base_fair_ratio

    fair_ratios = [x["fair_ratio"] for x in analyzed]
    avg_fair_ratio = (sum(fair_ratios) / len(fair_ratios)) if fair_ratios else None
    top_undervalued = sorted(analyzed, key=lambda x: x["fair_ratio"])[:15]

    premium_cluster_listings = [item for item in analyzed if item.get("backdropName") in premium_backdrops]
    regular_cluster_listings = [item for item in analyzed if item.get("backdropName") not in premium_backdrops]

    output = {
        "collection": "Instant Ramen",
        "premium_multiplier": premium_multiplier,
        "collection_median_price": collection_median_price,
        "black_median": black_median,
        "premium_backdrops": sorted(premium_backdrops),
        "premium_cluster": build_cluster(premium_cluster_listings, top_n=10),
        "regular_cluster": build_cluster(regular_cluster_listings, top_n=10),
        "total_listings": len(combined),
        "average_fair_ratio": avg_fair_ratio,
        "floor_TON": floor,
        "top_undervalued": top_undervalued,
        "backdrop_analysis": backdrop_analysis,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
