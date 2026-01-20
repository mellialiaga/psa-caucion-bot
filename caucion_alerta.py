import os, re, json, requests
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
# Helpers: env robustos
# =========================
def env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v if v != "" else default

def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    if v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default

def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    if v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default

# =========================
# Runtime config
# =========================
MODE = env_str("MODE", "demo").lower()  # demo | commercial

SOURCE_NAME = env_str("SOURCE_NAME", DEF_SOURCE_NAME)
BYMA_HTML_URL = env_str("BYMA_HTML_URL", DEF_BYMA_HTML_URL)

# Umbrales (si faltan o están vacíos, usa defaults)
THRESH_RED    = env_float("THRESH_RED", 35.5)
THRESH_GREEN  = env_float("THRESH_GREEN", 38.0)
THRESH_ROCKET = env_float("THRESH_ROCKET", 40.0)

# Resumen diario
DAILY_HOUR = env_int("DAILY_HOUR", 10)

# Capital (para estimar ingreso diario)
CAPITAL_BASE = env_float("CAPITAL_BASE", 38901078.37)

# Telegram
TOKEN = env_str("TG_BOT_TOKEN", "")
DEFAULT_CHAT_ID = env_str("TG_CHAT_ID", "")

# Multi-user:
# USERS_JSON ejemplo:
# [{"name":"Pablo","chat_id":"123","capital":38901078.37},{"name":"Javier","chat_id":"456","capital":2000000}]
USERS_JSON_RAW = env_str("USERS_JSON", "")

def load_users():
    users = []

    # 1) Si viene USERS_JSON, usarlo
    if USERS_JSON_RAW:
        try:
            parsed = json.loads(USERS_JSON_RAW)
            if isinstance(parsed, list):
                for u in parsed:
                    if not isinstance(u, dict):
                        continue
                    chat_id = str(u.get("chat_id", "")).strip()
                    if not chat_id:
                        continue
                    users.append({
                        "name": str(u.get("name", "Usuario")).strip() or "Usuario",
                        "chat_id": chat_id,
                        "capital": float(u.get("capital", CAPITAL_BASE)) if str(u.get("capital", "")).strip() != "" else CAPITAL_BASE,
                    })
        except Exception:
            # si está mal formado, caemos al fallback
            users = []

    # 2) Fallback: single-user con TG_CHAT_ID
    if not users and DEFAULT_CHAT_ID:
        users = [{
            "name": "Pablo",
            "chat_id": DEFAULT_CHAT_ID,
            "capital": CAPITAL_BASE
        }]

    return users

def send(chat_id: str, msg: str):
    if not TOKEN:
        raise RuntimeError("Falta TG_BOT_TOKEN (Secret).")
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=25)
    r.raise_for_status()

def money(n: float) -> str:
    # Formato AR: $1.234.567
    s = f"{n:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${s}"

def pct(n: float) -> str:
    return f"{n:.2f}%"

# =========================
# Scraping: 1D y 7D
# =========================
def fetch_rates():
    """
    Devuelve dict: {"1D": float|None, "7D": float|None}
    Heurística:
    - Busca patrones cercanos a "1 día" / "7 días" y toma el primer % en esa zona.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    html = requests.get(BYMA_HTML_URL, headers=headers, timeout=30).text

    def find_rate(days_label: str):
        # Busca algo como "1 día" ... "%" (tasa)
        # tolera separadores coma/punto
        patterns = [
            rf"{days_label}\s*D[IÍ]A.*?(\d{{1,2}}[.,]\d{{1,3}})\s*%",
            rf"{days_label}\s*D[IÍ]AS.*?(\d{{1,2}}[.,]\d{{1,3}})\s*%",
        ]
        for pat in patterns:
            m = re.search(pat, html, re.I | re.S)
            if m:
                return float(m.group(1).replace(",", "."))
        return None

    r1 = find_rate("1")
    r7 = find_rate("7")

    return {"1D": r1, "7D": r7, "source_url": BYMA_HTML_URL}

# =========================
# State + dashboard
# =========================
def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_band": "",               # RED|GREEN|ROCKET|MID|ERR
        "last_daily": "",              # YYYY-MM-DD
        "last_rate_1d": None,
        "last_rate_7d": None,
        "last_opportunity": "",        # texto para no spamear
        "updated_at": ""
    }

def save_state(st: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")

def render_dashboard(st: dict):
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    rate_1d = st.get("last_rate_1d")
    rate_7d = st.get("last_rate_7d")

    def safe(v):
        return "—" if v is None else f"{v:.2f}%"

    html = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>PSA Caución Bot — Dashboard</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; background:#0b1220; color:#e8eefc; }}
    .wrap {{ max-width: 860px; margin: 0 auto; }}
    .card {{ background:#121a2c; border:1px solid #223055; border-radius:16px; padding:18px; margin:14px 0; box-shadow: 0 10px 25px rgba(0,0,0,.25); }}
    .kpi {{ display:flex; gap:12px; flex-wrap:wrap; }}
    .kpi .item {{ flex:1; min-width:220px; background:#0f1730; border:1px solid #223055; border-radius:14px; padding:14px; }}
    .label {{ opacity:.8; font-size:12px; }}
    .value {{ font-size:28px; font-weight:700; margin-top:4px; }}
    .small {{ opacity:.85; font-size:13px; line-height:1.4; }}
    a {{ color:#9ad2ff; }}
    .badge {{ display:inline-block; padding:6px 10px; border-radius:999px; background:#1b2a4d; border:1px solid #2a4172; font-size:12px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1 style="margin:0 0 8px 0;">PSA Caución Bot — Dashboard</h1>
      <div class="small">Estado del bot y últimas tasas detectadas.</div>
      <div style="margin-top:10px;">
        <span class="badge">Modo: {MODE}</span>
        <span class="badge">Fuente: {SOURCE_NAME}</span>
      </div>
    </div>

    <div class="card">
      <div class="kpi">
        <div class="item">
          <div class="label">Tasa 1D</div>
          <div class="value">{safe(rate_1d)}</div>
        </div>
        <div class="item">
          <div class="label">Tasa 7D</div>
          <div class="value">{safe(rate_7d)}</div>
        </div>
        <div class="item">
          <div class="label">Última actualización</div>
          <div class="value" style="font-size:18px;">{st.get("updated_at","—") or "—"}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h3 style="margin:0 0 8px 0;">Config</h3>
      <div class="small">
        Umbrales: RED &lt;= {THRESH_RED:.2f}% | GREEN &gt;= {THRESH_GREEN:.2f}% | ROCKET &gt;= {THRESH_ROCKET:.2f}%<br/>
        Fuente: <a href="{BYMA_HTML_URL}" target="_blank" rel="noopener">{BYMA_HTML_URL}</a>
      </div>
    </div>

    <div class="card">
      <div class="small">© PSA Caución Bot</div>
    </div>
  </div>
</body>
</html>
"""
    DASHBOARD_PATH.write_text(html, encoding="utf-8")

# =========================
# Decision logic
# =========================
def band_for(rate_1d: float | None):
    if rate_1d is None:
        return "ERR"
    if rate_1d >= THRESH_ROCKET:
        return "ROCKET"
    if rate_1d >= THRESH_GREEN:
        return "GREEN"
    if rate_1d <= THRESH_RED:
        return "RED"
    return "MID"

def opportunity_text(rates: dict) -> str:
    r1 = rates.get("1D")
    r7 = rates.get("7D")

    # Si no hay data, nada
    if r1 is None and r7 is None:
        return ""

    # Si 7D supera a 1D por margen, alertar oportunidad en 7D
    if r7 is not None and r1 is not None:
        if r7 >= r1 + 1.0:  # margen configurable si querés
            return f"📌 Oportunidad: 7D está mejor que 1D (+{(r7-r1):.2f} pp)"
    # Si solo hay 7D
    if r7 is not None and r1 is None:
        return "📌 Oportunidad: detecté tasa 7D disponible"
    return ""

# =========================
# Main
# =========================
def main():
    now = datetime.now(TZ)
    st = load_state()
    users = load_users()

    if not users:
        raise RuntimeError("No hay usuarios configurados. Seteá TG_CHAT_ID o USERS_JSON.")

    rates = fetch_rates()
    r1 = rates.get("1D")
    r7 = rates.get("7D")

    st["last_rate_1d"] = r1
    st["last_rate_7d"] = r7
    st["updated_at"] = now.isoformat()

    # Dashboard siempre (aunque sea con —)
    save_state(st)
    render_dashboard(st)

    # Si no pude leer 1D ni 7D, avisar 1 vez y cortar
    if r1 is None and r7 is None:
        if st.get("last_band") != "ERR":
            for u in users:
                send(u["chat_id"], "⚠️ No pude leer tasas de caución (1D/7D). Revisar fuente o selector.")
            st["last_band"] = "ERR"
            save_state(st)
            render_dashboard(st)
        return

    # Band por 1D (si no hay 1D, se considera MID pero igual puede haber oportunidad 7D)
    band = band_for(r1)
    prev_band = st.get("last_band", "")

    # Alertas por cambio de banda (para no spamear)
    if band != prev_band and r1 is not None:
        if band == "ROCKET":
            msg = f"🚀 CAUCIÓN 1D (ROCKET)\nTasa: {pct(r1)}"
        elif band == "GREEN":
            msg = f"🟢 CAUCIÓN 1D (BUENA)\nTasa: {pct(r1)}"
        elif band == "RED":
            msg = f"🔴 CAUCIÓN 1D (BAJA)\nTasa: {pct(r1)}\n👉 Evaluar alternativas"
        else:
            msg = f"ℹ️ CAUCIÓN 1D (NEUTRA)\nTasa: {pct(r1)}"

        if MODE == "demo":
            msg += "\n\n🧪 MODO DEMO: mensajes de prueba."

        for u in users:
            send(u["chat_id"], msg)

        st["last_band"] = band

    # Oportunidad 7D (si aplica) – evita spam por texto repetido
    opp = opportunity_text(rates)
    if opp and opp != st.get("last_opportunity", ""):
        detail = []
        if r1 is not None:
            detail.append(f"1D: {pct(r1)}")
        if r7 is not None:
            detail.append(f"7D: {pct(r7)}")
        extra = "\n".join(detail)

        msg = f"{opp}\n{extra}"
        if MODE == "demo":
            msg += "\n\n🧪 MODO DEMO: mensajes de prueba."

        for u in users:
            send(u["chat_id"], msg)

        st["last_opportunity"] = opp

    # Resumen diario (usa 1D si existe, si no 7D)
    today = now.strftime("%Y-%m-%d")
    if now.hour == DAILY_HOUR and st.get("last_daily") != today:
        for u in users:
            cap = float(u.get("capital", CAPITAL_BASE))
            base_rate = r1 if r1 is not None else (r7 if r7 is not None else 0.0)
            daily_income = cap * (base_rate / 100.0) / DAYS_IN_YEAR

            msg = (
                f"📊 Resumen diario — Caución\n"
                f"1D: {('—' if r1 is None else pct(r1))} | 7D: {('—' if r7 is None else pct(r7))}\n"
                f"Capital: {money(cap)}\n"
                f"Estimación ingreso/día (aprox): {money(daily_income)}"
            )
            if MODE == "demo":
                msg += "\n\n🧪 MODO DEMO: mensajes de prueba."
            send(u["chat_id"], msg)

        st["last_daily"] = today

    save_state(st)
    render_dashboard(st)

if __name__ == "__main__":
    main()
