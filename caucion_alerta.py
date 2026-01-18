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
SUPER_HIGH  = float(os.getenv("SUPER_HIGH", "42.0"))

DAILY_HOUR  = int(os.getenv("DAILY_HOUR", "10"))

CAPITAL_BASE = float(os.getenv("CAPITAL_BASE", "38901078.37"))

# --- Fees (según tarifario BMB / BYMA) ---
# BMB: Caución Colocador = 0,083% mensual (prorrata por día)
BMB_FEE_MONTHLY = float(os.getenv("BMB_FEE_MONTHLY", "0.00083"))  # 0,083% => 0.00083

# BYMA: gastos administración garantías = 0,045% sobre monto (por operación)
BYMA_GARANTIA_FEE = float(os.getenv("BYMA_GARANTIA_FEE", "0.00045"))  # 0,045% => 0.00045

# IVA sobre comisiones/aranceles (caución no está exenta)
IVA = float(os.getenv("IVA", "0.21"))

# Para cálculos
DAYS_IN_YEAR = int(os.getenv("DAYS_IN_YEAR", "365"))
DAYS_IN_MONTH = int(os.getenv("DAYS_IN_MONTH", "30"))  # prorrateo simple comisión mensual

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

def gross_interest_daily(capital: float, tna: float) -> float:
    return capital * (tna / 100.0) / DAYS_IN_YEAR

def estimated_costs_daily(capital: float) -> float:
    """
    Estimación diaria para caución 1D:
    - Comisión BMB colocador 0,083% mensual prorrateada 1/30
    - BYMA garantía 0,045% por operación (1D -> diario)
    - IVA sobre ambos
    """
    bmb_daily = capital * (BMB_FEE_MONTHLY / DAYS_IN_MONTH)
    byma_daily = capital * BYMA_GARANTIA_FEE
    subtotal = bmb_daily + byma_daily
    return subtotal * (1.0 + IVA)

def msg_financial_block(rate: float, trend: str) -> str:
    gross = gross_interest_daily(CAPITAL_BASE, rate)
    costs = estimated_costs_daily(CAPITAL_BASE)
    net = gross - costs

    # Señal rápida
    if net > 0:
        sign = "✅ Neto positivo"
    else:
        sign = "🧊 Neto negativo (costos > interés)"

    return (
        f"Tasa (TNA): {rate:.2f}%  | Tendencia: {trend}\n\n"
        f"💰 Con {money(CAPITAL_BASE)} (estimación diaria):\n"
        f"• Bruto: {money(gross)}\n"
        f"• Costos: {money(costs)}\n"
        f"• Neto: {money(net)}\n"
        f"{sign}"
    )

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

    last_rate = st.get("last_rate")
    trend = trend_text(last_rate, rate)

    # Estado por umbrales
    state = "MID"
    if rate >= THRESH_HIGH:
        state = "HIGH"
    elif rate <= THRESH_LOW:
        state = "LOW"

    # Cruces
    crossed_high = rate >= THRESH_HIGH
    crossed_low  = rate <= THRESH_LOW

    # 🔥 Súper tasa
    if rate >= SUPER_HIGH and st.get("last_state") != "SUPER":
        send(
            "🔥 SÚPER TASA CAUCIÓN 1D\n\n"
            + msg_financial_block(rate, trend)
            + f"\n\nUmbrales: 🟢≥{THRESH_HIGH:.1f}% | ⚠️≤{THRESH_LOW:.1f}% | 🔥≥{SUPER_HIGH:.1f}%"
        )
        st["last_state"] = "SUPER"

    # 🟢 Cruce a oportunidad
    if crossed_high and not st.get("last_cross_high", False):
        send(
            f"🟢 CRUCE A OPORTUNIDAD (≥ {THRESH_HIGH:.1f}%)\n\n"
            + msg_financial_block(rate, trend)
        )
        st["last_cross_high"] = True
    if not crossed_high:
        st["last_cross_high"] = False

    # ⚠️ Cruce a warning
    if crossed_low and not st.get("last_cross_low", False):
        send(
            f"⚠️ CRUCE A WARNING (≤ {THRESH_LOW:.1f}%)\n\n"
            + msg_financial_block(rate, trend)
            + "\n\n👉 Evaluar alternativas"
        )
        st["last_cross_low"] = True
    if not crossed_low:
        st["last_cross_low"] = False

    # Alertas por cambio de estado (respaldo)
    if state != st.get("last_state") and st.get("last_state") not in ("SUPER",):
        if state == "HIGH":
            send("🟢 OPORTUNIDAD CAUCIÓN 1D\n\n" + msg_financial_block(rate, trend))
        elif state == "LOW":
            send(
                "⚠️ WARNING CAUCIÓN 1D\n\n"
                + msg_financial_block(rate, trend)
                + "\n\n👉 Evaluar alternativas"
            )
        st["last_state"] = state

    # 📊 Resumen diario
    today = now.strftime("%Y-%m-%d")
    if now.hour == DAILY_HOUR and st.get("last_daily") != today:
        send(
            "📊 Resumen diario Caución 1D\n\n"
            + msg_financial_block(rate, trend)
            + f"\n\nUmbrales: 🟢≥{THRESH_HIGH:.1f}% | ⚠️≤{THRESH_LOW:.1f}% | 🔥≥{SUPER_HIGH:.1f}%"
        )
        st["last_daily"] = today

    # Guardar última tasa
    st["last_rate"] = rate
    s
