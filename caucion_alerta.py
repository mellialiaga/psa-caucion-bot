import os, re, json, csv, requests, sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from statistics import quantiles

# =========================
# Paths
# =========================
STATE_DIR = Path(".state")
STATE_PATH = STATE_DIR / "state.json"

DOCS_DIR = Path("docs")
DASHBOARD_PATH = DOCS_DIR / "index.html"

DATA_DIR = Path("data")
RATES_CSV = DATA_DIR / "rates.csv"

# =========================
# Helpers ENV
# =========================
def env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if not v or not v.strip() else v.strip()

def env_float(name: str, default: float) -> float:
    try:
        return float(env_str(name, str(default)))
    except Exception:
        return default

def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except Exception:
        return default

# =========================
# Config
# =========================
TZ = ZoneInfo("America/Argentina/Cordoba")

DEF_SOURCE_NAME = "Bull Market Brokers"
DEF_BYMA_HTML_URL = "https://www.bullmarketbrokers.com/Cotizaciones/cauciones"

@dataclass(frozen=True)
class Config:
    mode: str
    source_name: str
    byma_html_url: str

    # thresholds legacy (quedan por compatibilidad; ya no mandan la decisión)
    thresh_red: float
    thresh_green: float
    thresh_rocket: float

    daily_hour: int
    capital_base: float

    tg_token: str
    default_chat_id: str
    users_json_raw: str

    dedup_minutes: int
    http_timeout: int

    # percentiles/bandas
    pctl_window_days: int
    min_points_for_pctls: int
    notify_on_band_change_only: int  # 1/0 (bool)

CFG = Config(
    mode=env_str("MODE", "demo"),
    source_name=env_str("SOURCE_NAME", DEF_SOURCE_NAME),
    byma_html_url=env_str("BYMA_HTML_URL", DEF_BYMA_HTML_URL),

    thresh_red=env_float("THRESH_RED", 35.5),
    thresh_green=env_float("THRESH_GREEN", 38.0),
    thresh_rocket=env_float("THRESH_ROCKET", 40.0),

    daily_hour=env_int("DAILY_HOUR", 10),
    capital_base=env_float("CAPITAL_BASE", 38901078.37),

    tg_token=env_str("TG_BOT_TOKEN"),
    default_chat_id=env_str("TG_CHAT_ID"),
    users_json_raw=env_str("USERS_JSON"),

    dedup_minutes=env_int("DEDUP_MINUTES", 10),
    http_timeout=env_int("HTTP_TIMEOUT", 30),

    pctl_window_days=env_int("PCTL_WINDOW_DAYS", 60),
    min_points_for_pctls=env_int("MIN_POINTS_FOR_PCTLS", 20),
    notify_on_band_change_only=env_int("NOTIFY_ON_BAND_CHANGE_ONLY", 1),
)

# =========================
# Users
# =========================
def load_users() -> List[dict]:
    users: List[dict] = []

    if CFG.users_json_raw:
        try:
            parsed = json.loads(CFG.users_json_raw)
            for u in parsed:
                chat_id = str(u.get("chat_id", "")).strip()
                if chat_id:
                    users.append({
                        "name": u.get("name", "Usuario"),
                        "chat_id": chat_id,
                        "capital": float(u.get("capital", CFG.capital_base))
                    })
        except Exception as e:
            print("⚠️ USERS_JSON inválido:", e)

    if not users and CFG.default_chat_id:
        users.append({
            "name": "Usuario",
            "chat_id": CFG.default_chat_id,
            "capital": CFG.capital_base
        })

    return users

# =========================
# Telegram (NUNCA rompe)
# =========================
def send_safe(chat_id: str, msg: str) -> None:
    if not CFG.tg_token or not chat_id:
        print("⚠️ Telegram no configurado, mensaje omitido")
        return

    try:
        url = f"https://api.telegram.org/bot{CFG.tg_token}/sendMessage"
        r = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"⚠️ Error Telegram ({chat_id}):", e)

# =========================
# Utils
# =========================
def pct(v: float) -> str:
    return f"{v:.2f}%"

def now_tz() -> datetime:
    return datetime.now(TZ)

def safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None

# =========================
# Scraping
# =========================
def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=CFG.http_timeout)
    r.raise_for_status()
    return r.text

def parse_rates_from_html(html: str) -> Dict[str, Optional[float]]:
    """
    Extrae tasas 1D y 7D de la página HTML.
    Si algún plazo no aparece, devuelve None.
    """
    def find(days: str) -> Optional[float]:
        m = re.search(rf"{days}\s*D.*?(\d+[.,]\d+)\s*%", html, re.I | re.S)
        return float(m.group(1).replace(",", ".")) if m else None

    return {"1D": find("1"), "7D": find("7")}

def fetch_rates() -> Dict[str, Optional[float]]:
    html = fetch_html(CFG.byma_html_url)
    return parse_rates_from_html(html)

# =========================
# State
# =========================
def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(s: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")

# =========================
# CSV Historical storage (append-only + dedupe)
# =========================
CSV_HEADER = ["timestamp", "source", "term", "tna"]

def ensure_csv_exists() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not RATES_CSV.exists():
        with RATES_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(CSV_HEADER)

def read_last_row_for_term(term: str, max_lines: int = 400) -> Optional[dict]:
    """
    Lee hacia atrás escaneando últimas N líneas del CSV.
    Suficiente para repos chicos y evita cargar todo el archivo.
    """
    if not RATES_CSV.exists():
        return None

    try:
        lines = RATES_CSV.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines[-max_lines:]):
            if not line or line.startswith("timestamp,"):
                continue
            parts = list(csv.reader([line]))[0]
            if len(parts) != 4:
                continue
            ts, src, t, tna = parts
            if t == term:
                return {"timestamp": ts, "source": src, "term": t, "tna": float(tna)}
    except Exception as e:
        print("⚠️ No pude leer última fila del CSV:", e)

    return None

def should_append(term: str, tna: float, ts: datetime) -> Tuple[bool, str]:
    """
    Dedupe por: mismo term + misma tasa dentro de ventana DEDUP_MINUTES.
    """
    last = read_last_row_for_term(term)
    if not last:
        return True, "no_last"

    try:
        last_ts = datetime.fromisoformat(last["timestamp"])
    except Exception:
        return True, "bad_last_ts"

    if abs((ts - last_ts).total_seconds()) <= CFG.dedup_minutes * 60 and float(last["tna"]) == float(tna):
        return False, "dedup_same_rate_recent"

    return True, "rate_changed_or_old"

def append_rates_csv(ts: datetime, source: str, rates: Dict[str, Optional[float]]) -> dict:
    ensure_csv_exists()

    written = []
    skipped = []

    with RATES_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)

        for term, tna in rates.items():
            if tna is None:
                skipped.append((term, "none"))
                continue

            ok, reason = should_append(term, tna, ts)
            if not ok:
                skipped.append((term, reason))
                continue

            w.writerow([ts.isoformat(), source, term, f"{tna:.4f}"])
            written.append((term, tna))

    return {"written": written, "skipped": skipped}

# =========================
# Percentiles y Bandas
# =========================
def load_historical_rates(term: str, days: int) -> List[float]:
    """
    Devuelve lista de TNAs para un plazo dado, filtrado por ventana de días.
    """
    if not RATES_CSV.exists():
        return []

    cutoff = now_tz() - timedelta(days=days)
    values: List[float] = []

    try:
        with RATES_CSV.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("term") != term:
                    continue
                try:
                    ts = datetime.fromisoformat(row["timestamp"])
                    if ts < cutoff:
                        continue
                    tna = float(row["tna"])
                    values.append(tna)
                except Exception:
                    continue
    except Exception as e:
        print("⚠️ Error leyendo histórico:", e)
        return []

    return values

def compute_percentiles(values: List[float]) -> dict:
    """
    Calcula P40 / P60 / P75 usando quantiles.
    Requiere mínimo CFG.min_points_for_pctls puntos.
    """
    if len(values) < CFG.min_points_for_pctls:
        return {}

    # quantiles n=100 devuelve lista de 99 cortes (1..99)
    qs = quantiles(values, n=100, method="inclusive")
    return {"p40": qs[39], "p60": qs[59], "p75": qs[74], "n": len(values)}

def classify_band(tna: float, p: dict) -> str:
    if not p or any(k not in p for k in ("p40", "p60", "p75")):
        return "SIN_HISTORICO"
    if tna >= p["p75"]:
        return "EXCELENTE"
    if tna >= p["p60"]:
        return "BUENA"
    if tna >= p["p40"]:
        return "ACEPTABLE"
    return "BAJA"

# =========================
# Dashboard
# =========================
def render_dashboard(state: dict) -> None:
    DOCS_DIR.mkdir(exist_ok=True)

    p = state.get("percentiles_1d", {}) or {}
    band = state.get("band_1d", "—")
    n = p.get("n", "—")

    def fmt(v):
        try:
            return pct(float(v))
        except Exception:
            return "—"

    DASHBOARD_PATH.write_text(f"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>PSA Caución Bot — Dashboard</title>
</head>
<body style="background:#0b1220;color:#fff;font-family:system-ui,Segoe UI,Roboto,Arial;padding:24px;">
  <h1>PSA Caución Bot — Dashboard</h1>

  <h2>Últimas tasas</h2>
  <ul>
    <li><b>1D:</b> {state.get("last_rate_1d","—")}</li>
    <li><b>7D:</b> {state.get("last_rate_7d","—")}</li>
    <li><b>Updated:</b> {state.get("updated_at","—")}</li>
  </ul>

  <h2>Banda dinámica (1D)</h2>
  <ul>
    <li><b>Ventana:</b> {state.get("pctl_window_days","—")} días</li>
    <li><b>Puntos:</b> {n}</li>
    <li><b>P40:</b> {fmt(p.get("p40"))}</li>
    <li><b>P60:</b> {fmt(p.get("p60"))}</li>
    <li><b>P75:</b> {fmt(p.get("p75"))}</li>
    <li><b>Banda actual:</b> {band}</li>
  </ul>

  <p style="opacity:.75;">CSV: {state.get("csv_path","data/rates.csv")}</p>
</body>
</html>
""", encoding="utf-8")

# =========================
# Alerts
# =========================
def send_business_alerts(users: List[dict], r1: Optional[float], r7: Optional[float], state: dict) -> None:
    # Si no hay datos, avisar una vez (simple)
    if r1 is None and r7 is None:
        for u in users:
            send_safe(u["chat_id"], "⚠️ No pude leer tasas de caución.")
        return

    if r1 is None:
        return

    band = state.get("band_1d", "SIN_HISTORICO")
    p = state.get("percentiles_1d", {}) or {}

    prev_band = state.get("prev_band_1d")  # lo guardamos en state
    band_changed = (prev_band is not None and prev_band != band)

    # Política de spam: por defecto, solo si cambió de banda,
    # pero siempre notificar extremos (EXCELENTE/BAJA)
    notify = True
    if CFG.notify_on_band_change_only:
        notify = band_changed or band in ("EXCELENTE", "BAJA")

    if not notify:
        return

    # Mensajes
    if band == "SIN_HISTORICO":
        for u in users:
            send_safe(
                u["chat_id"],
                f"ℹ️ Caución 1D: {pct(r1)}\n"
                f"Aún no hay histórico suficiente para bandas (min {CFG.min_points_for_pctls} puntos)."
            )
        return

    p40 = safe_float(p.get("p40"))
    p60 = safe_float(p.get("p60"))
    p75 = safe_float(p.get("p75"))

    # Texto base
    base = (
        f"📌 Caución 1D: {pct(r1)}\n"
        f"Banda: {band}\n"
        f"Ventana: {CFG.pctl_window_days}D | n={p.get('n','—')}\n"
        f"P40={pct(p40) if p40 is not None else '—'} | "
        f"P60={pct(p60) if p60 is not None else '—'} | "
        f"P75={pct(p75) if p75 is not None else '—'}"
    )

    emoji = {
        "EXCELENTE": "🟢",
        "BUENA": "🟡",
        "ACEPTABLE": "🟠",
        "BAJA": "🔴",
    }.get(band, "ℹ️")

    for u in users:
        send_safe(u["chat_id"], f"{emoji} {base}")

# =========================
# MAIN
# =========================
def main() -> None:
    ts = now_tz()
    users = load_users()
    state = load_state()

    # 1) Fetch
    rates = fetch_rates()
    r1, r7 = rates.get("1D"), rates.get("7D")

    # 2) Persist histórico
    csv_result = append_rates_csv(ts, CFG.source_name, rates)

    # 3) Percentiles/Bandas (sobre 1D)
    hist_1d = load_historical_rates("1D", CFG.pctl_window_days)
    pctls_1d = compute_percentiles(hist_1d)
    band_1d = classify_band(r1, pctls_1d) if r1 is not None else "N/A"

    # Guardar banda previa para detectar cambios
    prev_band_1d = state.get("band_1d")

    # 4) Update state
    state.update({
        "last_rate_1d": r1,
        "last_rate_7d": r7,
        "updated_at": ts.isoformat(),

        "csv_path": str(RATES_CSV),
        "csv_written": csv_result.get("written", []),
        "csv_skipped": csv_result.get("skipped", []),

        "pctl_window_days": CFG.pctl_window_days,
        "percentiles_1d": pctls_1d,
        "band_1d": band_1d,
        "prev_band_1d": prev_band_1d,
    })
    save_state(state)

    # 5) Dashboard
    render_dashboard(state)

    # 6) Alertas
    send_business_alerts(users, r1, r7, state)

    print("✅ Bot ejecutado correctamente")
    if csv_result["written"]:
        print("🧾 CSV appended:", csv_result["written"])
    if csv_result["skipped"]:
        print("🧹 CSV skipped:", csv_result["skipped"])
    print("📊 Band 1D:", band_1d, "| Percentiles:", pctls_1d or "N/A")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Error crítico:", e)
        sys.exit(1)
