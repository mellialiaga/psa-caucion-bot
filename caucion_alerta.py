#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PSA Caución Bot – Backend con scraping real BYMA
Genera docs/data/dashboard.json
"""

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


# =========================
# Utils
# =========================

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# =========================
# Scraping BYMA
# =========================

def fetch_bymadata() -> list[dict]:
    """
    Devuelve lista cruda de cauciones BYMA.
    """
    r = requests.get(BYMA_CAUCIONES_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def parse_latest_rate(rows: list[dict], plazo_dias: int) -> float | None:
    """
    Extrae la última tasa TNA para un plazo dado (1D, 7D).
    """
    filtered = [
        r for r in rows
        if r.get("plazo") == plazo_dias and r.get("tna") is not None
    ]
    if not filtered:
        return None

    # ordenar por fecha/hora si existe
    filtered.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    return float(filtered[0]["tna"])


# =========================
# Payload builder
# =========================

def build_dashboard_payload() -> dict:
    now = now_utc_iso()

    try:
        rows = fetch_bymadata()

        tna_1d = parse_latest_rate(rows, 1)
        tna_7d = parse_latest_rate(rows, 7)

        quality = "ok"
        source = "BYMA"

    except Exception as e:
        print("ERROR scraping BYMA:", e)
        tna_1d = None
        tna_7d = None
        quality = "error"
        source = "BYMA"

    # Series formato dashboard {t, v}
    series_1d = []
    series_7d = []
    spread_series = []

    if tna_1d is not None:
        series_1d.append({"t": now, "v": tna_1d})

    if tna_7d is not None:
        series_7d.append({"t": now, "v": tna_7d})

    if tna_1d is not None and tna_7d is not None:
        spread_series.append({"t": now, "v": tna_7d - tna_1d})

    payload = {
        "generated_at": now,

        "meta": {
            "note": "Datos reales BYMA (snapshot, sin histórico aún)",
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
            "n_1d_60d": len(series_1d),
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
    out_dir = os.path.join("docs", "data")
    ensure_dir(out_dir)

    payload = build_dashboard_payload()

    dashboard_path = os.path.join(out_dir, "dashboard.json")
    latest_path = os.path.join(out_dir, "latest.json")

    write_json(dashboard_path, payload)
    write_json(latest_path, payload)

    print("OK")
    print(f"- dashboard: {dashboard_path}")
    print(f"- latest:    {latest_path}")
    print(f"- quality:   {payload['quality']}")


if __name__ == "__main__":
    main()
