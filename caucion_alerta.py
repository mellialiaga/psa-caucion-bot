#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PSA Caución Bot – Backend
Genera docs/data/dashboard.json con el formato esperado por el dashboard PRO.
"""

import json
import os
from datetime import datetime, timezone


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
# Payload builder (placeholder)
# =========================

def build_dashboard_payload() -> dict:
    """
    Payload PRO compatible con el dashboard.
    Más adelante este método se reemplaza por:
    - scraping real
    - histórico
    - percentiles
    - bandas dinámicas
    """

    now = now_utc_iso()

    # Series en formato { t, v } (timestamp, valor)
    series_1d = [
        {"t": now, "v": 30.00}
    ]

    series_7d = [
        {"t": now, "v": 31.50}
    ]

    spread_series = [
        {"t": now, "v": 31.50 - 30.00}
    ]

    last_1d = series_1d[-1]["v"]
    last_7d = series_7d[-1]["v"]

    payload = {
        "generated_at": now,

        "meta": {
            "csv_path": "—",
            "note": "Datos placeholder. Reemplazar por scraping real."
        },

        "kpis": {
            "updated_at": now,
            "source": "dummy",
            "last_1d": last_1d,
            "last_7d": last_7d,
            "spread_7d_1d": last_7d - last_1d,
            "band_1d": "N/A",
            "n_1d_60d": len(series_1d)
        },

        "pctls": {
            # Se completa cuando haya histórico real
            "1D": {
                "p40": None,
                "p60": None,
                "p75": None,
                "n": 0
            }
        },

        "data": {
            "series": {
                "1D": series_1d,
                "7D": series_7d,
                "spread_7d_1d": spread_series
            }
        },

        "events": {
            "band_changes_1d": []
        }
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


if __name__ == "__main__":
    main()
