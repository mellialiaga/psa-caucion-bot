from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import csv
import json
from typing import Dict, List, Optional, Tuple

# Entradas / salidas
RATES_CSV = Path("data") / "rates.csv"
OUT = Path("docs") / "data" / "dashboard.json"

# Ajustes
MAX_POINTS = 3000          # límite para no inflar el JSON
DEFAULT_LOOKBACK_DAYS = 180
TERMS = ("1D", "7D")

def parse_iso(ts: str) -> Optional[datetime]:
    try:
        # soporta "2026-01-21T10:30:00-03:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None

def read_rates_csv(path: Path) -> List[dict]:
    if not path.exists():
        return []
    rows: List[dict] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ts = parse_iso(r.get("timestamp", ""))
            if not ts:
                continue
            term = (r.get("term") or "").strip()
            if term not in TERMS:
                continue
            try:
                tna = float(r.get("tna", ""))
            except Exception:
                continue
            rows.append({
                "ts": ts,
                "timestamp": ts.isoformat(),
                "source": (r.get("source") or "").strip(),
                "term": term,
                "tna": tna,
            })
    rows.sort(key=lambda x: x["ts"])
    return rows

def quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    if len(v) == 1:
        return v[0]
    # linear interpolation (pandas-like)
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    frac = pos - lo
    return v[lo] * (1 - frac) + v[hi] * frac

def compute_percentiles(values: List[float]) -> dict:
    # p40/p60/p75 como tu bot
    if len(values) < 20:
        return {"n": len(values)}
    return {
        "p40": quantile(values, 0.40),
        "p60": quantile(values, 0.60),
        "p75": quantile(values, 0.75),
        "n": len(values),
    }

def classify_band(tna: Optional[float], p: dict) -> str:
    if tna is None:
        return "N/A"
    if not p or any(k not in p or p[k] is None for k in ("p40", "p60", "p75")):
        return "SIN_HISTORICO"
    if tna >= p["p75"]:
        return "EXCELENTE"
    if tna >= p["p60"]:
        return "BUENA"
    if tna >= p["p40"]:
        return "ACEPTABLE"
    return "BAJA"

def build_series(rows: List[dict], lookback_days: int) -> dict:
    if not rows:
        return {
            "series": {"1D": [], "7D": [], "spread_7d_1d": []},
            "sources": [],
            "last": {},
        }

    cutoff = rows[-1]["ts"] - timedelta(days=lookback_days)
    filtered = [r for r in rows if r["ts"] >= cutoff]

    # series por term
    s1 = [{"t": r["timestamp"], "v": r["tna"], "src": r["source"]} for r in filtered if r["term"] == "1D"]
    s7 = [{"t": r["timestamp"], "v": r["tna"], "src": r["source"]} for r in filtered if r["term"] == "7D"]

    # spread con join por timestamp exacto (mismo ts) o por “último disponible”
    # estrategia simple y robusta: caminata por tiempo, last-known 1D/7D
    last1 = None
    last7 = None
    spread = []
    for r in filtered:
        if r["term"] == "1D":
            last1 = r["tna"]
        elif r["term"] == "7D":
            last7 = r["tna"]
        if last1 is not None and last7 is not None:
            spread.append({"t": r["timestamp"], "v": (last7 - last1)})

    # last values
    last_1d = s1[-1]["v"] if s1 else None
    last_7d = s7[-1]["v"] if s7 else None
    last_spread = (last_7d - last_1d) if (last_1d is not None and last_7d is not None) else None

    sources = sorted({r["source"] for r in filtered if r.get("source")})

    # compact to max points
    def downsample(arr: List[dict], max_points: int) -> List[dict]:
        if len(arr) <= max_points:
            return arr
        step = max(1, len(arr) // max_points)
        return arr[::step]

    s1 = downsample(s1, MAX_POINTS)
    s7 = downsample(s7, MAX_POINTS)
    spread = downsample(spread, MAX_POINTS)

    return {
        "series": {"1D": s1, "7D": s7, "spread_7d_1d": spread},
        "sources": sources,
        "last": {
            "1D": last_1d,
            "7D": last_7d,
            "spread_7d_1d": last_spread,
            "updated_at": rows[-1]["timestamp"],
            "source": rows[-1]["source"],
        },
    }

def band_events(series_1d: List[dict], pctls: dict) -> List[dict]:
    # eventos de cambio de banda (1D)
    ev = []
    prev = None
    for p in series_1d:
        b = classify_band(p.get("v"), pctls)
        if prev is None:
            prev = b
            continue
        if b != prev:
            ev.append({"t": p["t"], "from": prev, "to": b, "v": p.get("v")})
            prev = b
    # limitar
    return ev[-200:]

def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)

    rows = read_rates_csv(RATES_CSV)

    # percentiles sobre ventana “reciente” (60 días por defecto tipo bot)
    # pero si el CSV es más viejo, igual toma lo disponible dentro del lookback
    now = datetime.now()
    cutoff_60 = now - timedelta(days=60)

    vals_1d_60 = [r["tna"] for r in rows if r["term"] == "1D" and r["ts"] >= cutoff_60]
    pctls_1d = compute_percentiles(vals_1d_60)

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "meta": {
            "csv_path": str(RATES_CSV),
            "lookback_default_days": DEFAULT_LOOKBACK_DAYS,
            "max_points": MAX_POINTS,
        },
        "pctls": {
            "1D": pctls_1d,
        },
        "data": build_series(rows, DEFAULT_LOOKBACK_DAYS),
    }

    # banda actual + eventos
    last_1d = payload["data"]["last"].get("1D")
    payload["kpis"] = {
        "band_1d": classify_band(last_1d, pctls_1d),
        "last_1d": last_1d,
        "last_7d": payload["data"]["last"].get("7D"),
        "spread_7d_1d": payload["data"]["last"].get("spread_7d_1d"),
        "updated_at": payload["data"]["last"].get("updated_at"),
        "source": payload["data"]["last"].get("source"),
        "n_1d_60d": pctls_1d.get("n", 0),
    }

    s1 = payload["data"]["series"]["1D"]
    payload["events"] = {
        "band_changes_1d": band_events(s1, pctls_1d),
    }

    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK → generado {OUT} (rows={len(rows)})")

if __name__ == "__main__":
    main()
