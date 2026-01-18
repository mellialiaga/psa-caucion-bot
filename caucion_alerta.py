import os, re, json, requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, Optional, List

# =========================
# HARDENED CONFIG
# =========================
BYMA_HTML_URL = os.getenv("BYMA_HTML_URL", "https://www.portfoliopersonal.com/Cotizaciones/Cauciones")

TZ = ZoneInfo(os.getenv("TZ", "America/Argentina/Cordoba"))

MODE = os.getenv("MODE", "PRO").upper()  # DEMO | PRO

# Defaults globales (se pueden pisar por usuario)
DEFAULT_CAPITAL_BASE = float(os.getenv("CAPITAL_BASE", "38901078.37"))
TENORS = [int(x.strip()) for x in os.getenv("TENORS", "1,7,14,30").split(",") if x.strip()]

# Umbrales default por plazo
DEFAULT_THRESHOLDS = {
    1: float(os.getenv("THRESH_1D", "40.0")),
    7: float(os.getenv("THRESH_7D", "43.0")),
    14: float(os.getenv("THRESH_14D", "44.0")),
    30: float(os.getenv("THRESH_30D", "45.0")),
}
PREMIUM_SPREAD = float(os.getenv("PREMIUM_SPREAD", "3.0"))
SUPER_HIGH = float(os.getenv("SUPER_HIGH", "48.0"))

DAILY_HOUR = int(os.getenv("DAILY_HOUR", "10"))

# Payday: N-ésimo día hábil (lun-vie, sin feriados)
DEFAULT_PAYDAY_N = int(os.getenv("PAYDAY_N", "4"))
DEFAULT_PAYDAY_REMIND_DAYS = int(os.getenv("PAYDAY_REMIND_DAYS", "2"))
DEFAULT_SALARY_TARGET = float(os.getenv("SALARY_TARGET", "0"))

# Costo calibrado con boleta (aplica a 1D para estimar neto)
BASE_FEE = float(os.getenv("BASE_FEE", "0.000032"))  # 0,0032%
IVA = float(os.getenv("IVA", "0.21"))               # 21%

DAYS_IN_YEAR = 365
DAYS_MONTH = 30

# Estado y dashboard
STATE_DIR = Path(".state")
DOCS_DIR = Path("docs")
DASH_DIR = DOCS_DIR / "data"
DASH_JSON_PATH = DASH_DIR / "latest.json"


# =========================
# TELEGRAM (hardening)
# =========================
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()  # usado solo si no hay USERS_JSON

def can_send_default() -> bool:
    return bool(TG_BOT_TOKEN) and bool(TG_CHAT_ID)

def send_message(chat_id: str, msg: str):
    if not TG_BOT_TOKEN or not chat_id:
        return  # no hard-fail en scheduled si falta token/chat
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=20)
    r.raise_for_status()


# =========================
# UTIL
# =========================
def money(n: float) -> str:
    s = f"{n:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${s}"

def gross_daily(capital: float, tna: float) -> float:
    return capital * (tna / 100.0) / DAYS_IN_YEAR

def estimated_costs_daily_1d(capital: float) -> float:
    # 1D: costos aproximados calibrados: (0,0032% + IVA)
    return capital * BASE_FEE * (1.0 + IVA)

def trend(prev: Optional[float], curr: Optional[float]) -> str:
    if prev is None or curr is None:
        return "—"
    if curr > prev:
        return f"⬆ (+{curr-prev:.2f})"
    if curr < prev:
        return f"⬇ (-{prev-curr:.2f})"
    return "➡ 0.00"

def is_business_day(d: date) -> bool:
    return d.weekday() < 5

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

def ensure_dirs():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DASH_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

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


# =========================
# DATA FETCH
# =========================
def fetch_rates(tenors: List[int]) -> Dict[int, Optional[float]]:
    headers = {"User-Agent": "Mozilla/5.0"}
    html = requests.get(BYMA_HTML_URL, headers=headers, timeout=25).text
    rates: Dict[int, Optional[float]] = {}
    for d in tenors:
        pattern = rf"{d}\s*D[IÍ]A(?:S)?\s*.*?PESOS.*?(\d{{1,2}}[.,]\d{{1,2}})\s*%"
        m = re.search(pattern, html, re.I | re.S)
        rates[d] = float(m.group(1).replace(",", ".")) if m else None
    return rates


# =========================
# MULTI-USER
# =========================
def load_users() -> List[Dict[str, Any]]:
    """
    Multi-usuario SaaS simple:
    - USERS_JSON secret con lista de usuarios
    - si no existe, usa TG_CHAT_ID + defaults
    """
    raw = os.getenv("USERS_JSON", "").strip()
    if raw:
        try:
            users = json.loads(raw)
            if isinstance(users, list):
                # filtrar los que tengan chat_id
                out = []
                for u in users:
                    if isinstance(u, dict) and str(u.get("chat_id", "")).strip():
                        out.append(u)
                return out
        except Exception:
            pass

    # fallback single-user
    if can_send_default():
        return [{
            "name": "default",
            "chat_id": TG_CHAT_ID,
            "capital": DEFAULT_CAPITAL_BASE,
            "payday_n": DEFAULT_PAYDAY_N,
            "payday_remind_days": DEFAULT_PAYDAY_REMIND_DAYS,
            "salary_target": DEFAULT_SALARY_TARGET,
            "mode": MODE,
        }]

    return []


def thresholds_for_user(user: Dict[str, Any]) -> Dict[int, float]:
    th = dict(DEFAULT_THRESHOLDS)
    # overrides opcionales por usuario: thresh_1d, thresh_7d, etc.
    for d in TENORS:
        k = f"thresh_{d}d"
        if k in user:
            try:
                th[d] = float(user[k])
            except Exception:
                pass
    return th


# =========================
# STATE PER USER
# =========================
def state_path_for(chat_id: str) -> Path:
    # un estado por usuario
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", chat_id)
    return STATE_DIR / f"state_{safe}.json"

def load_state(chat_id: str) -> Dict[str, Any]:
    return safe_load_json(state_path_for(chat_id), {
        "last_rates": {},
        "crossed": {},
        "premium_sent": {},
        "super_sent": False,
        "last_daily": "",
        "payday_notified": "",
        "payday_remind": ""
    })

def save_state(chat_id: str, st: Dict[str, Any]):
    safe_write_json(state_path_for(chat_id), st)


# =========================
# MESSAGE BUILDERS
# =========================
def watermark(mode: str) -> str:
    return "\n\n🧪 Modo DEMO" if mode == "DEMO" else ""

def msg_net_block_1d(rate_1d: float, capital: float) -> str:
    gross = gross_daily(capital, rate_1d)
    costs = estimated_costs_daily_1d(capital)
    net = gross - costs
    return (
        f"💰 Estimado 1D (con {money(capital)}):\n"
        f"• Bruto: {money(gross)}\n"
        f"• Costos: {money(costs)}\n"
        f"• Neto: {money(net)}"
    )

def msg_opportunity(d: int, r: float, rate_1d: float, tr: str, capital: float, mode: str) -> str:
    base = (
        f"🟢 OPORTUNIDAD {d}D\n\n"
        f"{d}D: {r:.2f}% {tr}\n"
        f"1D: {rate_1d:.2f}%\n\n"
        f"≈ {money(gross_daily(capital, r))}/día | ≈ {money(gross_daily(capital, r)*DAYS_MONTH)}/mes(30d)"
    )
    if d == 1 and mode == "PRO":
        base += "\n\n" + msg_net_block_1d(rate_1d, capital)
    return base + watermark(mode)

def msg_premium(d: int, r: float, rate_1d: float, spread: float, capital: float, mode: str) -> str:
    base = (
        f"🟣 PREMIO POR LOCKEAR {d}D\n\n"
        f"{d}D: {r:.2f}% | 1D: {rate_1d:.2f}%\n"
        f"Premio: +{spread:.2f} pts\n\n"
        f"≈ {money(gross_daily(capital, r))}/día (bruto aprox)\n"
        f"👉 Oportunidad si podés inmovilizar {d} días"
    )
    return base + watermark(mode)

def msg_super(best_d: int, best_r: float, rate_1d: float, capital: float, mode: str) -> str:
    base = (
        f"🔥 SÚPER OPORTUNIDAD\n\n"
        f"Mejor plazo: {best_d}D → {best_r:.2f}%\n"
        f"1D: {rate_1d:.2f}%\n\n"
        f"≈ {money(gross_daily(capital, best_r))}/día (bruto aprox)"
    )
    return base + watermark(mode)

def msg_daily_summary(rates: Dict[int, Optional[float]], last_rates: Dict[str, Any], capital: float, payday: date, mode: str) -> str:
    lines = [
        "📊 Resumen diario – Curva de Cauciones (Pesos)",
        "",
        f"Capital base: {money(capital)}",
        f"Payday (4º hábil): {payday.isoformat()}",
        "",
        "Plazo | Tasa | Tendencia | $/día | $/mes(30d)",
        "---|---:|---:|---:|---:"
    ]
    for d in sorted(rates.keys()):
        r = rates[d]
        prev = last_rates.get(str(d))
        tr = trend(prev, r)
        if r is None:
            lines.append(f"{d}D | — | — | — | —")
        else:
            dd = gross_daily(capital, r)
            mm = dd * DAYS_MONTH
            lines.append(f"{d}D | {r:.2f}% | {tr} | {money(dd)} | {money(mm)}")

    if mode == "PRO" and rates.get(1) is not None:
        lines += ["", msg_net_block_1d(float(rates[1]), capital)]

    return "\n".join(lines) + watermark(mode)


# =========================
# PAYDAY LOGIC
# =========================
def payday_logic(now: datetime, user: Dict[str, Any], rates: Dict[int, Optional[float]], st: Dict[str, Any]):
    chat_id = str(user["chat_id"])
    mode = str(user.get("mode", MODE)).upper()
    capital = float(user.get("capital", DEFAULT_CAPITAL_BASE))
    payday_n = int(user.get("payday_n", DEFAULT_PAYDAY_N))
    remind_days = int(user.get("payday_remind_days", DEFAULT_PAYDAY_REMIND_DAYS))
    salary_target = float(user.get("salary_target", DEFAULT_SALARY_TARGET))

    today = now.date()
    payday = nth_business_day(today.year, today.month, payday_n)
    remind = business_days_before(payday, remind_days)

    r1 = rates.get(1)
    if r1 is None:
        return

    gross = gross_daily(capital, r1)
    costs = estimated_costs_daily_1d(capital)
    net = gross - costs

    if today == remind and st.get("payday_remind") != str(remind):
        msg = (
            f"🗓️ Recordatorio SUELDO\n\n"
            f"El cobro es el {payday_n}º día hábil: {payday.isoformat()}\n"
            f"Hoy faltan {remind_days} hábiles.\n\n"
            f"1D: {r1:.2f}% | Neto estimado 1D: {money(net)}\n"
            f"👉 Sugerencia: priorizar 1D para llegar líquido."
        )
        if salary_target > 0:
            msg += f"\n🎯 Sueldo objetivo: {money(salary_target)}"
        send_message(chat_id, msg + watermark(mode))
        st["payday_remind"] = str(remind)

    if today == payday and st.get("payday_notified") != str(payday):
        msg = (
            f"💵 HOY ES DÍA DE SUELDO\n\n"
            f"Fecha: {payday.isoformat()}\n"
            f"1D: {r1:.2f}% | Neto estimado 1D: {money(net)}\n\n"
            f"👉 Retirar sueldo y rearmar estrategia."
        )
        send_message(chat_id, msg + watermark(mode))
        st["payday_notified"] = str(payday)


# =========================
# DASHBOARD OUTPUT
# =========================
def build_dashboard_payload(now: datetime, rates: Dict[int, Optional[float]], users: List[Dict[str, Any]]) -> Dict[str, Any]:
    # payload general (sin datos sensibles de usuarios)
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
        "users_count": len(users),
    }


# =========================
# MAIN
# =========================
def main():
    ensure_dirs()
    now = datetime.now(TZ)

    # 1) Fetch rates once per run
    try:
        rates = fetch_rates(TENORS)
    except Exception as e:
        # No hard-fail total: si no se puede fetch, no enviar spam
        # pero dejar huella en dashboard
        rates = {d: None for d in TENORS}

    # 2) Load users
    users = load_users()

    # 3) Dashboard JSON (siempre)
    dash = build_dashboard_payload(now, rates, users)
    safe_write_json(DASH_JSON_PATH, dash)

    # Si no hay usuarios configurados, termina sin fallar
    if not users:
        return

    # 4) Process each user
    for user in users:
        chat_id = str(user.get("chat_id", "")).strip()
        if not chat_id:
            continue

        user_mode = str(user.get("mode", MODE)).upper()
        capital = float(user.get("capital", DEFAULT_CAPITAL_BASE))
        thresholds = thresholds_for_user(user)

        st = load_state(chat_id)
        last_rates = st.get("last_rates", {})

        r1 = rates.get(1)
        if r1 is None:
            # mensaje de error una sola vez por usuario
            if st.get("last_error") != "NO_1D":
                send_message(chat_id, "⚠️ No pude leer la tasa 1D (fuente BYMA HTML)." + watermark(user_mode))
                st["last_error"] = "NO_1D"
                save_state(chat_id, st)
            continue
        st["last_error"] = ""

        # Payday logic
        payday_logic(now, user, rates, st)

        # SUPER (una vez, por usuario)
        if not st.get("super_sent", False):
            best = None
            for d, r in rates.items():
                if r is not None and (best is None or r > best[1]):
                    best = (d, r)
            if best and best[1] >= SUPER_HIGH:
                send_message(chat_id, msg_super(best[0], best[1], r1, capital, user_mode))
                st["super_sent"] = True

        # DEMO: reducir ruido (solo 1D + resumen)
        active_tenors = [1] if user_mode == "DEMO" else TENORS

        # Cruces por umbral (anti-spam)
        crossed = st.get("crossed", {})
        for d in active_tenors:
            r = rates.get(d)
            if r is None:
                continue
            thr = thresholds.get(d, thresholds.get(1, 40.0))
            is_above = r >= thr
            was_above = bool(crossed.get(str(d), False))

            if is_above and not was_above:
                tr = trend(last_rates.get(str(d)), r)
                send_message(chat_id, msg_opportunity(d, r, r1, tr, capital, user_mode))
            crossed[str(d)] = is_above
        st["crossed"] = crossed

        # Premio vs 1D (solo PRO)
        if user_mode != "DEMO":
            premium_sent = st.get("premium_sent", {})
            for d in active_tenors:
                if d == 1:
                    continue
                r = rates.get(d)
                if r is None:
                    continue
                spread = r - r1
                is_premium = spread >= PREMIUM_SPREAD
                was_premium = bool(premium_sent.get(str(d), False))
                if is_premium and not was_premium:
                    send_message(chat_id, msg_premium(d, r, r1, spread, capital, user_mode))
                premium_sent[str(d)] = is_premium
            st["premium_sent"] = premium_sent

        # Resumen diario (1 vez por día)
        today_str = now.strftime("%Y-%m-%d")
        if now.hour == DAILY_HOUR and st.get("last_daily") != today_str:
            payday = nth_business_day(now.year, now.month, int(user.get("payday_n", DEFAULT_PAYDAY_N)))
            send_message(chat_id, msg_daily_summary(rates, last_rates, capital, payday, user_mode))
            st["last_daily"] = today_str

        # Guardar tasas para tendencia
        st["last_rates"] = {str(k): v for k, v in rates.items() if v is not None}
        save_state(chat_id, st)


if __name__ == "__main__":
    main()
