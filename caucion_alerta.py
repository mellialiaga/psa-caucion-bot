#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
from datetime import datetime, timezone


def iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def build_placeholder_snapshot() -> dict:
    """
    Snapshot mínimo válido para el dashboard.
    Después lo reemplazás por datos reales (scraping BYMA/BMB).
    """
    now = iso_utc_now()
    # Un punto mínimo para 1D, así el dashboard puede renderizar algo.
    point_1d = {
        "date": now,          # el frontend lo parsea como fecha
        "tna": 30.0,          # placeholder
        "tea": None,          # opcional
        "source": "dummy",
        "quality": "placeholder",
    }

    data = {
        "updated_at": now,
        "source": "dummy",
        "quality": "placeholder",
        "series": {
            "1D": [point_1d],
            # Podés sumar 7D si querés:
            # "7D": [{"date": now, "tna": 32.0, "tea": None, "source": "dummy", "quality": "placeholder"}]
        },
        "meta": {
            "note": "Primer dato generado correctamente (placeholder). Reemplazar por scraping real.",
        },
    }
    return data


def write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    out_dir = os.path.join("docs", "data")
    ensure_dir(out_dir)

    dashboard_path = os.path.join(out_dir, "dashboard.json")
    latest_path = os.path.join(out_dir, "latest.json")

    data = build_placeholder_snapshot()

    # Escribimos ambos para compatibilidad (y para que puedas usar latest.json si querés).
    write_json(dashboard_path, data)
    write_json(latest_path, data)

    print(f"OK: wrote {dashboard_path} and {latest_path}")


if __name__ == "__main__":
    main()
