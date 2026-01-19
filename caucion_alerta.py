import os, re, json, time, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# =========================
# CONFIG
# =========================
TZ = ZoneInfo("America/Argentina/Cordoba")

MODE = os.getenv("MODE", "DEMO").strip().upper()  # DEMO | COMMERCIAL
SOURCE_NAME = os.getenv("SOURCE_NAME", "Bull Market Brokers").strip()

# Fuente (ajustable)
BYMA_HTML_URL = os.getenv("BYMA_HTML_URL", "https://www.bullmarketbrokers.com/Cotizaciones/cauciones").strip()

# Defaults (si el usuario no trae config propia)
DEF_THRESH_RED    = float(os.getenv("THRESH_RED", "35.5"))
DEF_THRESH_GREEN  = float(os.getenv("THRESH_GREEN", "37.0"))
DEF_THRESH_ROCKET = float(os.getenv("THRESH_ROCKET", "40.0"))
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "10"))

# Telegram token (común)
TG_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()

# Single-user fallback (si no hay USERS_JSON)
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()

# Multi-user (recomendado)
# {"users":[{"name":"Pablo","chat_id":"123","enabled":true,"capital":38901078.37,"profile":"BALANCED",
#            "thresh_red":35.5,"thresh_green":37.0,"thresh_rocket":40.0}]}
USERS_JSON = os.getenv("USERS_JSON", "").strip()

# Persistencia
STATE_PATH = Path(".state/state.json")
DASH_PATH  = Path("docs/data/latest.json")

DAYS_IN_YEAR = 365


# =========================
# HELPERS
# =========================
def ensure_dirs():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASH_PATH.parent.mkdir(parents=True, exist_ok=True)

def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(st):
    ensure_dirs()
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")

def tg_send(chat_id: str, msg: str):
    if not TG_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=20).raise_for_status()
    except Exception:
        pass

def parse_users():
    users = []
    if USERS_JSON:
        try:
            payload = json.loads(USERS_JSON)
            for u in payload.get("users", []):
                if not u.get("enabled", True):
                    continue
                users.append({
                    "name": u.get("name", "Usuario"),
                    "chat_id": str(u.get("chat_id", "")).strip(),
                    "capital": float(u.get("capital", 0) or 0),
                    "thresh_red": float(u.get("thresh_red", DEF_THRESH_RED)),
                    "thresh_green": float(u.get("thresh_green", DEF_THRESH_GREEN)),
                    "thresh_rocket": float(u.get("thresh_rocket", DEF_THRESH_ROCKET)),
                })
        except Exception:
            users = []

    if not users and TG_CHAT_ID:
        users.append({
            "name": "Pablo",
            "chat_id": TG_CHAT_ID,
            "capital": float(os.getenv("CAPITAL_BASE", "0") or 0),
            "thresh_red": DEF_THRESH_RED,
            "thresh_green": DEF_THRESH_GREEN,
            "thresh_rocket": DEF_THRESH_ROCKET,
        })

    return users

def fetch_rate_1d():
    """
    HTML scraping tolerante. Si cambia el HTML, ajustamos regex.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        html = requests.get(BYMA_HTML_URL, headers=headers, timeout=25).text
    except Exception:
        return None

    # Intenta encontrar "1 día/1D" + porcentaje
    m = re.search(r"(1\s*D(I|Í)A|1D).*?(\d{1,2}[.,]\d{1,3})\s*%", html, re.I | re.S)
    if m:
        return float(m.group(3).replace(",", "."))
    # fallback: primer % razonable
    m2 = re.search(r"(\d{1,2}[.,]\d{1,3})\s*%", html, re.I)
    if m2:
        return float(m2.group(1).replace(",", "."))
    return None

def signal_for_user(rate: float, u: dict):
    if rate is None:
        return ("ERR", "⚠️ SIN DATOS", "—", "No se pudo leer la tasa.")
    if rate >= u["thresh_rocket"]:
        return ("ROCKET", "🚀 OPORTUNIDAD FUERTE", "Caucionar", f"Tasa ≥ {u['thresh_rocket']:.2f}%")
    if rate >= u["thresh_green"]:
        return ("GREEN", "🟢 CONVIENE CAUCIONAR", "Caucionar", f"Tasa ≥ {u['thresh_green']:.2f}%")
    if rate >= u["thresh_red"]:
        return ("YELLOW", "🟡 ESPERAR", "Esperar", f"Zona media (≥ {u['thresh_red']:.2f}% y < {u['thresh_green']:.2f}%)")
    return ("RED", "🔴 NO CONVIENE", "No caucionar", f"Tasa < {u['thresh_red']:.2f}%")

def disclaimer():
    if MODE == "COMMERCIAL":
        return "\n\nℹ️ Información orientativa. No constituye asesoramiento financiero."
    return ""

def write_dashboard(now: datetime, rate: float, users: list):
    ensure_dirs()
    payload = {
        "ts": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "mode": MODE,
        "source": SOURCE_NAME,
        "rate_1d": rate,
        "users_enabled": len(users),
        "url": BYMA_HTML_URL,
        "note": "Dashboard informativo. No ejecuta operaciones."
    }
    DASH_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

# (Opcional) Comandos: /status y /help (simple, sin webhook)
def poll_last_command():
    """
    Pull simple: lee updates recientes, busca /status o /help del chat.
    Limitación: sin webhook, solo sirve si corrés frecuente.
    """
    if not TG_TOKEN:
        return []
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
        data = requests.get(url, timeout=20).json()
        if not data.get("ok"):
            return []
        cmds = []
        for upd in data.get("result", [])[-20:]:
            msg = upd.get("message", {})
            text = (msg.get("text") or "").strip()
            chat_id = str((msg.get("chat") or {}).get("id", "")).strip()
            if text in ("/status", "/help") and chat_id:
                cmds.append((chat_id, text))
        return cmds
    except Exception:
        return []

# =========================
# MAIN
# =========================
def main():
    ensure_dirs()
    now = datetime.now(TZ)

    st = load_state()
    users = parse_users()

    rate = fetch_rate_1d()
    write_dashboard(now, rate, users)

    # 1) Manejo de error lectura (avisar una vez)
    if rate is None:
        if not st.get("err_sent"):
            for u in users:
                tg_send(u["chat_id"], "⚠️ No pude leer la tasa de Caución 1D (BMB).")
            st["err_sent"] = True
        save_state(st)
        return
    st["err_sent"] = False

    # 2) Alertas por usuario (evita spam por estado repetido)
    # state key: last_state_<chat_id>
    for u in users:
        key = f"last_state_{u['chat_id']}"
        last = st.get(key, "")
        status, label, action, explain = signal_for_user(rate, u)

        # solo notifica cambios relevantes
        notify = False
        if status in ("GREEN", "ROCKET", "RED") and status != last:
            notify = True

        if notify:
            msg = (
                f"{label}\n"
                f"Tasa 1D: {rate:.2f}%\n"
                f"Acción: {action}\n"
                f"{explain}"
                f"{disclaimer()}"
            )
            tg_send(u["chat_id"], msg)
            st[key] = status

    # 3) Resumen diario (1 vez por día)
    today = now.strftime("%Y-%m-%d")
    if now.hour == DAILY_HOUR and st.get("last_daily") != today:
        for u in users:
            status, label, action, _ = signal_for_user(rate, u)

            daily_income = None
            if u.get("capital", 0) > 0:
                daily_income = u["capital"] * (rate / 100.0) / DAYS_IN_YEAR

            msg = (
                f"📊 Resumen diario Caución 1D\n"
                f"Tasa: {rate:.2f}%\n"
                f"Estado: {label}\n"
                f"Acción: {action}"
            )
            if daily_income is not None:
                msg += f"\nEstimación ingreso/día (sobre {u['capital']:,.0f}): ${daily_income:,.0f}".replace(",", ".")
            msg += disclaimer()

            tg_send(u["chat_id"], msg)

        st["last_daily"] = today

    # 4) Comandos simples (opcional)
    cmds = poll_last_command()
    for chat_id, cmd in cmds:
        if cmd == "/help":
            tg_send(chat_id, "Comandos:\n/status -> estado actual\n/help -> ayuda")
        elif cmd == "/status":
            # status del usuario (buscamos thresholds del usuario si existe)
            u = next((x for x in users if x["chat_id"] == chat_id), None)
            if not u:
                u = {"thresh_red": DEF_THRESH_RED, "thresh_green": DEF_THRESH_GREEN, "thresh_rocket": DEF_THRESH_ROCKET}
            status, label, action, explain = signal_for_user(rate, u)
            tg_send(chat_id, f"{label}\nTasa 1D: {rate:.2f}%\nAcción: {action}\n{explain}{disclaimer()}")

    save_state(st)

if __name__ == "__main__":
    main()