import os, re, json, requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

# =========================
# CONFIG
# =========================
BYMA_HTML_URL = "https://www.portfoliopersonal.com/Cotizaciones/Cauciones"

TOKEN = os.environ["TG_BOT_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

TZ = ZoneInfo("America/Argentina/Cordoba")
STATE_PATH = Path(".state/state.json")

CAPITAL_BASE = float(os.getenv("CAPITAL_BASE", "38901078.37"))
DAYS_IN_YEAR = int(os.getenv("DAYS_IN_YEAR", "365"))
DAYS_MONTH = int(os.getenv("DAYS_MONTH", "30"))

TENORS = [int(x.strip()) for x in os.getenv("TENORS", "1,7,14,30").split(",")]

THRESH_DEFAULTS = {
    1: float(os.getenv("THRESH_1D", "40.0")),
    7: float(os.getenv("THRESH_7D", "43.0")),
    14: float(os.getenv("THRESH_14D", "44.0")),
    30: float(os.getenv("THRESH_30D", "45.0")),
}

PREMIUM_SPREAD = float(os.getenv("PREMIUM_SPREAD", "3.0"))
SUPER_HIGH = float(os.getenv("SUPER_HIGH", "48.0"))

DAILY_HOUR = int(os.getenv("DAILY_HOUR", "10"))

# Payday: 4to día hábil (lun-vie) del mes
PAYDAY_N = int(os.getenv("PAYDAY_N", "4"))
PAYDAY_REMIND_DAYS = int(os.getenv("PAYDAY_REMIND_DAYS", "2"))  # recordatorio N hábiles antes
SALARY_TARGET = float(os.getenv("SALARY_TARGET", "0"))  # opcional: tu sueldo objetivo (ARS)

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
        "last_daily": "",
        "last_rates": {},
        "crossed": {},
        "premium_sent": {},
        "super_sent": False,
        "payday_notified": "",      # YYYY-MM-DD del payday ya notificado
        "payday_remind": "",        # YYYY-MM-DD del remind ya notificado
    }

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")

def trend(last_rate, rate):
    if last_rate is None or rate is None:
        return "—"
    if rate > last_rate:
        return f"⬆ (+{rate-last_rate:.2f})"
    if rate < last_rate:
        return f"⬇ (-{last_rate-rate:.2f})"
    return "➡ (0.00)"

def build_table(rates, last_rates):
    lines = ["Plazo | Tasa | Tendencia | $/día | $/mes(30d)", "---|---:|---:|---:|---:"]
    for d in sorted(rates.keys()):
        r = rates[d]
        lr = last_rates.get(str(d))
        tr = trend(lr, r)
        if r is None:
            lines.append(f"{d}D | — | — | — | —")
        else:
            dd = gross_daily(CAPITAL_BASE, r)
            mm = dd * DAYS_MONTH
            lines.append(f"{d}D | {r:.2f}% | {tr} | {money(dd)} | {money(mm)}")
    return "\n".join(lines)

def is_business_day(dt: date) -> bool:
    # Lunes(0) a Viernes(4). (No contempla feriados)
    return dt.weekday() < 5

def nth_business_day(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    count = 0
    while True:
        if is_business_day(d):
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)

def business_days_before(target: date, k: int) -> date:
    d = target
    moved = 0
    while moved < k:
        d -= timedelta(days=1)
        if is_business_day(d):
            moved += 1
    return d

def payday_messages(now_dt: datetime, rates: dict, st: dict):
    """Notifica reminder y payday (una sola vez)."""
    today = now_dt.date()
    payday = nth_business_day(today.year, today.month, PAYDAY_N)
    remind_day = business_days_before(payday, PAYDAY_REMIND_DAYS)

    rate_1d = rates.get(1)
    if rate_1d is None:
        return  # sin 1D no hacemos mensajes de sueldo

    # Estimación diaria con 1D (para tomar decisiones rápidas)
    daily_1d = gross_daily(CAPITAL_BASE, rate_1d)

    # Reminder (N hábiles antes)
    if today == remind_day and st.get("payday_remind") != str(remind_day):
        msg = (
            f"🗓️ Recordatorio SUELDO\n\n"
            f"El cobro es el {PAYDAY_N}º día hábil: {payday.isoformat()}.\n"
            f"Hoy faltan {PAYDAY_REMIND_DAYS} hábiles.\n\n"
            f"✅ Sugerencia: si vas a retirar, empezá a priorizar 1D para llegar líquido.\n"
            f"1D hoy: {rate_1d:.2f}% → ≈ {money(daily_1d)} / día (bruto, aprox)\n"
        )
        if SALARY_TARGET > 0:
            msg += f"\n🎯 Sueldo objetivo: {money(SALARY_TARGET)}\n👉 Guardá ese monto en 1D + un buffer."
        send(msg)
        st["payday_remind"] = str(remind_day)

    # Día de cobro
    if today == payday and st.get("payday_notified") != str(payday):
        msg = (
            f"💵 HOY ES DÍA DE SUELDO (4º hábil)\n\n"
            f"Fecha: {payday.isoformat()}\n"
            f"1D: {rate_1d:.2f}% → ≈ {money(daily_1d)} / día (bruto, aprox)\n\n"
            f"✅ Acción: retirar el sueldo y dejar el remanente trabajando (1D o escalonado)."
        )
        send(msg)
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
        send("⚠️ No pude leer la tasa de Caución 1D (fuente BYMA HTML).")
        return

    # --- Payday logic (4º hábil) ---
    payday_messages(now, rates, st)

    # 1) SUPER ALERTA (mejor plazo supera SUPER_HIGH)
    if not st.get("super_sent", False):
        best = None
        for d, r in rates.items():
            if r is not None and (best is None or r > best[1]):
                best = (d, r)
        if best and best[1] >= SUPER_HIGH:
            d, r = best
            send(
                f"🔥 SÚPER OPORTUNIDAD (curva)\n\n"
                f"Mejor plazo: {d}D → {r:.2f}%\n"
                f"1D: {rate_1d:.2f}%\n\n"
                f"💰 Estimado (base {money(CAPITAL_BASE)}):\n"
                f"≈ {money(gross_daily(CAPITAL_BASE, r))} / día (bruto, aprox)"
            )
            st["super_sent"] = True

    # 2) Alertas por cruce de umbral por plazo
    crossed = st.get("crossed", {})
    for d, r in rates.items():
        if r is None:
            continue
        thr = THRESH_DEFAULTS.get(d, THRESH_DEFAULTS.get(1, 40.0))
        is_above = (r >= thr)
        was_above = bool(crossed.get(str(d), False))

        if is_above and not was_above:
            send(
                f"🟢 OPORTUNIDAD {d}D (cruce ≥ {thr:.1f}%)\n\n"
                f"{d}D: {r:.2f}% {trend(last_rates.get(str(d)), r)}\n"
                f"1D: {rate_1d:.2f}%\n\n"
                f"💰 Estimado con {money(CAPITAL_BASE)}:\n"
                f"≈ {money(gross_daily(CAPITAL_BASE, r))} / día\n"
                f"≈ {money(gross_daily(CAPITAL_BASE, r)*DAYS_MONTH)} / mes(30d)\n\n"
                f"👉 Si no necesitás liquidez diaria, es una tasa para mirar"
            )
        crossed[str(d)] = is_above
    st["crossed"] = crossed

    # 3) Premio por lockear vs 1D
    premium_sent = st.get("premium_sent", {})
    for d, r in rates.items():
        if d == 1 or r is None:
            continue
        spread = r - rate_1d
        is_premium = spread >= PREMIUM_SPREAD
        was_premium = bool(premium_sent.get(str(d), False))

        if is_premium and not was_premium:
            send(
                f"🟣 PREMIO POR LOCKEAR {d}D\n\n"
                f"{d}D: {r:.2f}% | 1D: {rate_1d:.2f}%\n"
                f"Premio: +{spread:.2f} pts\n\n"
                f"💰 Estimado con {money(CAPITAL_BASE)}:\n"
                f"≈ {money(gross_daily(CAPITAL_BASE, r))} / día (bruto, aprox)\n\n"
                f"👉 Oportunidad si podés inmovilizar {d} días"
            )
        premium_sent[str(d)] = is_premium
    st["premium_sent"] = premium_sent

    # 4) Curva invertida
    for d, r in rates.items():
        if d == 1 or r is None:
            continue
        if r < rate_1d:
            send(
                f"🟡 CURVA INVERTIDA\n\n"
                f"{d}D: {r:.2f}% < 1D: {rate_1d:.2f}%\n"
                f"👉 Señal: ojo con lockear, el corto paga más."
            )
            break

    # 5) Resumen diario (tabla)
    today_str = now.strftime("%Y-%m-%d")
    if now.hour == DAILY_HOUR and st.get("last_daily") != today_str:
        table = build_table(rates, last_rates)
        payday = nth_business_day(now.year, now.month, PAYDAY_N)
        send(
            "📊 Resumen diario – Curva de Cauciones (Pesos)\n\n"
            f"Capital base: {money(CAPITAL_BASE)}\n"
            f"Payday (4º hábil): {payday.isoformat()}\n\n"
            f"{table}\n\n"
            f"Reglas: umbrales por plazo + premio ≥ {PREMIUM_SPREAD:.1f} pts vs 1D"
        )
        st["last_daily"] = today_str

    # Guardar tasas para tendencia
    st["last_rates"] = {str(k): v for k, v in rates.items() if v is not None}
    save_state(st)

if __name__ == "__main__":
    main()
