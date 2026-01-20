import os, re, json, csv, requests, sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional, Dict, List, Tuple

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

    # thresholds (publicadas, no netas)
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
    Si algún plazo no aparece, devuelve None para ese plazo.
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

def read_last_row_for_term(term: str, max_lines: int = 300) -> Optional[dict]:
    """
    Lee hacia atrás (simple) escaneando últimas N líneas.
    Para repo chico es suficiente y evita cargar todo el CSV.
    """
    if not RATES_CSV.exists():
        return None

    try:
        lines = RATES_CSV.read_text(encoding="utf-8").splitlines()
        # si hay pocas líneas, ok
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
# Dashboard
# =========================
def render_dashboard(state: dict) -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    DASHBOARD_PATH.write_text(f"""
<!doctype html>
<html>
<head><meta charset="utf-8"><title>PSA Caución Bot</title></head>
<body style="background:#0b1220;color:#fff;font-family:sans-serif">
<h1>PSA Caución Bot</h1>
<p>1D: {state.get("last_rate_1d","—")}</p>
<p>7D: {state.get("last_rate_7d","—")}</p>
<p>Updated: {state.get("updated_at","—")}</p>
<p>CSV: {state.get("csv_path","data/rates.csv")}</p>
</body>
</html>
""", encoding="utf-8")

# =========================
# Alerts (por ahora conservador: mantiene ROCKET)
# =========================
def send_business_alerts(users: List[dict], r1: Optional[float], r7: Optional[float]) -> None:
    if r1 is None and r7 is None:
        for u in users:
            send_safe(u["chat_id"], "⚠️ No pude leer tasas de caución.")
        return

    # Mantengo la lógica actual (rocket) y después ampliamos con bandas dinámicas
    if r1 is not None and r1 >= CFG.thresh_rocket:
        for u in users:
            send_safe(u["chat_id"], f"🚀 Caución 1D ROCKET: {pct(r1)}")

# =========================
# MAIN
# =========================
def main() -> None:
    ts = now_tz()
    users = load_users()
    state = load_state()

    rates = fetch_rates()
    r1, r7 = rates.get("1D"), rates.get("7D")

    # Persistir histórico primero (si hay algo)
    csv_result = append_rates_csv(ts, CFG.source_name, rates)

    # Actualizar state/dashboard
    state.update({
        "last_rate_1d": r1,
        "last_rate_7d": r7,
        "updated_at": ts.isoformat(),
        "csv_path": str(RATES_CSV),
        "csv_written": csv_result.get("written", []),
        "csv_skipped": csv_result.get("skipped", []),
    })
    save_state(state)
    render_dashboard(state)

    # Alertas
    send_business_alerts(users, r1, r7)

    print("✅ Bot ejecutado correctamente")
    if csv_result["written"]:
        print("🧾 CSV appended:", csv_result["written"])
    if csv_result["skipped"]:
        print("🧹 CSV skipped:", csv_result["skipped"])

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Error crítico:", e)
        sys.exit(1)
