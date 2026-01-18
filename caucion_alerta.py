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

TZ = ZoneInfo("America/Argentina/Cordoba")
STATE_PATH = Path(".state/state.json")

# Capital para estimaciones (diarias)
CAPITAL_BASE = float(os.getenv("CAPITAL_BASE", "38901078.37"))
DAYS_IN_YEAR = int(os.getenv("DAYS_IN_YEAR", "365"))   # TNA/365
DAYS_MONTH = int(os.getenv("DAYS_MONTH", "30"))         # proyección simple mensual

# Plazos a monitorear (días)
TENORS = [int(x.strip()) for x in os.getenv("TENORS", "1,7,14,30").split(",")]

# Umbrales por plazo (TNA)
# Defaults razonables: ajustalos con env vars si querés.
THRESH_DEFAULTS = {
    1: float(os.getenv("THRESH_1D", "40.0")),
    7: float(os.getenv("THRESH_7D", "43.0")),
    14: float(os.getenv("THRESH_14D", "44.0")),
    30: float(os.getenv("THRESH_30D", "45.0")),
}

# Premio vs 1D para considerar oportunidad (puntos porcentuales)
# Ej: si 7D - 1D >= 3.0 -> oportunidad por premio
PREMIUM_SPREAD = float(os.getenv("PREMIUM_SPREAD", "3.0"))

# Alerta “super” global (si cualquier plazo supera este nivel)
SUPER_HIGH = float(os.getenv("SUPER_HIGH", "48.0"))

# Resumen diario
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "10"))

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
    """
    Lee tasas desde HTML (sin JS) buscando filas: 'X DÍA(S)' + 'PESOS' + '%'
    Devuelve dict {days: rate_float}
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    html = requests.get(BYMA_HTML_URL, headers=headers, timeout=25).text
    rates = {}

    for d in tenors:
        pattern = rf"{d}\s*D[IÍ]A(?:S)?\s*.*?PESOS.*?(\d{{1,2}}[.,]\d{{1,2}})\s*%"
        m = re.search(pattern, html, re.I | re.S)
        if m:
            rates[d] = float(m.group(1).replace(",", "."))
        else:
            rates[d] = None

    return rates

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {
        "last_daily": "",
        "last_rates": {},          # { "1": 39.5, "7": 42.1, ... }
        "crossed": {},             # { "1": false/true, "7": ... } para umbrales
        "premium_sent": {},        # { "7": false/true, "14": ... } premio vs 1D
        "super_sent": False,
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

def fmt_rate(r):
    return "—" if r is None else f"{r:.2f}%"

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

# =========================
# MAIN
# =========================
def main():
    now = datetime.now(TZ)
    st = load_state()

    rates = fetch_rates(TENORS)
    last_rates = st.get("last_rates", {})

    # Requisito mínimo: 1D debe existir para comparativas
    rate_1d = rates.get(1)
    if rate_1d is None:
        send("⚠️ No pude leer la tasa de Caución 1D (fuente BYMA HTML).")
        return

    # 1) SUPER ALERTA si cualquier plazo supera SUPER_HIGH
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
                f"💰 Estimado {d}D (base {money(CAPITAL_BASE)}):\n"
                f"≈ {money(gross_daily(CAPITAL_BASE, r))} / día (aprox)\n"
                f"📌 Señal: tasa excepcional"
            )
            st["super_sent"] = True

    # 2) Alertas por UMBRAL y CRUCE por cada plazo
    crossed = st.get("crossed", {})
    for d, r in rates.items():
        if r is None:
            continue
        thr = THRESH_DEFAULTS.get(d, THRESH_DEFAULTS.get(1, 40.0))
        is_above = (r >= thr)
        was_above = bool(crossed.get(str(d), False))

        # Solo avisar al CRUZAR (evita spam)
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

    # 3) Alertas por PREMIO vs 1D (lockear paga mucho más)
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
                f"💰 Estimado {d}D con {money(CAPITAL_BASE)}:\n"
                f"≈ {money(gross_daily(CAPITAL_BASE, r))} / día\n\n"
                f"👉 Oportunidad si podés inmovilizar {d} días"
            )
        premium_sent[str(d)] = is_premium

    st["premium_sent"] = premium_sent

    # 4) Curva “rara” / inversión: plazo largo < corto (señal)
    # Ej: 7D < 1D
    for d, r in rates.items():
        if d == 1 or r is None:
            continue
        if r < rate_1d:
            send(
                f"🟡 CURVA INVERTIDA\n\n"
                f"{d}D: {r:.2f}% < 1D: {rate_1d:.2f}%\n"
                f"👉 Señal de mercado: el corto paga más que el largo (ojo con lockear)"
            )
            break  # con una alcanza

    # 5) Resumen diario (tabla completa)
    today = now.strftime("%Y-%m-%d")
    if now.hour == DAILY_HOUR and st.get("last_daily") != today:
        table = build_table(rates, last_rates)
        send(
            "📊 Resumen diario – Curva de Cauciones (Pesos)\n\n"
            f"Capital base: {money(CAPITAL_BASE)}\n\n"
            f"{table}\n\n"
            f"Reglas: umbrales por plazo + premio ≥ {PREMIUM_SPREAD:.1f} pts vs 1D"
        )
        st["last_daily"] = today

    # Guardar tasas para tendencia
    st["last_rates"] = {str(k): v for k, v in rates.items() if v is not None}
    save_state(st)

if __name__ == "__main__":
    main()
