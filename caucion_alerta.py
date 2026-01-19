import os
import re
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# =========================
# CONFIG
# =========================

TZ = ZoneInfo("America/Argentina/Cordoba")

# Fuente actual (ajustala si tu bot lee otra página)
BYMA_HTML_URL = os.getenv("BYMA_HTML_URL", "https://www.bullmarketbrokers.com/Cotizaciones/cauciones")

# Thresholds default (perfil BALANCED)
THRESH_RED    = float(os.getenv("THRESH_RED", "35.5"))   # < red
THRESH_GREEN  = float(os.getenv("THRESH_GREEN", "37.0")) # >= green
THRESH_ROCKET = float(os.getenv("THRESH_ROCKET", "40.0"))# >= rocket

DAILY_HOUR = int(os.getenv("DAILY_HOUR", "10"))  # resumen diario (hora local)
MODE = os.getenv("MODE", "DEMO")                 # DEMO | COMMERCIAL

# Capital (opcional; si no está, no calcula "ingreso diario")
CAPITAL_BASE = float(os.getenv("CAPITAL_BASE", "0") or "0")
DAYS_IN_YEAR = 365

# Telegram (single-user fallback)
TG_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT  = os.getenv("TG_CHAT_ID", "").strip()

# Multi-user (opcional)
# USERS_JSON debe ser un JSON string con estructura:
# {"users":[{"name":"Pablo","chat_id":"123","enabled":true,"profile":"BALANCED","capital":1000000}]}
USERS_JSON = os.getenv("USERS_JSON", "").strip()

# State + dashboard
STATE_PATH = Path(".state/state.json")
DASH_PATH  = Path("docs/data/latest.json")


# =========================
# HELPERS
# =========================

def safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default


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


def tg_send(token: str, chat_id: str, msg: str):
    if not token or not chat_id:
        return  # No rompe nunca si falta config
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=20)
        r.raise_for_status()
    except Exception:
        # Nunca rompemos por Telegram
        pass


def parse_users():
    """
    Devuelve lista de usuarios a notificar.
    Si USERS_JSON no existe, vuelve a modo single-user con TG_CHAT_ID.
    """
    users = []

    if USERS_JSON:
        try:
            payload = json.loads(USERS_JSON)
            raw = payload.get("users", [])
            for u in raw:
                if not u.get("enabled", True):
                    continue
                users.append({
                    "name": u.get("name", "Usuario"),
                    "chat_id": str(u.get("chat_id", "")).strip(),
                    "profile": u.get("profile", "BALANCED"),
                    "capital": float(u.get("capital", CAPITAL_BASE or 0) or 0),
                })
        except Exception:
            # Si el JSON está mal, caemos a single-user
            users = []

    if not users and TG_CHAT:
        users.append({
            "name": "Pablo",
            "chat_id": TG_CHAT,
            "profile": "BALANCED",
            "capital": CAPITAL_BASE or 0
        })

    return users


def compute_signal(rate: float):
    """
    Semáforo BALANCED por defecto.
    """
    if rate is None:
        return {
            "status": "ERR",
            "status_label": "⚠️ SIN DATOS",
            "action": "—",
            "explain": "No se pudo leer la tasa."
        }

    if rate >= THRESH_ROCKET:
        return {
            "status": "ROCKET",
            "status_label": "🚀 OPORTUNIDAD FUERTE",
            "action": "Caucionar",
            "explain": f"Tasa excepcional (≥ {THRESH_ROCKET:.2f}%)."
        }

    if rate >= THRESH_GREEN:
        return {
            "status": "GREEN",
            "status_label": "🟢 CONVIENE CAUCIONAR",
            "action": "Caucionar",
            "explain": f"Tasa por encima del umbral (≥ {THRESH_GREEN:.2f}%)."
        }

    if rate >= THRESH_RED:
        return {
            "status": "YELLOW",
            "status_label": "🟡 ESPERAR",
            "action": "Esperar",
            "explain": f"Tasa en zona media (≥ {THRESH_RED:.2f}% y < {THRESH_GREEN:.2f}%)."
        }

    return {
        "status": "RED",
        "status_label": "🔴 NO CONVIENE",
        "action": "No caucionar",
        "explain": f"Tasa deprimida (< {THRESH_RED:.2f}%)."
    }


def fetch_rate():
    """
    Extrae la tasa 1D desde HTML.
    Muy tolerante: intenta encontrar "1 día" y un porcentaje cercano.
    """
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        html = requests.get(BYMA_HTML_URL, headers=headers, timeout=25).text
    except Exception:
        return None

    # Busca un porcentaje en contexto "1D / 1 día"
    # Esto es genérico porque las webs cambian.
    # Podés ajustar regex si BMB cambia su HTML.
    m = re.search(r"(1\s*D(I|Í)A|1D).*?(\d{1,2}[.,]\d{1,3})\s*%", html, re.I | re.S)
    if not m:
        # fallback: busca el primer porcentaje razonable en la página
        m2 = re.search(r"(\d{1,2}[.,]\d{1,3})\s*%", html, re.I)
        if not m2:
            return None
        return float(m2.group(1).replace(",", "."))
    return float(m.group(3).replace(",", "."))


def write_dashboard(now: datetime, rate: float, signal: dict, users_count: int):
    ensure_dirs()

    payload = {
        "ts": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "source": "Bull Market Brokers",
        "mode": MODE,
        "rate_1d": rate,
        "status": signal.get("status"),
        "status_label": signal.get("status_label"),
        "action": signal.get("action"),
        "explain": signal.get("explain"),
        "profile": "BALANCED",
        "thresholds": {
            "red": THRESH_RED,
            "green": THRESH_GREEN,
            "rocket": THRESH_ROCKET
        },
        "users_enabled": users_count,
        "note": "Dashboard informativo. No ejecuta operaciones ni brinda asesoramiento financiero."
    }

    DASH_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================
# MAIN
# =========================

def main():
    ensure_dirs()
    now = datetime.now(TZ)
    st = load_state()

    users = parse_users()
    users_count = len(users)

    rate = fetch_rate()
    signal = compute_signal(rate)

    # Siempre escribimos dashboard (incluso si rate None)
    write_dashboard(now, rate, signal, users_count)

    # Si no hay token o no hay usuarios, nunca rompemos
    if not TG_TOKEN or users_count == 0:
        save_state(st)
        return

    # Estado global (para no spamear)
    last_state = st.get("last_state", "")
    last_err_sent = st.get("last_err_sent", False)
    last_daily = st.get("last_daily", "")

    # Error de lectura: avisar una vez
    if rate is None:
        if not last_err_sent:
            for u in users:
                tg_send(TG_TOKEN, u["chat_id"], "⚠️ No pude leer la tasa de Caución 1D (BMB).")
            st["last_err_sent"] = True
        st["last_state"] = "ERR"
        save_state(st)
        return

    # Si vuelve a haber datos, reseteamos flag error
    st["last_err_sent"] = False

    # Alertas por cambio de semáforo (solo para GREEN/ROCKET/RED, amarillo no spamea)
    curr = signal["status"]

    if curr != last_state:
        # 🔥 Mensaje principal
        if curr in ("GREEN", "ROCKET"):
            msg = f"{signal['status_label']}\nTasa 1D: {rate:.2f}%\n{signal['explain']}"
        elif curr == "RED":
            msg = f"{signal['status_label']}\nTasa 1D: {rate:.2f}%\n👉 Evaluar alternativas."
        else:
            msg = None  # YELLOW no se notifica por cambio (opcional)

        if msg:
            for u in users:
                tg_send(TG_TOKEN, u["chat_id"], msg)

        st["last_state"] = curr

    # Resumen diario a cierta hora (1 vez por día)
    today = now.strftime("%Y-%m-%d")
    if now.hour == DAILY_HOUR and last_daily != today:
        # Si hay capital cargado, calcula ingreso estimado por día
        cap = CAPITAL_BASE
        daily_income = None
        if cap and rate:
            daily_income = cap * (rate / 100.0) / DAYS_IN_YEAR

        base = f"📊 Resumen diario Caución 1D\nTasa: {rate:.2f}%\nEstado: {signal['status_label']}\nAcción: {signal['action']}"
        if daily_income is not None:
            base += f"\nEstimación ingreso/día (sobre {cap:,.0f}): ${daily_income:,.0f}".replace(",", ".")

        for u in users:
            tg_send(TG_TOKEN, u["chat_id"], base)

        st["last_daily"] = today

    save_state(st)


if __name__ == "__main__":
    main()