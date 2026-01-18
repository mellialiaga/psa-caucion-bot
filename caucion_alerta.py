import os, re, json, requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

# =========================
# CONFIG GENERAL
# =========================
BYMA_HTML_URL = "https://www.portfoliopersonal.com/Cotizaciones/Cauciones"

TOKEN = os.environ["TG_BOT_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

TZ = ZoneInfo("America/Argentina/Cordoba")
STATE_PATH = Path(".state/state.json")

# Capital base
CAPITAL_BASE = float(os.getenv("CAPITAL_BASE", "38901078.37"))

# TNA → diario
DAYS_IN_YEAR = 365
DAYS_MONTH = 30

# Plazos a monitorear
TENORS = [int(x.strip()) for x in os.getenv("TENORS", "1,7,14,30").split(",")]

# Umbrales por plazo
THRESHOLDS = {
    1: float(os.getenv("THRESH_1D", "40.0")),
    7: float(os.getenv("THRESH_7D", "43.0")),
    14: float(os.getenv("THRESH_14D", "44.0")),
    30: float(os.getenv("THRESH_30D", "45.0")),
}

# Premio vs 1D
PREMIUM_SPREAD = float(os.getenv("PREMIUM_SPREAD", "3.0"))

# Súper oportunidad
SUPER_HIGH = float(os.getenv("SUPER_HIGH", "48.0"))

# Resumen diario
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "10"))

# Sueldo: 4º día hábil
PAYDAY_N = int(os.getenv("PAYDAY_N", "4"))
PAYDAY_REMIND_DAYS = int(os.getenv("PAYDAY_REMIND_DAYS", "2"))
SALARY_TARGET = float(os.getenv("SALARY_TARGET", "0"))

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

def gross_daily(capital: float, tna: float) -> float:
    return capital * (tna / 100.0) / DAYS_IN_YEAR

def estimated_costs_daily(capital: float) -> float:
    """
    Costos reales caución 1D (calibrado con boleta BMB):
    - Arancel 0,0027%
    - Derecho BYMA 0,0005%
    - IVA 21%
    Total efectivo ≈ 0,00387%
    """
    BASE_FEE = 0.000032   # 0,0032%
    IVA = 0.21
    return capital * BASE_FEE * (1 + IVA)

def fetch_rates(tenors):
    headers = {"User-Agent": "Mozilla/5.0"}
    html = requests.get(BYMA_HTML_URL, headers=headers, timeout=25).text
    rates = {}
    for d in tenors:
        pattern = rf"{d}\s*D[IÍ]A(?:S)?\s*.*?PESOS.*?(\d{{1,2}}[.,]\d{{1,2}})\s*%"
        m = re.search(pattern, html, re.I | re.S)
        rates[d] = float(m.group(1).replace(",", ".")) if m else None
    return rates

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {
        "last_rates": {},
        "crossed": {},
        "premium_sent": {},
        "super_sent": False,
        "last_daily": "",
        "payday_notified": "",
        "payday_remind": ""
    }

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")

def trend(prev, curr):
    if prev is None or curr is None:
        return "—"
    if curr > prev:
        return f"⬆ (+{curr-prev:.2f})"
    if curr < prev:
        return f"⬇ (-{prev-curr:.2f})"
    return "➡ 0.00"

# =========================
# FECHAS HÁBILES
# =========================
def is_business_day(d: date) -> bool:
    return d.weekday() < 5

def nth_business_day(year, month, n):
    d = date(year, month, 1)
    count = 0
    while True:
        if is_business_day(d):
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)

def business_days_before(target: date, k: int):
    d = target
    moved = 0
    while moved < k:
        d -= timedelta(days=1)
        if is_business_day(d):
            moved += 1
    return d

# =========================
# PAYDAY LOGIC
# =========================
def payday_logic(now, rates, st):
    today = now.date()
    payday = nth_business_day(today.year, today.month, PAYDAY_N)
    remind = business_days_before(payday, PAYDAY_REMIND_DAYS)

    rate_1d = rates.get(1)
    if rate_1d is None:
        return

    gross = gross_daily(CAPITAL_BASE, rate_1d)
    costs = estimated_costs_daily(CAPITAL_BASE)
    net = gross - costs

    if today == remind and st["payday_remind"] != str(remind):
        msg = (
            f"🗓️ Recordatorio SUELDO\n\n"
            f"El cobro es el {PAYDAY_N}º día hábil: {payday}\n\n"
            f"1D hoy: {rate_1d:.2f}%\n"
            f"Bruto: {money(gross)} | Neto: {money(net)}\n\n"
            f"👉 Sugerencia: priorizar 1D para llegar líquido."
        )
        if SALARY_TARGET > 0:
            msg += f"\n🎯 Sueldo objetivo: {money(SALARY_TARGET)}"
        send(msg)
        st["payday_remind"] = str(remind)

    if today == payday and st["payday_notified"] != str(payday):
        send(
            f"💵 HOY ES DÍA DE SUELDO\n\n"
            f"Fecha: {payday}\n"
            f"1D: {rate_1d:.2f}%\n"
            f"Neto estimado hoy: {money(net)}\n\n"
            f"👉 Retirar sueldo y rearmar estrategia."
        )
        st["payday_notified"] = str(payday)

# =========================
# MAIN
# =========================
def main():
    now = datetime.now(TZ)
    st = load_state()

    rates = fetch_rates(TENORS)
    last_rates = st.get("last_rates", {})

    rate_1d = rates.get(1)
    if rate_1d is None:
        send("⚠️ No pude leer la tasa de Caución 1D.")
        return

    # Payday
    payday_logic(now, rates, st)

    # Súper oportunidad
    if not st["super_sent"]:
        best = max(
            [(d, r) for d, r in rates.items() if r is not None],
            key=lambda x: x[1],
            default=None
        )
        if best and best[1] >= SUPER_HIGH:
            d, r = best
            send(
                f"🔥 SÚPER OPORTUNIDAD\n\n"
                f"Mejor plazo: {d}D → {r:.2f}%\n"
                f"1D: {rate_1d:.2f}%"
            )
            st["super_sent"] = True

    # Cruces por umbral
    for d, r in rates.items():
        if r is None:
            continue
        thr = THRESHOLDS.get(d, THRESHOLDS[1])
        crossed_now = r >= thr
        crossed_before = st["cr]()_
