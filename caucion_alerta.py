import os, re, json, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# =========================
# CONFIG
# =========================
BYMA_HTML_URL = "https://www.portfoliopersonal.com/Cotizaciones/Cauciones"

TOKEN = os.environ["TG_BOT_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

THRESH_HIGH = float(os.getenv("THRESH_HIGH", "40.0"))
THRESH_LOW  = float(os.getenv("THRESH_LOW", "35.0"))
DAILY_HOUR  = int(os.getenv("DAILY_HOUR", "10"))

CAPITAL_BASE = float(os.getenv("CAPITAL_BASE", "38901078.37"))
DAYS_IN_YEAR = 365

TZ = ZoneInfo("America/Argentina/Cordoba")
STATE_PATH = Path(".state/state.json")

# =========================
# HELPERS
# =========================
def send(msg: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20
    )
    r.raise_for_status()

def money(n: float) -> str:
    # Formato AR: $1.234.567
    s = f"{n:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${s}"

def fetch_rate():
    headers = {"User-Agent": "Mozilla/5.0"}
    html = requests.get(BYMA_HTML_URL, headers=headers, timeout=25).text

    # Busca: "1 DÍA" + "PESOS" y toma el primer %
    m = re.search(
        r"1\s*D[IÍ]A.*?PESOS.*?(\d{1,2}[.,]\d{1,2})\s*%",
        html,
        re.I | re.S
    )
    if not m:
        return None

    return float(m.group(1).replace(",", "."))

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"last_state": "", "last_daily": ""}

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")

# =========================
# MAIN
# =========================
def main():
    now = datetime.now(TZ)
    st = load_state()

    rate = fetch_rate()

    # Si no hay tasa, avisar una sola vez
    if rate is None:
        if st["last_state"] != "ERR":
            send("⚠️ No pude leer la tasa de Caución 1D (BYMA).")
            st["last_state"] = "ERR"
            save_state(st)
        return

    # Estimación diaria
    daily_income = CAPITAL_BASE * (rate / 100.0) / DAYS_IN_YEAR

    # Determinar estado
    state = "MID"
    if rate >= THRESH_HIGH:
        state = "HIGH"
    elif rate <= THRESH_LOW:
        state = "LOW"

    # Alertas solo si cambia el estado
    if state != st["last_state"]:
        if state == "HIGH":
            send(
                f"🟢 OPORTUNIDAD CAUCIÓN 1D\n"
                f"Tasa (TNA): {rate:.2f}%\n\n"
                f"💰 Estimado diario con {money(CAPITAL_BASE)}:\n"
                f"≈ {money(daily_income)} / día"
            )
        elif state == "LOW":
            send(
                f"⚠️ WARNING CAUCIÓN 1D\n"
                f"Tasa (TNA): {rate:.2f}%\n\n"
                f"💰 Estimado diario con {money(CAPITAL_BASE)}:\n"
                f"≈ {money(daily_income)} / día\n\n"
                f"👉 Evaluar alternativas"
            )
        st["last_state"] = state

    # Resumen diario (1 vez por día)
    today = now.strftime("%Y-%m-%d")
    if now.hour == DAILY_HOUR and st["last_daily"] != today:
        send(
            f"📊 Resumen diario Caución 1D\n"
            f"Tasa (TNA): {rate:.2f}%\n\n"
            f"💰 Estimado diario con {money(CAPITAL_BASE)}:\n"
            f"≈ {money(daily_income)} / día"
        )
        st["last_daily"] = today

    save_state(st)

if __name__ == "__main__":
    main()
