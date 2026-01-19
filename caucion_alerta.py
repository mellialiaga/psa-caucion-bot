import os
import re
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# =========================
# Helpers: ENV safe parsing
# =========================

def env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw != "" else default

def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    if raw == "":
        return default
    return float(raw.replace(",", "."))

def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    if raw == "":
        return default
    return int(raw)

def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return default


# =========================
# Config (defaults)
# =========================

TZ = ZoneInfo("America/Argentina/Cordoba")

TOKEN = env_str("TG_BOT_TOKEN")
SINGLE_CHAT_ID = env_str("TG_CHAT_ID")  # fallback single user

MODE = env_str("MODE", "commercial").lower()  # demo | commercial
SOURCE_NAME = env_str("SOURCE_NAME", "BMB/BYMA")
BYMA_HTML_URL = env_str("BYMA_HTML_URL", "https://www.bullmarketbrokers.com/Cotizaciones/cauciones")

# Thresholds default (used if not specified per-user)
DEF_THRESH_RED    = env_float("THRESH_RED", 35.5)
DEF_THRESH_GREEN  = env_float("THRESH_GREEN", 40.0)
DEF_THRESH_ROCKET = env_float("THRESH_ROCKET", 45.0)

DEF_DAILY_HOUR = env_int("DAILY_HOUR", 10)
DEF_CAPITAL_BASE = env_float("CAPITAL_BASE", 38901078.37)

DAYS_IN_YEAR = 365

# State storage (repo)
STATE_PATH = Path(".state/state.json")

# Dashboard (GitHub Pages via /docs)
DASH_DIR = Path("docs")
DASH_DATA = DASH_DIR / "data"
DASH_LATEST = DASH_DATA / "latest.json"
DASH_HISTORY = DASH_DATA / "history.json"  # lightweight rolling log

# Monitoring tenors (days)
TENORS_DEFAULT = [1, 7, 30]  # podes ajustar
HTTP_TIMEOUT = 25


# =========================
# Telegram
# =========================

def send_message(chat_id: str, text: str) -> None:
    if not TOKEN:
        raise RuntimeError("Missing TG_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)
    r.raise_for_status()


# =========================
# Formatting
# =========================

def pct(n: float) -> str:
    return f"{n:.2f}%"

def money(n: float) -> str:
    # AR format: 1.234.567
    s = f"{n:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${s}"

def daily_income(capital: float, tna: float) -> float:
    return capital * (tna / 100.0) / DAYS_IN_YEAR


# =========================
# Users (multi-user)
# USERS_JSON example:
# [
#   {"chat_id":"123", "name":"Pablo", "capital_base":38901078.37, "daily_hour":10,
#    "thresh_red":35.5, "thresh_green":40.0, "thresh_rocket":45.0, "tenors":[1,7,30]}
# ]
# =========================

def load_users() -> List[Dict[str, Any]]:
    users_json = os.getenv("USERS_JSON")
    users: List[Dict[str, Any]] = []

    if users_json and users_json.strip() != "":
        try:
            parsed = json.loads(users_json)
            if isinstance(parsed, list):
                for u in parsed:
                    if not isinstance(u, dict):
                        continue
                    chat_id = str(u.get("chat_id", "")).strip()
                    if not chat_id:
                        continue
                    users.append(u)
        except Exception:
            # fallback if json invalid
            users = []

    # fallback single user
    if not users:
        if not SINGLE_CHAT_ID:
            raise RuntimeError("Missing USERS_JSON and TG_CHAT_ID. Provide at least one.")
        users = [{
            "chat_id": SINGLE_CHAT_ID,
            "name": "Usuario",
            "capital_base": DEF_CAPITAL_BASE,
            "daily_hour": DEF_DAILY_HOUR,
            "thresh_red": DEF_THRESH_RED,
            "thresh_green": DEF_THRESH_GREEN,
            "thresh_rocket": DEF_THRESH_ROCKET,
            "tenors": TENORS_DEFAULT
        }]

    # normalize defaults per user
    for u in users:
        u["name"] = str(u.get("name", "Usuario")).strip() or "Usuario"
        u["capital_base"] = float(u.get("capital_base", DEF_CAPITAL_BASE) or DEF_CAPITAL_BASE)
        u["daily_hour"] = int(u.get("daily_hour", DEF_DAILY_HOUR) or DEF_DAILY_HOUR)
        u["thresh_red"] = float(u.get("thresh_red", DEF_THRESH_RED) or DEF_THRESH_RED)
        u["thresh_green"] = float(u.get("thresh_green", DEF_THRESH_GREEN) or DEF_THRESH_GREEN)
        u["thresh_rocket"] = float(u.get("thresh_rocket", DEF_THRESH_ROCKET) or DEF_THRESH_ROCKET)
        ten = u.get("tenors", TENORS_DEFAULT)
        if not isinstance(ten, list) or not ten:
            ten = TENORS_DEFAULT
        u["tenors"] = [int(x) for x in ten if str(x).strip().isdigit()]
        if not u["tenors"]:
            u["tenors"] = TENORS_DEFAULT

    return users


# =========================
# Fetch rates
# - robust parsing for multiple tenors
# =========================

def fetch_html(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.text

def _find_rate_for_tenor(html: str, days: int) -> Optional[float]:
    """
    Tries to extract rate (TNA) for given tenor in days from an HTML page.
    This is heuristic-based; adjust regex to match BMB/BYMA markup if needed.
    """
    # Common variants: "1D", "1 día", "1 dias", "1 días", "7 días", etc.
    day_patterns = [
        rf"{days}\s*D[IÍ]A[S]?",          # 1 DIA / 7 DÍAS
        rf"{days}\s*D",                   # 1D / 7D
        rf"\({days}\s*d[ií]a[s]?\)",      # (1 días)
    ]

    # Accept numbers like 34,500 or 34.500 or 34.50
    rate_pattern = r"(\d{1,3}(?:[.,]\d{1,3})?)"

    for dp in day_patterns:
        # Strategy: find the block around the tenor and then capture a nearby "Tasa" number
        m = re.search(rf"({dp}).{{0,400}}?Tasa.{{0,120}}?{rate_pattern}", html, re.I | re.S)
        if m:
            val = m.group(2)
            try:
                return float(val.replace(".", "").replace(",", "."))
            except Exception:
                pass

        # Another strategy: capture first % after tenor
        m2 = re.search(rf"({dp}).{{0,400}}?{rate_pattern}\s*%", html, re.I | re.S)
        if m2:
            val = m2.group(2)
            try:
                return float(val.replace(".", "").replace(",", "."))
            except Exception:
                pass

    return None

def fetch_rates(days_list: List[int]) -> Dict[int, Optional[float]]:
    html = fetch_html(BYMA_HTML_URL)
    out: Dict[int, Optional[float]] = {}
    for d in days_list:
        out[d] = _find_rate_for_tenor(html, d)
    return out


# =========================
# State
# =========================

def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_err": False,
        "last_daily": {},      # chat_id -> yyyy-mm-dd
        "last_band": {},       # chat_id -> {"1":"GREEN", "7":"RED", ...}
        "last_sent": {},       # chat_id -> {"key":"timestamp"}
        "last_rates": {},      # tenor->last known rate
        "updated_at": ""
    }

def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def now_str() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


# =========================
# Decision logic (balanced)
# =========================

def band_for_rate(rate: float, red: float, green: float, rocket: float) -> str:
    if rate >= rocket:
        return "ROCKET"
    if rate >= green:
        return "GREEN"
    if rate <= red:
        return "RED"
    return "MID"

def build_alert_text(user: Dict[str, Any], tenor: int, rate: float) -> str:
    name = user["name"]
    cap = float(user["capital_base"])
    di = daily_income(cap, rate)

    prefix = ""
    if MODE == "demo":
        prefix = "🧪 DEMO | "

    return (
        f"{prefix}📌 {name} | Caución {tenor}D ({SOURCE_NAME})\n"
        f"📈 Tasa (TNA): {pct(rate)}\n"
        f"💰 Ingreso estimado diario (sobre {money(cap)}): {money(di)}\n"
        f"🕒 {now_str()}"
    )

def should_send_transition(prev_band: str, new_band: str) -> bool:
    # Only notify on meaningful band changes
    if prev_band != new_band and new_band in ("RED", "GREEN", "ROCKET"):
        return True
    return False


# =========================
# Dashboard generation
# =========================

def ensure_dashboard_assets() -> None:
    DASH_DIR.mkdir(parents=True, exist_ok=True)
    (DASH_DIR / "data").mkdir(parents=True, exist_ok=True)

    # Minimal static dashboard (index + app.js)
    index_path = DASH_DIR / "index.html"
    app_path = DASH_DIR / "app.js"
    style_path = DASH_DIR / "style.css"

    if not index_path.exists():
        index_path.write_text("""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>PSA Caución Bot | Dashboard</title>
  <link rel="stylesheet" href="./style.css"/>
</head>
<body>
  <header>
    <h1>PSA Caución Bot</h1>
    <p class="sub">Dashboard simple (GitHub Pages) – últimas tasas y estado</p>
  </header>

  <main>
    <section class="card">
      <h2>Última actualización</h2>
      <div id="meta">Cargando…</div>
    </section>

    <section class="card">
      <h2>Últimas tasas</h2>
      <table>
        <thead>
          <tr><th>Plazo</th><th>Tasa (TNA)</th><th>Banda</th></tr>
        </thead>
        <tbody id="rates"></tbody>
      </table>
    </section>

    <section class="card">
      <h2>Historial (últimos puntos)</h2>
      <div id="history">Cargando…</div>
    </section>
  </main>

  <footer>
    <p>Generado automáticamente por GitHub Actions.</p>
  </footer>

  <script src="./app.js"></script>
</body>
</html>
""", encoding="utf-8")

    if not app_path.exists():
        app_path.write_text("""async function loadJson(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return await r.json();
}

function bandEmoji(b) {
  if (b === "ROCKET") return "🚀";
  if (b === "GREEN") return "🟢";
  if (b === "RED") return "🔴";
  return "⚪";
}

(async () => {
  try {
    const latest = await loadJson("./data/latest.json");
    const hist = await loadJson("./data/history.json");

    document.getElementById("meta").innerHTML =
      `<b>${latest.updated_at}</b><br/>Fuente: ${latest.source_name || "-"} | Modo: ${latest.mode || "-"}`;

    const rows = Object.entries(latest.rates || {}).map(([tenor, obj]) => {
      const rate = (obj && obj.rate != null) ? obj.rate.toFixed(2) + "%" : "N/D";
      const band = (obj && obj.band) ? obj.band : "N/D";
      return `<tr>
        <td>${tenor}D</td>
        <td>${rate}</td>
        <td>${bandEmoji(band)} ${band}</td>
      </tr>`;
    }).join("");

    document.getElementById("rates").innerHTML = rows || "<tr><td colspan='3'>Sin datos</td></tr>";

    const h = (hist.items || []).slice(-30).reverse();
    document.getElementById("history").innerHTML =
      h.map(x => `• <b>${x.at}</b> — ${x.tenor}D: ${x.rate}% (${x.band})`).join("<br/>") || "Sin historial";
  } catch (e) {
    document.getElementById("meta").textContent = "Error cargando dashboard: " + e.message;
  }
})();
""", encoding="utf-8")

    if not style_path.exists():
        style_path.write_text("""body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 0; background: #0b1220; color: #e8eefc; }
header { padding: 24px; border-bottom: 1px solid #1d2a44; background: #0e1730; }
h1 { margin: 0 0 6px 0; font-size: 22px; }
.sub { margin: 0; opacity: 0.8; }
main { max-width: 960px; margin: 0 auto; padding: 18px; display: grid; gap: 14px; }
.card { background: #0e1730; border: 1px solid #1d2a44; border-radius: 10px; padding: 14px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 10px; border-bottom: 1px solid #1d2a44; text-align: left; }
footer { padding: 18px; text-align: center; opacity: 0.7; }
""", encoding="utf-8")


def update_dashboard(rates: Dict[int, Optional[float]], bands: Dict[int, str]) -> None:
    ensure_dashboard_assets()
    DASH_DATA.mkdir(parents=True, exist_ok=True)

    latest_payload = {
        "updated_at": now_str(),
        "mode": MODE,
        "source_name": SOURCE_NAME,
        "byma_html_url": BYMA_HTML_URL,
        "rates": {}
    }

    for tenor, rate in rates.items():
        latest_payload["rates"][str(tenor)] = {
            "rate": None if rate is None else float(rate),
            "band": bands.get(tenor, "N/D")
        }

    DASH_LATEST.write_text(json.dumps(latest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # History (rolling)
    history = {"items": []}
    if DASH_HISTORY.exists():
        try:
            history = json.loads(DASH_HISTORY.read_text(encoding="utf-8"))
        except Exception:
            history = {"items": []}

    items = history.get("items", [])
    if not isinstance(items, list):
        items = []

    for tenor, rate in rates.items():
        if rate is None:
            continue
        items.append({
            "at": latest_payload["updated_at"],
            "tenor": int(tenor),
            "rate": round(float(rate), 2),
            "band": bands.get(tenor, "MID")
        })

    # keep last 200
    items = items[-200:]
    history["items"] = items
    DASH_HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================
# Main
# =========================

def main() -> None:
    users = load_users()
    st = load_state()
    st.setdefault("last_daily", {})
    st.setdefault("last_band", {})
    st.setdefault("last_sent", {})
    st.setdefault("last_rates", {})

    # union of all tenors required by any user
    tenors_all = sorted({t for u in users for t in u["tenors"]})
    rates = fetch_rates(tenors_all)

    # if all None -> warn once
    if all(rates[t] is None for t in tenors_all):
        if not st.get("last_err", False):
            for u in users:
                send_message(str(u["chat_id"]), f"⚠️ No pude leer tasas de caución ({SOURCE_NAME}). Reintento automático.")
            st["last_err"] = True
            st["updated_at"] = now_str()
            save_state(st)
        return

    st["last_err"] = False

    # Build "global" bands for dashboard (using defaults)
    dash_bands: Dict[int, str] = {}
    for t in tenors_all:
        r = rates[t]
        if r is None:
            dash_bands[t] = "N/D"
        else:
            dash_bands[t] = band_for_rate(r, DEF_THRESH_RED, DEF_THRESH_GREEN, DEF_THRESH_ROCKET)

    # Alerts per user
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")

    for u in users:
        chat_id = str(u["chat_id"])
        name = u["name"]

        red = float(u["thresh_red"])
        green = float(u["thresh_green"])
        rocket = float(u["thresh_rocket"])
        daily_hour = int(u["daily_hour"])

        st["last_band"].setdefault(chat_id, {})
        st["last_daily"].setdefault(chat_id, "")

        # 1) band transitions
        for tenor in u["tenors"]:
            rate = rates.get(tenor)
            if rate is None:
                continue

            new_band = band_for_rate(rate, red, green, rocket)
            prev_band = st["last_band"][chat_id].get(str(tenor), "MID")

            if should_send_transition(prev_band, new_band):
                if new_band == "ROCKET":
                    extra = "\n🔥 Banda: 🚀 ROCKET (tasa excepcional)"
                elif new_band == "GREEN":
                    extra = "\n✅ Banda: 🟢 GREEN (oportunidad)"
                else:
                    extra = "\n⚠️ Banda: 🔴 RED (baja)"

                msg = build_alert_text(u, tenor, rate) + extra
                send_message(chat_id, msg)

            st["last_band"][chat_id][str(tenor)] = new_band

        # 2) daily summary
        if now.hour == daily_hour and st["last_daily"][chat_id] != today:
            lines = []
            for tenor in u["tenors"]:
                rate = rates.get(tenor)
                if rate is None:
                    lines.append(f"• {tenor}D: N/D")
                    continue
                b = band_for_rate(rate, red, green, rocket)
                emoji = "🚀" if b == "ROCKET" else ("🟢" if b == "GREEN" else ("🔴" if b == "RED" else "⚪"))
                lines.append(f"• {tenor}D: {pct(rate)} {emoji} {b}")

            prefix = "🧪 DEMO | " if MODE == "demo" else ""
            msg = (
                f"{prefix}📊 {name} | Resumen diario ({SOURCE_NAME})\n"
                + "\n".join(lines)
                + f"\n🕒 {now_str()}"
            )
            send_message(chat_id, msg)
            st["last_daily"][chat_id] = today

    # Save last known rates
    st["last_rates"] = {str(k): (None if v is None else float(v)) for k, v in rates.items()}
    st["updated_at"] = now_str()
    save_state(st)

    # Update dashboard
    update_dashboard(rates, dash_bands)


if __name__ == "__main__":
    main()