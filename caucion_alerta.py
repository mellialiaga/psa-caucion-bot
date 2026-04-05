#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PSA Caución Bot — Backend unificado
- Scraping BYMA con sesión correcta (cookies + headers)
- Parseo robusto de la respuesta (tolera int o string en plazo/term)
- Histórico append-only en docs/data/history.csv
- Genera docs/data/dashboard.json con series completas + percentiles
- Envía alertas por Telegram (si TG_BOT_TOKEN está seteado)
"""

import csv
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
import urllib3

urllib3.disable_warnings()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("caucion")


# ══════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════

BYMA_DASHBOARD_URL = "https://open.bymadata.com.ar/#/dashboard"
BYMA_CAUCIONES_URL = (
    "https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free/cauciones"
)
BYMA_HEADERS = {
    "Connection": "keep-alive",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/96.0.4664.110 Safari/537.36"
    ),
    "Origin": "https://open.bymadata.com.ar",
    "Referer": "https://open.bymadata.com.ar/",
    "Accept-Language": "es-US,es-419;q=0.9,es;q=0.8",
}

TIMEOUT = 20

DATA_DIR = os.path.join("docs", "data")
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")
DASHBOARD_JSON = os.path.join(DATA_DIR, "dashboard.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

# Percentiles / ventanas
PCTL_WINDOW_DAYS = int(os.getenv("PCTL_WINDOW_DAYS", "60"))
MIN_POINTS_FOR_PCTLS = int(os.getenv("MIN_POINTS_FOR_PCTLS", "20"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "180"))

# Telegram
TG_BOT_TOKEN  = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID    = os.getenv("TG_CHAT_ID", "")
USERS_JSON_RAW = os.getenv("USERS_JSON", "")

# Alertas
SPREAD_ALERT_MIN         = float(os.getenv("SPREAD_ALERT_MIN", "0.50"))
NOTIFY_ON_BAND_CHANGE    = os.getenv("NOTIFY_ON_BAND_CHANGE_ONLY", "1") == "1"
DEDUP_MINUTES            = int(os.getenv("DEDUP_MINUTES", "30"))


# ══════════════════════════════════════════════
# Utilidades
# ══════════════════════════════════════════════

def now_ar() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def write_json(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def load_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ══════════════════════════════════════════════
# Scraping BYMA
# ══════════════════════════════════════════════

def make_byma_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BYMA_HEADERS)
    try:
        s.get(BYMA_DASHBOARD_URL, timeout=TIMEOUT, verify=False)
        log.info("Sesión BYMA inicializada (cookies OK)")
    except Exception as e:
        log.warning("No se pudo inicializar sesión BYMA: %s", e)
    return s


def fetch_byma_cauciones(session: requests.Session) -> list:
    resp = session.post(
        BYMA_CAUCIONES_URL,
        data='{"Content-Type":"application/json"}',
        timeout=TIMEOUT,
        verify=False,
    )
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "cauciones", "result", "items"):
            if key in data and isinstance(data[key], list):
                log.info("Respuesta como dict, usando clave '%s'", key)
                return data[key]
        log.warning("Estructura inesperada de la API: keys=%s", list(data.keys()))
    return []


def normalize_term(row: dict) -> Optional[int]:
    for field in ("plazo", "term", "termDays", "days", "denominationTerm", "plazoDias"):
        val = row.get(field)
        if val is not None:
            try:
                return int(float(str(val).strip()))
            except (ValueError, TypeError):
                continue
    return None


def normalize_tna(row: dict) -> Optional[float]:
    for field in ("tna", "rate", "interestRate", "tasaNominalAnual", "annualRate", "lastPrice"):
        val = row.get(field)
        if val is not None:
            try:
                v = float(str(val).strip())
                if v <= 0:
                    continue
                # Convertir de fracción a porcentaje si viene como 0.35 en vez de 35.0
                if 0 < v < 2:
                    v = v * 100
                return round(v, 4)
            except (ValueError, TypeError):
                continue
    return None


def parse_rates(rows: list) -> dict:
    buckets: dict[int, list[float]] = {}
    for row in rows:
        plazo = normalize_term(row)
        tna = normalize_tna(row)
        if plazo is None or tna is None or tna <= 0:
            continue
        buckets.setdefault(plazo, []).append(tna)

    log.info("Plazos detectados en la API: %s", sorted(buckets.keys()))

    result: dict[int, float] = {}
    for plazo, tnas in buckets.items():
        result[plazo] = round(sum(tnas) / len(tnas), 4)

    return {"1D": result.get(1), "7D": result.get(7)}


# ══════════════════════════════════════════════
# Histórico CSV
# ══════════════════════════════════════════════

CSV_FIELDS = ["timestamp", "source", "term", "tna"]


def load_history() -> list[dict]:
    if not os.path.exists(HISTORY_CSV):
        return []
    rows = []
    with open(HISTORY_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                ts_str = r.get("timestamp") or r.get("timestamp_utc", "")
                ts = datetime.fromisoformat(ts_str)
                tna = float(r.get("tna", ""))
                term = (r.get("term") or "").strip()
                if term not in ("1D", "7D"):
                    continue
                rows.append({
                    "ts": ts,
                    "timestamp": ts.isoformat(),
                    "source": (r.get("source") or "BYMA").strip(),
                    "term": term,
                    "tna": tna,
                })
            except Exception:
                continue
    rows.sort(key=lambda x: x["ts"])
    return rows


def append_history(new_rows: list[dict]) -> int:
    ensure_dir(DATA_DIR)
    existing_keys: set[tuple] = set()
    file_exists = os.path.exists(HISTORY_CSV)
    if file_exists:
        with open(HISTORY_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ts = r.get("timestamp") or r.get("timestamp_utc", "")
                existing_keys.add((ts, r.get("term", "")))

    wrote = 0
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        for row in new_rows:
            key = (row["timestamp"], row["term"])
            if key in existing_keys:
                continue
            writer.writerow({k: row[k] for k in CSV_FIELDS})
            wrote += 1
    return wrote


# ══════════════════════════════════════════════
# Percentiles y bandas
# ══════════════════════════════════════════════

def quantile(values: list, q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] * (1 - (pos - lo)) + v[hi] * (pos - lo)


def compute_percentiles(values: list) -> dict:
    n = len(values)
    if n < MIN_POINTS_FOR_PCTLS:
        return {"n": n}
    return {
        "p40": round(quantile(values, 0.40), 4),
        "p60": round(quantile(values, 0.60), 4),
        "p75": round(quantile(values, 0.75), 4),
        "n": n,
    }


def classify_band(tna: Optional[float], pctls: dict) -> str:
    if tna is None:
        return "N/A"
    p40 = pctls.get("p40")
    p60 = pctls.get("p60")
    p75 = pctls.get("p75")
    if p40 is None or p60 is None or p75 is None:
        return "SIN_HISTORICO"
    if tna >= p75:
        return "EXCELENTE"
    if tna >= p60:
        return "BUENA"
    if tna >= p40:
        return "ACEPTABLE"
    return "BAJA"


# ══════════════════════════════════════════════
# Series y dashboard
# ══════════════════════════════════════════════

def build_series(rows: list, lookback_days: int) -> dict:
    if not rows:
        return {"series": {"1D": [], "7D": [], "spread_7d_1d": []}, "last": {}}

    cutoff = rows[-1]["ts"] - timedelta(days=lookback_days)
    filtered = [r for r in rows if r["ts"] >= cutoff]

    s1 = [{"t": r["timestamp"], "v": r["tna"]} for r in filtered if r["term"] == "1D"]
    s7 = [{"t": r["timestamp"], "v": r["tna"]} for r in filtered if r["term"] == "7D"]

    last1, last7 = None, None
    spread = []
    for r in filtered:
        if r["term"] == "1D":
            last1 = r["tna"]
        elif r["term"] == "7D":
            last7 = r["tna"]
        if last1 is not None and last7 is not None:
            spread.append({"t": r["timestamp"], "v": round(last7 - last1, 2)})

    def ds(arr, maxp=3000):
        if len(arr) <= maxp:
            return arr
        step = max(1, len(arr) // maxp)
        return arr[::step]

    last_1d = s1[-1]["v"] if s1 else None
    last_7d = s7[-1]["v"] if s7 else None

    return {
        "series": {"1D": ds(s1), "7D": ds(s7), "spread_7d_1d": ds(spread)},
        "last": {
            "1D": last_1d,
            "7D": last_7d,
            "spread_7d_1d": (
                round(last_7d - last_1d, 2)
                if last_1d is not None and last_7d is not None
                else None
            ),
            "updated_at": rows[-1]["timestamp"],
            "source": rows[-1]["source"],
        },
    }


def build_dashboard(tna_1d, tna_7d, rows: list, quality: str) -> dict:
    now = now_utc_iso()
    cutoff_pctls = datetime.now(timezone.utc) - timedelta(days=PCTL_WINDOW_DAYS)
    vals_1d = [r["tna"] for r in rows if r["term"] == "1D" and r["ts"] >= cutoff_pctls]
    pctls_1d = compute_percentiles(vals_1d)
    band_1d = classify_band(tna_1d, pctls_1d)
    data = build_series(rows, LOOKBACK_DAYS)

    return {
        "generated_at": now,
        "quality": quality,
        "meta": {
            "csv_path": HISTORY_CSV,
            "total_rows": len(rows),
            "pctl_window_days": PCTL_WINDOW_DAYS,
            "lookback_days": LOOKBACK_DAYS,
        },
        "kpis": {
            "updated_at": now,
            "source": "BYMA",
            "last_1d": tna_1d,
            "last_7d": tna_7d,
            "spread_7d_1d": (
                round(tna_7d - tna_1d, 2)
                if tna_1d is not None and tna_7d is not None
                else None
            ),
            "band_1d": band_1d,
            "n_1d_60d": pctls_1d.get("n", 0),
        },
        "pctls": {"1D": pctls_1d},
        "data": data,
        "events": {"band_changes_1d": []},
    }


# ══════════════════════════════════════════════
# Telegram
# ══════════════════════════════════════════════

def send_telegram(token: str, chat_id: str, text: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        if r.status_code != 200:
            log.warning("Telegram error %s: %s", r.status_code, r.text[:200])
        return r.status_code == 200
    except Exception as e:
        log.warning("Telegram exception: %s", e)
        return False


def get_users() -> list:
    if USERS_JSON_RAW:
        try:
            data = json.loads(USERS_JSON_RAW)
            if isinstance(data, list):
                return data
        except Exception as e:
            log.warning("USERS_JSON inválido: %s", e)
    if TG_CHAT_ID:
        return [{"name": "Usuario", "chat_id": TG_CHAT_ID, "capital": None}]
    return []


def format_alert(tna_1d, tna_7d, band, pctls, spread, capital) -> str:
    hora = now_ar().strftime("%H:%M")
    lines = [
        "<b>🧾 PSA Caución Bot</b>",
        f"🕐 {hora} hs AR",
        "",
    ]
    band_emoji = {"EXCELENTE": "🚀", "BUENA": "✅", "ACEPTABLE": "🟡", "BAJA": "🔴"}.get(band, "📌")
    if tna_1d is not None:
        lines.append(f"{band_emoji} <b>1D:</b> {tna_1d:.2f}% TNA  [{band}]")
    if tna_7d is not None:
        lines.append(f"📌 <b>7D:</b> {tna_7d:.2f}% TNA")
    if spread is not None:
        emoji = "🚀" if spread >= SPREAD_ALERT_MIN else "➡️"
        lines.append(f"{emoji} <b>Spread 7D-1D:</b> {spread:+.2f}%")

    p40 = pctls.get("p40")
    if p40 is not None:
        n = pctls.get("n", 0)
        lines += ["", f"📊 Percentiles 60d (n={n}):",
                  f"   p40={p40:.2f}%  p60={pctls['p60']:.2f}%  p75={pctls['p75']:.2f}%"]

    if capital and tna_1d is not None:
        renta = capital * tna_1d / 100 / 365
        lines += ["", f"💰 Renta diaria est.: <b>${renta:,.0f}</b>"]

    return "\n".join(lines)


def should_notify(band_1d: str, state: dict) -> tuple:
    prev_band = state.get("last_band", "")
    last_iso = state.get("last_notify_at", "")
    if last_iso:
        try:
            diff = datetime.now(timezone.utc) - datetime.fromisoformat(last_iso)
            if diff.total_seconds() < DEDUP_MINUTES * 60:
                return False, f"dedup ({int(diff.total_seconds()/60)}m < {DEDUP_MINUTES}m)"
        except Exception:
            pass
    if prev_band and band_1d != prev_band:
        return True, f"cambio de banda: {prev_band} → {band_1d}"
    if not NOTIFY_ON_BAND_CHANGE:
        return True, "modo siempre-notificar"
    return False, "sin cambio de banda"


# ══════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════

def main() -> None:
    ensure_dir(DATA_DIR)
    log.info("═══ PSA Caución Bot — inicio ═══")

    # 1. Scraping BYMA
    tna_1d: Optional[float] = None
    tna_7d: Optional[float] = None
    quality = "error"

    try:
        session = make_byma_session()
        raw_rows = fetch_byma_cauciones(session)
        session.close()
        log.info("Rows recibidos de BYMA: %d", len(raw_rows))

        if raw_rows:
            sample = raw_rows[0]
            log.info("Campos primer row: %s", list(sample.keys()))
            log.info("Valores primer row: %s", sample)

        rates = parse_rates(raw_rows)
        tna_1d = rates["1D"]
        tna_7d = rates["7D"]
        quality = "ok" if (tna_1d is not None or tna_7d is not None) else "no_data"

        if quality == "no_data":
            log.warning("No se encontraron tasas 1D/7D. Plazos presentes: %s",
                        sorted({normalize_term(r) for r in raw_rows if normalize_term(r)}))
    except Exception as e:
        log.error("Error scraping BYMA: %s", e)

    log.info("TNA 1D=%s  7D=%s  quality=%s", tna_1d, tna_7d, quality)

    # 2. Append histórico
    now_iso = now_utc_iso()
    new_rows = []
    if tna_1d is not None:
        new_rows.append({"timestamp": now_iso, "source": "BYMA", "term": "1D", "tna": tna_1d})
    if tna_7d is not None:
        new_rows.append({"timestamp": now_iso, "source": "BYMA", "term": "7D", "tna": tna_7d})

    appended = append_history(new_rows)
    log.info("Filas nuevas en histórico: %d", appended)

    # 3. Leer histórico completo
    all_rows = load_history()
    log.info("Total filas en histórico: %d", len(all_rows))

    # 4. Generar dashboard.json completo
    payload = build_dashboard(tna_1d, tna_7d, all_rows, quality)
    write_json(DASHBOARD_JSON, payload)
    log.info("Dashboard escrito → %s", DASHBOARD_JSON)

    # 5. Alertas Telegram
    if not TG_BOT_TOKEN:
        log.info("TG_BOT_TOKEN no seteado — omitiendo alertas.")
    elif quality == "ok":
        state = load_json(STATE_FILE, {})
        band_1d = payload["kpis"]["band_1d"]
        pctls_1d = payload["pctls"]["1D"]
        spread = payload["kpis"]["spread_7d_1d"]

        notify, motivo = should_notify(band_1d, state)
        log.info("Notificar: %s (%s)", notify, motivo)

        if notify:
            for user in get_users():
                chat_id = str(user.get("chat_id", ""))
                if not chat_id:
                    continue
                texto = format_alert(
                    tna_1d, tna_7d, band_1d, pctls_1d, spread, user.get("capital")
                )
                ok = send_telegram(TG_BOT_TOKEN, chat_id, texto)
                log.info("Telegram → %s: %s", user.get("name", chat_id), "OK" if ok else "FAIL")

            state.update({"last_band": band_1d, "last_notify_at": now_iso,
                          "last_1d": tna_1d, "last_7d": tna_7d})
            write_json(STATE_FILE, state)

    # 6. Resumen
    log.info("quality=%s | 1D=%s | 7D=%s | band=%s | rows=%d",
             quality, tna_1d, tna_7d, payload["kpis"]["band_1d"], len(all_rows))

    if quality == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
