#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PSA Caución Bot – Backend
- Scraping real BYMA
- Histórico append-only en CSV
- Genera docs/data/dashboard.json
"""

import csv
import json
import os
import requests
from datetime import datetime, timezone


# =========================
# Config
# =========================

BYMA_CAUCIONES_URL = "https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free/cauciones"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}

TIMEOUT = 15

DATA_DIR = os.path.join("docs", "data")
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")


# =========================
# Utils
# =========================

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# =========================
# Scraping BYMA
# =========================

def fetch_bymadata() -> list[dict]:
    r = requests.get(BYMA_CAUCIONES_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def parse_latest_rate(rows: list[dict], plazo_dias: int) -> float | None:
    """
    Extrae la última TNA para un plazo dado (1D, 7D).
    """
    filtered = [
        r for r in rows
        if r.get("plazo") == plazo_dias and r.get("tna") is not None
    ]
    if not filtered:
        return None

    filtered.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    return float(filtered[0]["tna"])


# =========================
# Histórico CSV
# =========================

CSV_FIELDS = [
    "timestamp_utc",
    "source",
    "term",
    "tna",
]


def load_existing_keys(csv_path: str) -> set[tuple]:
    """
    Devuelve set de (timestamp_utc, term) ya guardados
    para evitar duplicados.
    """
    keys = set()
    if not os.path.exists(csv_path):
        return keys

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            keys.add((row["timestamp_utc"], row["term"]))
    return keys


def append_history(rows: list[dict]) -> int:
    """
    Agrega filas nuevas al CSV (append-only).
    Deduplica por (timestamp_utc, term).
    """
    ensure_dir(DATA_DIR)

    existing_keys = load_existing_keys(HISTORY_CSV)
    wrote = 0

    file_exists = os.path.exists(HISTORY_CSV)

    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)

        if not file_exists:
            writer.writeheader()

        for row in rows:
            key = (row["timestamp_utc"], row["term"])
            if key in existing_keys:
                continue

            writer.writerow(row)
            wrote += 1

    return wrote


# =========================
# Payload builder
# =========================

def build_dashboard_payload() -> dict:
    now = now_utc_iso()

    quality = "ok"
    source = "BYMA"

    try:
        rows = fetch_bymadata()
        tna_1d = parse_latest_rate(rows, 1)
        tna_7d = parse_latest_rate(rows, 7)
    except Exception as e:
        print("ERROR scraping BYMA:", e)
        tna_1d = None
        tna_7d = None
        quality = "error"

    # --- histórico ---
    history_rows = []

    if tna_1d is not None:
        history_rows.append({
            "timestamp_utc": now,
            "source": source,
            "term": "1D",
            "tna": round(tna_1d, 4),
        })

    if tna_7d is not None:
        history_rows.append({
            "timestamp_utc": now,
            "source": source,
            "term": "7D",
            "tna": round(tna_7d, 4),
        })

    appended = append_history(history_rows) if quality == "ok" else 0

    # --- series para dashboard (snapshot, por ahora) ---
    series_1d = [{"t": now, "v": tna_1d}] if tna_1d is not None else []
    series_7d = [{"t": now, "v": tna_7d}] if tna_7d is not None else []
    spread_series = (
        [{"t": now, "v": round(tna_7d - tna_1d, 2)}]
        if tna_1d is not None and tna_7d is not None
        else []
    )

    payload = {
        "generated_at": now,

        "meta": {
            "csv_path": HISTORY_CSV,
            "rows_appended": appended,
        },

        "kpis": {
            "updated_at": now,
            "source": source,
            "last_1d": tna_1d,
            "last_7d": tna_7d,
            "spread_7d_1d": (
                None if tna_1d is None or tna_7d is None
                else round(tna_7d - tna_1d, 2)
            ),
            "band_1d": "N/A",
            "n_1d_60d": None,  # se completa cuando leamos histórico
        },

        "pctls": {
            "1D": {"p40": None, "p60": None, "p75": None, "n": 0}
        },

        "data": {
            "series": {
                "1D": series_1d,
                "7D": series_7d,
                "spread_7d_1d": spread_series,
            }
        },

        "events": {
            "band_changes_1d": []
        },

        "quality": quality
    }

    return payload


# =========================
# Main
# =========================

def main() -> None:
    ensure_dir(DATA_DIR)

    payload = build_dashboard_payload()

    dashboard_path = os.path.join(DATA_DIR, "dashboard.json")
    latest_path = os.path.join(DATA_DIR, "latest.json")

    write_json(dashboard_path, payload)
    write_json(latest_path, payload)

    print("OK")
    print(f"- dashboard: {dashboard_path}")
    print(f"- latest:    {latest_path}")
    print(f"- quality:   {payload['quality']}")
    print(f"- history:   {HISTORY_CSV}")


if __name__ == "__main__":
    main()
