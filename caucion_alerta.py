import os, re, json, requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

# =========================
# CONFIG
# =========================
BYMA_HTML_URL = os.getenv("BYMA_HTML_URL", "https://www.portfoliopersonal.com/Cotizaciones/Cauciones")
TZ = ZoneInfo(os.getenv("TZ", "America/Argentina/Cordoba"))

MODE = os.getenv("MODE", "PRO").upper()                 # DEMO | PRO
PROFILE = os.getenv("PROFILE", "BALANCED").upper()      # BALANCED (por ahora)

TENORS = [int(x.strip()) for x in os.getenv("TENORS", "1,7,14,30").split(",") if x.strip()]

# --- Reglas BALANCEADAS (default) ---
# Semáforo para 1D
RED_BELOW = float(os.getenv("RED_BELOW", "35.5"))       # < 35.5 => ROJO
YELLOW_BELOW = float(os.getenv("YELLOW_BELOW", "37.0")) # 35.5..36.9 => AMARILLO
GREEN_FROM = float(os.getenv("GREEN_FROM", "37.0"))     # >= 37 => elegible
TOP_FROM = float(os.getenv("TOP_FROM", "40.0"))         # >= 40 => fuerte

# Confirmación intradiaria (momentum)
INTRADAY_CONFIRM_PP = float(os.getenv("INTRADAY_CONFIRM_PP", "0.8"))  # +0.8pp en el día

# Contexto vs promedio 5 días (close del día a DAILY_HOUR)
AVG_DAYS = int(os.getenv("AVG_DAYS", "5"))
ABOVE_AVG_BONUS_PP = float(os.getenv("ABOVE_AVG_BONUS_PP", "0.3"))    # estar +0.3pp sobre el promedio

# Plazos >1D (oportunidad por umbral fijo, y premio vs 1D)
THRESHOLDS = {
    1: float(os.getenv("THRESH_1D", "38.0")),
    7: float(os.getenv("THRESH_7D", "41.0")),
    14: float(os.getenv("THRESH_14D", "42.0")),
    30: float(os.getenv("THRESH_30D", "43.0")),
}
PREMIUM_SPREAD = float(os.getenv("PREMIUM_SPREAD", "3.0"))  # premio vs 1D
SUPER_HIGH = float(os.getenv("SUPER_HIGH", "45.0"))         # súper oportunidad (cualquier plazo)

# Resumen diario
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "10"))

# Sueldo (N-ésimo hábil del mes)
DEFAULT_PAYDAY_N = int(os.getenv("PAYDAY_N", "4"))
DEFAULT_PAYDAY_REMIND_DAYS = int(os.getenv("PAYDAY_REMIND_DAYS", "2"))

# Capital
DEFAULT_CAPITAL_BASE = float(os.getenv("CAPITAL_BASE", "38901078.37"))
DAYS_IN_YEAR = 365
DAYS_MONTH = 30

# Costos reales (calibrados con boleta)
# Tu boleta real dio aprox 0,0039% diario de costo efectivo (arancel+der+IVA)
COST_DAILY_RATE = float(os.getenv("COST_DAILY_RATE", "0.000039"))  # 0.0039% diario

# =========================
# PATHS (persistencia en repo)
# =========================
STATE_DIR = Path(".state")
DOCS_DIR = Path("docs")
DASH_DIR = DOCS_DIR / "data"
DASH_JSON_PATH = DASH_DIR / "latest.json"

MARKET_HISTORY_PATH = STATE_DIR / "market_history.json"  # histórico para promedio 5 días (close diario)

# =========================
# TELEGRAM
# =========================
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()  # fallback single-user

def send_message(chat_id: str, msg: str):
    if not TG_BOT_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=20)
    r.raise_for_status()

# =========================
# UTIL
# =========================
def ensure_dirs():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DASH_DIR.mkdir(parents=True, exist_ok=True)

def safe_load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def safe_write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def money(n: float) -> str:
    s = f"{n:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${s}"

def gross_daily(capital: float, tna: float) -> float:
    return capital * (tna / 100.0) / DAYS_IN_YEAR

def net_daily_1d(capital: float, tna_1d: float) -> Tuple[float, float, float]:
    gross = gross_daily(capital, tna_1d)
    costs = capital * COST_DAILY_RATE
    net = gross - costs
    return gross, costs, net

def watermark(mode: str) -> str:
    return "\n\n🧪 Modo DEMO" if mode == "DEMO" else ""

def is_business_day(d: date) -> bool:
    return d.weekday() < 5

def nth_business_day(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    c = 0
    while True:
        if is_business_day(d):
            c += 1
            if c == n:
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

def trend(prev: Optional[float], curr: Optional[float]) -> str:
    if prev is None or curr is None:
        return "—"
    if curr > prev:
        return f"⬆ (+{curr-prev:.2f})"
    if curr < prev:
        return f"⬇ (-{prev-curr:.2f})"
    return "➡ 0.00"

# =========================
# FETCH RATES
# =========================
def fetch_rates(tenors: List[int]) -> Dict[int, Optional[float]]:
    headers = {"User-Agent": "Mozilla/5.0"}
    html = requests.get(BYMA_HTML_URL, headers=headers, timeout=25).text

    out: Dict[int, Optional[float]] = {}
    for d in tenors:
        # busca "1 DIA(S) ... PESOS ... xx,xx %"
        pat = rf"{d}\s*D[IÍ]A(?:S)?\s*.*?PESOS.*?(\d{{1,2}}[.,]\d{{1,2}})\s*%"
        m = re.search(pat, html, re.I | re.S)
        out[d] = float(m.group(1).replace(",", ".")) if m else None
    return out

# =========================
# MULTI USER
# =========================
def load_users() -> List[Dict[str, Any]]:
    raw = os.getenv("USERS_JSON", "").strip()
    if raw:
        try:
            users = json.loads(raw)
            if isinstance(users, list):
                return [u for u in users if isinstance(u, dict) and str(u.get("chat_id", "")).strip()]
        except Exception:
            pass

    if TG_CHAT_ID and TG_BOT_TOKEN:
        return [{
            "name": "default",
            "chat_id": TG_CHAT_ID,
            "capital": DEFAULT_CAPITAL_BASE,
            "mode": MODE,
            "profile": PROFILE,
            "payday_n": DEFAULT_PAYDAY_N,
            "payday_remind_days": DEFAULT_PAYDAY_REMIND_DAYS,
        }]
    return []

def state_path_for(chat_id: str) -> Path:
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", chat_id)
    return STATE_DIR / f"state_{safe}.json"

def load_state(chat_id: str) -> Dict[str, Any]:
    return safe_load_json(state_path_for(chat_id), {
        "last_rates": {},
        "last_daily": "",
        "crossed": {},
        "premium_sent": {},
        "super_sent": False,
        "payday_notified": "",
        "payday_remind": "",
        "intraday": {}  # { "YYYY-MM-DD": {"open": x, "last": y} }
    })

def save_state(chat_id: str, st: Dict[str, Any]):
    safe_write_json(state_path_for(chat_id), st)

# =========================
# MARKET HISTORY (promedio 5 días)
# =========================
def load_market_history() -> Dict[str, Any]:
    """
    Estructura:
    {
      "tenor_1": [{"date":"2026-01-18","rate":37.5}, ...],
      "tenor_7": [...]
    }
    """
    base = {}
    for d in TENORS:
        base[f"tenor_{d}"] = []
    return safe_load_json(MARKET_HISTORY_PATH, base)

def save_market_history(hist: Dict[str, Any]):
    safe_write_json(MARKET_HISTORY_PATH, hist)

def append_daily_close(hist: Dict[str, Any], day: str, rates: Dict[int, Optional[float]]):
    for d in TENORS:
        key = f"tenor_{d}"
        r = rates.get(d)
        if r is None:
            continue
        arr = hist.get(key, [])
        # evitar duplicar el mismo día
        if any(x.get("date") == day for x in arr):
            continue
        arr.append({"date": day, "rate": float(r)})
        # mantener sólo últimos 30
        arr = arr[-30:]
        hist[key] = arr

def avg_last_n(hist: Dict[str, Any], tenor: int, n: int) -> Optional[float]:
    arr = hist.get(f"tenor_{tenor}", [])
    if not arr:
        return None
    last = arr[-n:]
    if not last:
        return None
    return sum(x["rate"] for x in last) / len(last)

# =========================
# BALANCED DECISION (1D)
# =========================
def classify_1d_zone(rate_1d: float) -> str:
    if rate_1d < RED_BELOW:
        return "RED"
    if rate_1d < YELLOW_BELOW:
        return "YELLOW"
    if rate_1d >= TOP_FROM:
        return "TOP"
    return "GREEN"

def balanced_signal_1d(rate_1d: float, avg5_1d: Optional[float], intraday_delta: float) -> Tuple[bool, str]:
    """
    Devuelve (should_alert, reason)
    Balanceado:
    - Debe estar en GREEN o TOP por nivel de tasa.
    - Y además:
        (a) estar >= avg5 + bonus, o
        (b) tener confirmación intradía (delta >= 0.8pp)
    TOP siempre alerta (fuerte).
    """
    zone = classify_1d_zone(rate_1d)

    if zone == "RED":
        return (False, f"Zona baja (<{RED_BELOW:.1f}%)")
    if zone == "YELLOW":
        return (False, f"Zona media ({RED_BELOW:.1f}-{YELLOW_BELOW-0.1:.1f}%)")

    # GREEN o TOP
    if zone == "TOP":
        return (True, "Tasa excepcional (TOP)")

    ok_context = False
    if avg5_1d is None:
        # sin historia: permitir por tasa sola pero más conservador
        ok_context = rate_1d >= THRESHOLDS.get(1, 38.0)
    else:
        ok_context = rate_1d >= (avg5_1d + ABOVE_AVG_BONUS_PP)

    ok_intraday = intraday_delta >= INTRADAY_CONFIRM_PP

    if ok_context or ok_intraday:
        why = []
        if ok_context:
            why.append("por encima del promedio 5d")
        if ok_intraday:
            why.append(f"subiendo intradía (+{intraday_delta:.2f}pp)")
        return (True, " + ".join(why))

    return (False, "Sin confirmación (ni contexto ni momentum)")

# =========================
# MESSAGES
# =========================
def msg_status_today(rate_1d: float, zone: str, avg5: Optional[float], intraday_delta: float, capital: float, mode: str) -> str:
    gross, costs, net = net_daily_1d(capital, rate_1d)
    avg_txt = "—" if avg5 is None else f"{avg5:.2f}%"
    return (
        f"📌 Estado del día (BALANCEADO)\n\n"
        f"1D: {rate_1d:.2f}% | Zona: {zone}\n"
        f"Promedio 5d: {avg_txt}\n"
        f"Momentum intradía: {intraday_delta:+.2f} pp\n\n"
        f"Estimado 1D (con {money(capital)}):\n"
        f"• Bruto: {money(gross)}\n"
        f"• Costos aprox: {money(costs)}\n"
        f"• Neto: {money(net)}"
        + watermark(mode)
    )

def msg_opportunity_1d(rate_1d: float, reason: str, avg5: Optional[float], intraday_delta: float, capital: float, mode: str) -> str:
    gross, costs, net = net_daily_1d(capital, rate_1d)
    avg_txt = "—" if avg5 is None else f"{avg5:.2f}%"
    tag = "🚀" if rate_1d >= TOP_FROM else "🟢"
    title = "OPORTUNIDAD FUERTE" if rate_1d >= TOP_FROM else "Caución 1D habilitada"
    return (
        f"{tag} {title}\n\n"
        f"Tasa 1D: {rate_1d:.2f}%\n"
        f"Promedio 5d: {avg_txt}\n"
        f"Momentum intradía: {intraday_delta:+.2f} pp\n"
        f"Motivo: {reason}\n\n"
        f"Estimado 1D (con {money(capital)}):\n"
        f"• Neto: {money(net)}"
        + watermark(mode)
    )

def msg_threshold_other(d: int, r: float, r1: float, capital: float, mode: str) -> str:
    return (
        f"🟢 OPORTUNIDAD {d}D\n\n"
        f"{d}D: {r:.2f}% | 1D: {r1:.2f}%\n"
        f"≈ {money(gross_daily(capital, r))}/día | ≈ {money(gross_daily(capital, r)*DAYS_MONTH)}/mes(30d)"
        + watermark(mode)
    )

def msg_premium(d: int, r: float, r1: float, spread: float, capital: float, mode: str) -> str:
    return (
        f"🟣 PREMIO POR LOCKEAR {d}D\n\n"
        f"{d}D: {r:.2f}% | 1D: {r1:.2f}%\n"
        f"Premio: +{spread:.2f} pts\n\n"
        f"≈ {money(gross_daily(capital, r))}/día (bruto aprox)\n"
        f"👉 Oportunidad si podés inmovilizar {d} días"
        + watermark(mode)
    )

def msg_super(best_d: int, best_r: float, r1: float, capital: float, mode: str) -> str:
    return (
        f"🔥 SÚPER OPORTUNIDAD\n\n"
        f"Mejor plazo: {best_d}D → {best_r:.2f}%\n"
        f"1D: {r1:.2f}%\n\n"
        f"≈ {money(gross_daily(capital, best_r))}/día (bruto aprox)"
        + watermark(mode)
    )

# =========================
# PAYDAY
# =========================
def payday_logic(now: datetime, user: Dict[str, Any], rate_1d: float, st: Dict[str, Any]):
    chat_id = str(user["chat_id"])
    mode = str(user.get("mode", MODE)).upper()
    capital = float(user.get("capital", DEFAULT_CAPITAL_BASE))
    payday_n = int(user.get("payday_n", DEFAULT_PAYDAY_N))
    remind_days = int(user.get("payday_remind_days", DEFAULT_PAYDAY_REMIND_DAYS))

    today = now.date()
    payday = nth_business_day(today.year, today.month, payday_n)
    remind = business_days_before(payday, remind_days)

    _, _, net = net_daily_1d(capital, rate_1d)

    if today == remind and st.get("payday_remind") != str(remind):
        send_message(chat_id,
            f"🗓️ Recordatorio SUELDO\n\n"
            f"Cobro: {payday_n}º hábil → {payday.isoformat()}\n"
            f"Hoy faltan {remind_days} hábiles.\n\n"
            f"1D: {rate_1d:.2f}% | Neto estimado 1D: {money(net)}\n"
            f"👉 Estrategia: priorizar 1D para llegar líquido."
            + watermark(mode)
        )
        st["payday_remind"] = str(remind)

    if today == payday and st.get("payday_notified") != str(payday):
        send_message(chat_id,
            f"💵 HOY ES DÍA DE SUELDO\n\n"
            f"Fecha: {payday.isoformat()}\n"
            f"1D: {rate_1d:.2f}% | Neto estimado 1D: {money(net)}\n\n"
            f"👉 Retirar sueldo y rearmar estrategia."
            + watermark(mode)
        )
        st["payday_notified"] = str(payday)

# =========================
# DASHBOARD PAYLOAD
# =========================
def build_dashboard_payload(now: datetime, rates: Dict[int, Optional[float]], users_count: int) -> Dict[str, Any]:
    best = None
    for d, r in rates.items():
        if r is not None and (best is None or r > best[1]):
            best = (d, r)

    return {
        "timestamp": now.isoformat(),
        "source": BYMA_HTML_URL,
        "rates": {str(k): v for k, v in rates.items()},
        "best": {"tenor_days": best[0], "rate": best[1]} if best else None,
        "tenors": TENORS,
        "mode": MODE,
        "profile": PROFILE,
        "users_count": users_count,
    }

# =========================
# MAIN
# =========================
def main():
    ensure_dirs()
    now = datetime.now(TZ)
    today_str = now.strftime("%Y-%m-%d")

    # 1) Fetch
    try:
        rates = fetch_rates(TENORS)
    except Exception:
        rates = {d: None for d in TENORS}

    users = load_users()

    # 2) Dashboard JSON (siempre)
    safe_write_json(DASH_JSON_PATH, build_dashboard_payload(now, rates, len(users)))

    if not users:
        return

    # 3) Cargar histórico mercado (promedios)
    hist = load_market_history()

    # 4) Por usuario
    for user in users:
        chat_id = str(user.get("chat_id", "")).strip()
        if not chat_id:
            continue

        mode = str(user.get("mode", MODE)).upper()
        capital = float(user.get("capital", DEFAULT_CAPITAL_BASE))
        profile = str(user.get("profile", PROFILE)).upper()

        st = load_state(chat_id)
        last_rates = st.get("last_rates", {})

        r1 = rates.get(1)
        if r1 is None:
            if st.get("last_error") != "NO_1D":
                send_message(chat_id, "⚠️ No pude leer la tasa 1D (fuente BYMA HTML)." + watermark(mode))
                st["last_error"] = "NO_1D"
                save_state(chat_id, st)
            continue
        st["last_error"] = ""

        # ---- Intradía (open y last) ----
        intraday = st.get("intraday", {})
        day_info = intraday.get(today_str, {})
        if "open" not in day_info:
            day_info["open"] = float(r1)
        day_info["last"] = float(r1)
        intraday[today_str] = day_info
        st["intraday"] = intraday

        intraday_delta = float(day_info.get("last", r1)) - float(day_info.get("open", r1))

        # ---- Promedio 5 días (close diario) ----
        avg5_1d = avg_last_n(hist, 1, AVG_DAYS)

        # ---- Payday ----
        payday_logic(now, user, float(r1), st)

        # ---- Súper oportunidad (una vez por usuario) ----
        if not st.get("super_sent", False):
            best = None
            for d, r in rates.items():
                if r is not None and (best is None or r > best[1]):
                    best = (d, r)
            if best and best[1] >= SUPER_HIGH:
                send_message(chat_id, msg_super(best[0], best[1], float(r1), capital, mode))
                st["super_sent"] = True

        # =========================
        # PROFILE: BALANCED
        # =========================
        if profile == "BALANCED":
            zone = classify_1d_zone(float(r1))

            should_alert, reason = balanced_signal_1d(float(r1), avg5_1d, intraday_delta)

            # anti-spam: alertar solo cuando cambia a "aprobado"
            crossed = st.get("crossed", {})
            was_ok = bool(crossed.get("1d_ok", False))
            now_ok = bool(should_alert)

            if now_ok and not was_ok:
                send_message(chat_id, msg_opportunity_1d(float(r1), reason, avg5_1d, intraday_delta, capital, mode))

            crossed["1d_ok"] = now_ok
            st["crossed"] = crossed

            # (opcional, pro): un status diario a la hora DAILY_HOUR
            if now.hour == DAILY_HOUR and st.get("last_daily") != today_str:
                send_message(chat_id, msg_status_today(float(r1), zone, avg5_1d, intraday_delta, capital, mode))
                st["last_daily"] = today_str

        # =========================
        # Otros plazos: umbral + premio vs 1D
        # =========================
        if mode != "DEMO":
            # Umbral por tenor
            crossed = st.get("crossed", {})
            for d in TENORS:
                if d == 1:
                    continue
                r = rates.get(d)
                if r is None:
                    continue
                thr = float(THRESHOLDS.get(d, 0))
                is_above = r >= thr
                was_above = bool(crossed.get(f"{d}d", False))
                if is_above and not was_above:
                    send_message(chat_id, msg_threshold_other(d, float(r), float(r1), capital, mode))
                crossed[f"{d}d"] = is_above
            st["crossed"] = crossed

            # Premio por lockear
            premium_sent = st.get("premium_sent", {})
            for d in TENORS:
                if d == 1:
                    continue
                r = rates.get(d)
                if r is None:
                    continue
                spread = float(r) - float(r1)
                is_premium = spread >= PREMIUM_SPREAD
                was_premium = bool(premium_sent.get(f"{d}d", False))
                if is_premium and not was_premium:
                    send_message(chat_id, msg_premium(d, float(r), float(r1), spread, capital, mode))
                premium_sent[f"{d}d"] = is_premium
            st["premium_sent"] = premium_sent

        # Guardar last rates para tendencias
        st["last_rates"] = {str(k): v for k, v in rates.items() if v is not None}

        save_state(chat_id, st)

    # 5) Guardar close diario (histórico) SOLO a DAILY_HOUR (una vez/día)
    # Esto habilita promedio 5d real.
    if now.hour == DAILY_HOUR:
        append_daily_close(hist, today_str, rates)
        save_market_history(hist)


if __name__ == "__main__":
    main()
