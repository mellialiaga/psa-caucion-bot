import os, re, json, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# =========================
# CONFIG
# =========================
# Fuente (dejamos el URL que venías usando; si lo cambiaste a BMB, ajustalo acá)
BYMA_HTML_URL = os.getenv("BYMA_HTML_URL", "https://www.portfoliopersonal.com/Cotizaciones/Cauciones")

TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
DEFAULT_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

MODE = os.getenv("MODE", "PRO").upper()        # DEMO / PRO
PROFILE = os.getenv("PROFILE", "BALANCED").upper()  # BALANCED / AGGRESSIVE / CONSERVATIVE (por ahora usamos BALANCED)

TZ = ZoneInfo("America/Argentina/Cordoba")

STATE_PATH = Path(".state/state.json")
DASH_PATH = Path("docs/data/latest.json")

CAPITAL_BASE = float(os.getenv("CAPITAL_BASE", "38901078.37"))
DAYS_IN_YEAR = 365

# Umbrales por defecto (podés ajustar por ENV si querés)
# BALANCED: rojo <35.5, amarillo 35.5-36.9, verde >=37, rocket >=40
BAL_RED = float(os.getenv("BAL_RED", "35.5"))
BAL_GREEN = float(os.getenv("BAL_GREEN", "37.0"))
BAL_ROCKET = float(os.getenv("BAL_ROCKET", "40.0"))

# Alertas clásicas (si las querés mantener)
THRESH_HIGH = float(os.getenv("THRESH_HIGH", "40.0"))
THRESH_LOW  = float(os.getenv("THRESH_LOW", "35.0"))
DAILY_HOUR  = int(os.getenv("DAILY_HOUR", "10"))

# Multiusuario (modo comercial): opcional
# Se define como SECRET en GitHub: USERS_JSON
# Ej: [{"name":"Pablo","chat_id":"123","capital":38901078.37,"mode":"PRO","profile":"BALANCED","enabled":true}]
USERS_JSON = os.getenv("USERS_JSON", "").strip()


# =========================
# HELPERS
# =========================
def send(chat_id: str, msg: str):
    if not TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=20)
    r.raise_for_status()


def money(n: float) -> str:
    # Formato AR simple: 1.234.567
    s = f"{n:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${s}"


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"last_state": "", "last_daily": ""}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def get_users():
    # Si hay USERS_JSON, usamos multiusuario
    if USERS_JSON:
        try:
            users = json.loads(USERS_JSON)
            # Filtra enabled true por defecto
            out = []
            for u in users:
                if u.get("enabled", True) is False:
                    continue
                out.append({
                    "name": u.get("name", "user"),
                    "chat_id": str(u.get("chat_id", "")).strip(),
                    "capital": float(u.get("capital", CAPITAL_BASE)),
                    "mode": str(u.get("mode", MODE)).upper(),
                    "profile": str(u.get("profile", PROFILE)).upper(),
                })
            return out
        except Exception:
            # fallback a 1 usuario
            pass

    # Fallback: mono usuario
    if DEFAULT_CHAT_ID:
        return [{
            "name": "default",
            "chat_id": DEFAULT_CHAT_ID,
            "capital": CAPITAL_BASE,
            "mode": MODE,
            "profile": PROFILE
        }]
    return []


def fetch_rate_1d():
    """
    Lee la tasa 1D en PESOS desde el HTML.
    Importante: esto depende del sitio. Si cambia el markup, ajustamos el regex.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    html = requests.get(BYMA_HTML_URL, headers=headers, timeout=25).text

    # Regex actual: busca "1 DÍA" + "PESOS" y toma el primer porcentaje luego
    m = re.search(r"1\s*D[IÍ]A.*?PESOS.*?(\d{1,2}[.,]\d{1,2})\s*%", html, re.I | re.S)
    if not m:
        return None

    return float(m.group(1).replace(",", "."))


def eval_balanced(rate: float):
    """
    Devuelve estado, acción y texto para perfil BALANCED.
    """
    if rate is None:
        return {
            "status": "NO_DATA",
            "label": "⚠️ Sin datos",
            "action": "—",
            "explain": "No se pudo leer la tasa desde la fuente."
        }

    if rate >= BAL_ROCKET:
        return {
            "status": "ROCKET",
            "label": "🚀 OPORTUNIDAD FUERTE",
            "action": "Caucionar",
            "explain": f"Tasa excepcional (≥ {BAL_ROCKET:.2f}%)."
        }

    if rate >= BAL_GREEN:
        return {
            "status": "GREEN",
            "label": "🟢 CONVIENE CAUCIONAR",
            "action": "Caucionar",
            "explain": f"Tasa por encima del umbral balanceado (≥ {BAL_GREEN:.2f}%)."
        }

    if rate >= BAL_RED:
        return {
            "status": "YELLOW",
            "label": "🟡 ESPERAR",
            "action": "Esperar",
            "explain": f"Tasa en zona media (≥ {BAL_RED:.2f}% y < {BAL_GREEN:.2f}%)."
        }

    return {
        "status": "RED",
        "label": "🔴 NO CONVIENE",
        "action": "No caucionar",
        "explain": f"Tasa deprimida (< {BAL_RED:.2f}%)."
    }


def write_dashboard(now_iso: str, rate: float, source: str, note: str, summary: dict, users: list):
    DASH_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": now_iso,
        "source": source,
        "rate_1d": rate,
        "profile": "BALANCED",
        "thresholds": {
            "red_lt": BAL_RED,
            "green_gte": BAL_GREEN,
            "rocket_gte": BAL_ROCKET
        },
        "status": summary.get("status"),
        "status_label": summary.get("label"),
        "action": summary.get("action"),
        "explain": summary.get("explain"),
        "note": note,
        "users_enabled": len(users)
    }
    DASH_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================
# MAIN
# =========================
def main():
    now = datetime.now(TZ)
    now_iso = now.isoformat(timespec="seconds")

    users = get_users()
    st = load_state()

    # 1) Fetch
    rate = None
    try:
        rate = fetch_rate_1d()
    except Exception:
        rate = None

    # 2) Dashboard summary (BALANCED)
    summary = eval_balanced(rate)
    source = "BYMA"
    note = "Perfil BALANCED. Herramienta informativa (no asesoramiento financiero)."

    # Escribimos dashboard SIEMPRE (aun con error), para ver fallas rápido
    write_dashboard(now_iso, rate, source, note, summary, users)

    # 3) Alerts (por usuario)
    # Si no hay usuarios, no hay a quién avisar.
    if not users:
        return

    # Error reading rate -> avisar 1 vez (a todos)
    if rate is None:
        if st.get("last_state") != "ERR":
            for u in users:
                if u["mode"] == "DEMO":
                    continue
                send(u["chat_id"], "⚠️ No pude leer la tasa de Caución 1D (fuente).")
            st["last_state"] = "ERR"
            save_state(st)
        return

    # Ingreso estimado diario por usuario (informativo)
    # (No lo mandamos siempre para no spamear, pero queda listo para mensajes premium)
    # daily_income = capital * (rate/100) / 365

    # Estado por umbrales "clásicos" (alto/bajo)
    state = "MID"
    if rate >= THRESH_HIGH:
        state = "HIGH"
    if rate <= THRESH_LOW:
        state = "LOW"

    # Cambio de estado -> alerta
    if state != st.get("last_state"):
        for u in users:
            if u["mode"] == "DEMO":
                # DEMO: limitamos alertas
                continue

            if state == "HIGH":
                send(u["chat_id"], f"🟢 OPORTUNIDAD CAUCIÓN 1D\nTasa: {rate:.2f}%")
            elif state == "LOW":
                send(u["chat_id"], f"⚠️ WARNING CAUCIÓN 1D\nTasa: {rate:.2f}%\n👉 Evaluar alternativas")
        st["last_state"] = state

    # Resumen diario (una vez por día)
    today = now.strftime("%Y-%m-%d")
    if now.hour == DAILY_HOUR and st.get("last_daily") != today:
        # En DEMO, podés mandar igual 1 resumen
        for u in users:
            send(u["chat_id"], f"📊 Resumen diario Caución 1D\nTasa: {rate:.2f}%\nEstado: {summary['label']}")
        st["last_daily"] = today

    save_state(st)


if __name__ == "__main__":
    main()
