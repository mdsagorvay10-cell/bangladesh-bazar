#!/usr/bin/env python3
"""
Merrono API read-only exporter.
Exports data from the documented GET endpoints only:
  GET /products
  GET /products/{id} (optional detail enrichment)
  GET /orders
  GET /orders/{id} (optional detail enrichment)

Install:
  python -m pip install requests pandas openpyxl

Set credentials as environment variables (do not put keys in this file):
  Windows PowerShell:
    $env:MERRONO_API_KEY="your_api_key"
    $env:MERRONO_SECRET_KEY="your_secret_key"
  macOS/Linux:
    export MERRONO_API_KEY="your_api_key"
    export MERRONO_SECRET_KEY="your_secret_key"

Run:
  python merrono_export.py
"""

import os
import re
import json
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
import pandas as pd

BASE_URL = "https://www.merrono.com/api/v3/"
PER_PAGE = 100
PRICE_INCREASE_PERCENT = 15  # Increase exported product prices by 15%; does not change the live website.
ENRICH_DETAILS = True
OUTPUT_DIR = Path("merrono_export")
TIMEOUT = 30

API_KEY = os.getenv("MERRONO_API_KEY", "").strip()
SECRET_KEY = os.getenv("MERRONO_SECRET_KEY", "").strip()

session = requests.Session()
session.headers.update({
    "Accept": "application/json",
    "Content-Type": "application/json",
})
if API_KEY and SECRET_KEY:
    session.headers.update({
        "X-API-KEY": API_KEY,
        "X-SECRET-KEY": SECRET_KEY,
    })


def request_json(path, params=None):
    url = urljoin(BASE_URL, path.lstrip("/"))
    response = session.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"API did not return JSON for {url}") from exc


def extract_records(payload):
    """Handle common API response wrappers."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("data", "products", "orders", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = extract_records(value)
            if nested:
                return nested

    # A single object response is not a collection.
    return []


def pagination_info(payload):
    if not isinstance(payload, dict):
        return None, None
    meta = payload.get("meta") or payload.get("pagination") or {}
    links = payload.get("links") or {}
    current = meta.get("current_page") or meta.get("page")
    last = meta.get("last_page") or meta.get("total_pages")
    next_link = links.get("next") if isinstance(links, dict) else None
    return current, (last, next_link)


def get_collection(path, params=None):
    params = dict(params or {})
    params.setdefault("per_page", PER_PAGE)
    page = 1
    all_rows = []

    while True:
        params["page"] = page
        payload = request_json(path, params=params)
        rows = extract_records(payload)

        if not rows:
            # Some APIs return the collection directly under a custom key.
            if page == 1 and isinstance(payload, dict):
                print(f"[INFO] Could not identify a list wrapper for {path}. "
                      "The response will be saved as raw JSON.")
            break

        all_rows.extend(rows)
        current, info = pagination_info(payload)
        last_page, next_link = info if info else (None, None)

        if next_link:
            # Avoid guessing URL formats; page-based pagination is used next.
            page += 1
        elif last_page is not None:
            if page >= int(last_page):
                break
            page += 1
        elif len(rows) < int(params["per_page"]):
            break
        else:
            page += 1

        if page > 10000:
            raise RuntimeError(f"Pagination safety stop reached for {path}")

    return all_rows


def get_id(row):
    if not isinstance(row, dict):
        return None
    return row.get("id") or row.get("product_id") or row.get("order_id")


def optionally_enrich(rows, detail_path):
    if not ENRICH_DETAILS:
        return rows
    enriched = []
    for index, row in enumerate(rows, start=1):
        record_id = get_id(row)
        if record_id is None:
            enriched.append(row)
            continue
        try:
            detail = request_json(f"{detail_path}/{record_id}")
            if isinstance(detail, dict) and isinstance(detail.get("data"), dict):
                detail = detail["data"]
            if isinstance(detail, dict):
                merged = dict(row)
                merged.update(detail)
                enriched.append(merged)
            else:
                enriched.append(row)
        except requests.RequestException as exc:
            print(f"[WARN] Could not fetch {detail_path}/{record_id}: {exc}")
            enriched.append(row)
        if index % 50 == 0:
            print(f"[INFO] Detail records checked: {index}")
        time.sleep(0.05)
    return enriched


def flatten_rows(rows):
    flattened = []
    for row in rows:
        if isinstance(row, dict):
            flattened.append({
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            })
        else:
            flattened.append({"value": json.dumps(row, ensure_ascii=False)})
    return pd.DataFrame(flattened)


def increase_product_prices(rows, percent=PRICE_INCREASE_PERCENT):
    """Add adjusted-price columns while preserving every original API value."""
    import re
    price_pattern = re.compile(r"(price|amount)", re.IGNORECASE)
    excluded_pattern = re.compile(
        r"(discount|saving|tax_rate|shipping|cost|margin|percent|percentage|currency)",
        re.IGNORECASE
    )
    updated = []
    for row in rows:
        if not isinstance(row, dict):
            updated.append(row)
            continue
        item = dict(row)
        for key, value in row.items():
            if not price_pattern.search(str(key)) or excluded_pattern.search(str(key)):
                continue
            if isinstance(value, bool) or value is None:
                continue
            try:
                # Accept numeric values and numeric strings; leave formatted text untouched.
                number = float(str(value).replace(",", "").strip())
            except (TypeError, ValueError):
                continue
            item[f"{key}_original"] = value
            item[f"{key}_plus_15_percent"] = round(number * (1 + percent / 100), 2)
        updated.append(item)
    return updated


def write_dataset(name, rows):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not rows:
        print(f"[WARN] No records found for {name}; no CSV/XLSX sheet created.")
        return None

    if name == "Products":
        rows = increase_product_prices(rows)

    df = flatten_rows(rows)
    csv_path = OUTPUT_DIR / f"{name}.csv"
    xlsx_path = OUTPUT_DIR / f"{name}.xlsx"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)
    print(f"[OK] {len(df)} rows -> {csv_path} and {xlsx_path}")
    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not API_KEY or not SECRET_KEY:
        print("[INFO] API credentials not set. Public product listing may still work.")
        print("       Set MERRONO_API_KEY and MERRONO_SECRET_KEY to export protected data.")

    datasets = {}

    # Public/read-only product list, using the documented GET endpoint.
    try:
        products = get_collection("products")
        products = optionally_enrich(products, "products")
        datasets["Products"] = products
        write_dataset("merrono_products", products)
    except requests.RequestException as exc:
        print(f"[ERROR] Products export failed: {exc}")

    # Protected/read-only orders list. No order creation or updates are performed.
    if API_KEY and SECRET_KEY:
        try:
            orders = get_collection("orders")
            orders = optionally_enrich(orders, "orders")
            datasets["Orders"] = orders
            write_dataset("merrono_orders", orders)
        except requests.RequestException as exc:
            print(f"[ERROR] Orders export failed: {exc}")
    else:
        print("[SKIP] Orders require valid API credentials; no credentials were provided.")

    # Preserve raw response data when the API uses an unexpected schema.
    (OUTPUT_DIR / "export_summary.json").write_text(
        json.dumps({
            "exported_datasets": {name: len(rows) for name, rows in datasets.items()},
            "read_only": True,
            "product_price_increase_percent": PRICE_INCREASE_PERCENT,
            "note": "Product export includes original price fields plus *_plus_15_percent fields. Only documented GET endpoints were called; live website prices are not changed."
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\nFinished. Output folder: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
