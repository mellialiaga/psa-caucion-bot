import os, re, json, requests, sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# =========================
# Config
# =========================
TZ = ZoneInfo("America/Argentina/Cordoba")
DAYS_IN_YEAR = 365

DEF_SOURCE_NAME = "Bull Market Brokers"
DEF_BYMA_HTML_URL = "https://www.bullmarketbrokers.com/Cotizaciones/cauciones"

STATE_DIR = Path(".state")
STATE_PATH = STATE_DIR / "state.json"

DOCS_DIR = Path("docs")
DASHBOARD_PATH = DOCS_DIR / "index.html"

# =========================
# Helpers ENV
# =========================
def env_str(name, default=""):
    v = os.getenv(name)
    return default if not v or not v.strip() else v.strip()

def env_float(name, default):
    try:
        return float(env_str(name, default))
    except Exception:
        return default

def env_int(name, default):
    try:
        return int(env_str(name, default))
    except Exception:
        return default

# =========================
# Runtime config
# =========================
MODE = env_str("MODE", "demo")
SOURCE_NAME = env_str("SOURCE_NAME", DEF_SOURCE_NAME)
BYMA_HTML_URL = env_str("BYMA_HTML_URL", DEF_BYMA_HTML_URL)

THRESH_RED    = env_float("THRESH_RED", 35.5)
THRESH_GREEN  = env_float("THRESH_GREEN", 38.0)
THRESH_ROCKET = env_float("THRESH_ROCKET", 40.0)

DAILY_HOUR = env_int("DAILY_HOUR", 10)
CAPITAL_BASE = env_float("CAPITAL_BASE", 38901078.37)

TOKEN = env_str("TG_BOT_TOKEN")
DEFAULT_CHAT_ID = env_str("TG_CHAT_ID")
USERS_JSON_RAW = env_str("USERS_JSON")

# =========================
# Users
# =========================
def load_users():
    users = []

    if USERS_JSON_RAW:
        try:
            parsed = json.loads(USERS_JSON_RAW)
            for u in parsed:
                chat_id = str(u.get("chat_id", "")).strip()
                if chat_id:
                    users.append({
                        "name": u.get("name", "Usuario"),
                        "chat_id": chat_id,
                        "capital": float(u.get("capital", CAPITAL_BASE))
                    })
        except Exception as e:
            print("⚠️ USERS_JSON inválido:", e)

    if not users and DEFAULT_CHAT_ID:
        users.append({
            "name": "Usuario",
            "chat_id": DEFAULT_CHAT_ID,
            "capital": CAPITAL_BASE
        })

    return users

# =========================
# Telegram (NUNCA rompe)
# =========================
def send_safe(chat_id, msg):
    if not TOKEN or not chat_id:
        print("⚠️ Telegram no configurado, mensaje omitido")
        return

    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id": chat_id,
            "text": msg
        }, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"⚠️ Error Telegram ({chat_id}):", e)

# =========================
# Utils
# =========================
def pct(v): return f"{v:.2f}%"
def money(v): return f"${v:,.0f}".replace(",", ".")

# =========================
# Scraping
# =========================
def fetch_rates():
    html = requests.get(BYMA_HTML_URL, timeout=30).text

    def find(d):
        m = re.search(rf"{d}\s*D.*?(\d+[.,]\d+)\s*%", html, re.I | re.S)
        return float(m.group(1).replace(",", ".")) if m else None

    return {
        "1D": find("1"),
        "7D": find("7")
    }

# =========================
# State / Dashboard
# =========================
def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}

def save_state(s):
    STATE_DIR.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(s, indent=2, ensure_ascii=False))

def render_dashboard(state):
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
</body>
</html>
""")

# =========================
# MAIN
# =========================
def main():
    now = datetime.now(TZ)
    users = load_users()
    state = load_state()

    rates = fetch_rates()
    r1, r7 = rates["1D"], rates["7D"]

    state.update({
        "last_rate_1d": r1,
        "last_rate_7d": r7,
        "updated_at": now.isoformat()
    })

    save_state(state)
    render_dashboard(state)

    if r1 is None and r7 is None:
        for u in users:
            send_safe(u["chat_id"], "⚠️ No pude leer tasas de caución.")
        return

    if r1 and r1 >= THRESH_ROCKET:
        for u in users:
            send_safe(u["chat_id"], f"🚀 Caución 1D ROCKET: {pct(r1)}")

    print("✅ Bot ejecutado correctamente")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Error crítico:", e)
        sys.exit(1)
