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
SUPER_HIGH  = float(os.getenv("SUPER_HIGH", "42.0"))   # alerta premium
DAILY_HOUR  = int(os.getenv("DAILY_HOUR", "10"))

CAPITAL_BASE = float(os.getenv("CAPITAL_BASE", "38901078.37"))
DAYS_YEAR = int(os.getenv("DAYS_YEAR", "365"))         # 365 o 252
DAYS_MONTH = int(os.getenv("DAYS_MONTH", "30"))        # proyección mensual simple

TZ = ZoneInfo("America/Argentina/Cordoba")
STATE_PATH = Path(".state/state.json")

# =========================
# HELPERS
# =========================
def send(msg: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=20)
    r.raise_for_status()

def money(n: float) -> str:
    s = f"{n:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${s}"

def fetch_rate():
    headers = {"User-Agent": "Mozilla/5.0"}
    html = requests.get(BYMA_HTML_URL, headers=headers, timeout=25).text
    m = re.search(r"1\s*D[IÍ]A.*?PESOS.*?(\d{1,2}[.,]\d{1,2})\s*%", html, re.I | re.S)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {
        "last_state": "",
        "last_daily": "",
        "last_rate": None,
        "last_cross_high": False,
        "last_cross_low": False,
    }

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")

def trend_text(last_rate, rate):
    if last_rate is None:
        return "—"
    if rate > last_rate:
        return f"⬆ sube (+{rate - last_rate:.2f})"
    if rate < last_rate:
        return f"⬇ baja (-{last_rate - rate:.2f})"
    return "➡ igual"

def income(capital, tna, days_in_year):
    return capital * (tna / 100.0) / days_in_year

# =========================
# MAIN
# =========================
def main():
    now = datetime.now(TZ)
    st = load_state()

    rate = fetch_rate()

    # Si no hay tasa, avisar una sola vez
    if rate is None:
        if st.get("last_state") != "ERR":
            send("⚠️ No pude leer la tasa de Caución 1D (BYMA).")
            st["last_state"] = "ERR"
            save_state(st)
        return

    # Tendencia simple
    last_rate = st.get("last_rate")
    trend = trend_text(last_rate, rate)

    # Ingresos estimados
    daily_365 = income(CAPITAL_BASE, rate, 365)
    daily_252 = income(CAPITAL_BASE, rate, 252)
    month_365 = daily_365 * DAYS_MONTH
    month_252 = daily_252 * DAYS_MONTH

    # Estado por umbrales
    state = "MID"
    if rate >= THRESH_HIGH:
        state = "HIGH"
    elif rate <= THRESH_LOW:
        state = "LOW"

    # Cruces (más inteligente que solo "estado cambió")
    crossed_high = rate >= THRESH_HIGH
    crossed_low  = rate <= THRESH_LOW

    # ALERTA SUPER (si aplica)
    if rate >= SUPER_HIGH and st.get("last_state") != "SUPER":
        send(
            f"🔥 SÚPER TASA CAUCIÓN 1D\n"
            f"Tasa (TNA): {rate:.2f}%  | Tendencia: {trend}\n\n"
            f"💰 Con {money(CAPITAL_BASE)}:\n"
            f"≈ {money(daily_365)}/día (365) | ≈ {money(daily_252)}/día (252)\n"
            f"≈ {money(month_365)}/mes (30d) | ≈ {money(month_252)}/mes (30d)\n\n"
            f"✅ Nivel MUY bueno para colocar"
        )
        st["last_state"] = "SUPER"

    # Cruce hacia arriba (pasa a >=40)
    if crossed_high and not st.get("last_cross_high", False):
        send(
            f"🟢 CRUCE A OPORTUNIDAD (≥ {THRESH_HIGH:.1f}%)\n"
            f"Tasa (TNA): {rate:.2f}%  | Tendencia: {trend}\n\n"
            f"💰 Con {money(CAPITAL_BASE)}:\n"
            f"≈ {money(daily_365)}/día (365) | ≈ {money(daily_252)}/día (252)\n"
            f"≈ {money(month_365)}/mes (30d) | ≈ {money(month_252)}/mes (30d)"
        )
        st["last_cross_high"] = True
    if not crossed_high:
        st["last_cross_high"] = False

    # Cruce hacia abajo (pasa a <=35)
    if crossed_low and not st.get("last_cross_low", False):
        send(
            f"⚠️ CRUCE A WARNING (≤ {THRESH_LOW:.1f}%)\n"
            f"Tasa (TNA): {rate:.2f}%  | Tendencia: {trend}\n\n"
            f"💰 Con {money(CAPITAL_BASE)}:\n"
            f"≈ {money(daily_365)}/día (365) | ≈ {money(daily_252)}/día (252)\n"
            f"≈ {money(month_365)}/mes (30d) | ≈ {money(month_252)}/mes (30d)\n\n"
            f"👉 Evaluar alternativas"
        )
        st["last_cross_low"] = True
    if not crossed_low:
        st["last_cross_low"] = False

    # Alertas por cambio de estado (respaldo)
    if state != st.get("last_state") and st.get("last_state") not in ("SUPER",):
        if state == "HIGH":
            send(
                f"🟢 OPORTUNIDAD CAUCIÓN 1D\n"
                f"Tasa (TNA): {rate:.2f}%  | Tendencia: {trend}\n\n"
                f"💰 Con {money(CAPITAL_BASE)}:\n"
                f"≈ {money(daily_365)}/día (365) | ≈ {money(daily_252)}/día (252)\n"
                f"≈ {money(month_365)}/mes (30d) | ≈ {money(month_252)}/mes (30d)"
            )
        elif state == "LOW":
            send(
                f"⚠️ WARNING CAUCIÓN 1D\n"
                f"Tasa (TNA): {rate:.2f}%  | Tendencia: {trend}\n\n"
                f"💰 Con {money(CAPITAL_BASE)}:\n"
                f"≈ {money(daily_365)}/día (365) | ≈ {money(daily_252)}/día (252)\n"
                f"≈ {money(month_365)}/mes (30d) | ≈ {money(month_252)}/mes (30d)\n\n"
                f"👉 Evaluar alternativas"
            )
        st["last_state"] = state

    # Resumen diario (1 vez por día)
    today = now.strftime("%Y-%m-%d")
    if now.hour == DAILY_HOUR and st.get("last_daily") != today:
        send(
            f"📊 Resumen diario Caución 1D\n"
            f"Tasa (TNA): {rate:.2f}%  | Tendencia: {trend}\n\n"
            f"💰 Con {money(CAPITAL_BASE)}:\n"
            f"≈ {money(daily_365)}/día (365) | ≈ {money(daily_252)}/día (252)\n"
            f"≈ {money(month_365)}/mes (30d) | ≈ {money(month_252)}/mes (30d)\n\n"
            f"Umbrales: 🟢≥{THRESH_HIGH:.1f}% | ⚠️≤{THRESH_LOW:.1f}% | 🔥≥{SUPER_HIGH:.1f}%"
        )
        st["last_daily"] = today

    # Guardar última tasa
    st["last_rate"] = rate

    save_state(st)

if __name__ == "__main__":
    main()
