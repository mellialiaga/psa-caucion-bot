import os, re, json, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

BYMA_HTML_URL = "https://www.portfoliopersonal.com/Cotizaciones/Cauciones"

TOKEN = os.environ["TG_BOT_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

THRESH_HIGH = float(os.getenv("THRESH_HIGH", "40.0"))
THRESH_LOW  = float(os.getenv("THRESH_LOW", "35.0"))
DAILY_HOUR  = int(os.getenv("DAILY_HOUR", "10"))

TZ = ZoneInfo("America/Argentina/Cordoba")
STATE_PATH = Path(".state/state.json")

def send(msg: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=20)
    r.raise_for_status()

def fetch_rate():
    headers = {"User-Agent": "Mozilla/5.0"}
    html = requests.get(BYMA_HTML_URL, headers=headers, timeout=25).text

    # Busca la fila "1 DÍA" + "PESOS" y toma el primer porcentaje que aparece después
    m = re.search(r"1\s*D[IÍ]A.*?PESOS.*?(\d{1,2}[.,]\d{1,2})\s*%", html, re.I | re.S)
    if not m:
        return None

    return float(m.group(1).replace(",", "."))

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"last_state": "", "last_daily": ""}

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")

def main():
    now = datetime.now(TZ)
    st = load_state()

    rate = fetch_rate()
    if rate is None:
        if st["last_state"] != "ERR":
            send("⚠️ No pude leer la tasa de Caución 1D (BMB).")
            st["last_state"] = "ERR"
            save_state(st)
        return

    state = "MID"
    if rate >= THRESH_HIGH: state = "HIGH"
    if rate <= THRESH_LOW:  state = "LOW"

    if state != st["last_state"]:
        if state == "HIGH":
            send(f"🟢 OPORTUNIDAD CAUCIÓN 1D\nTasa: {rate:.2f}%")
        elif state == "LOW":
            send(f"⚠️ WARNING CAUCIÓN 1D\nTasa: {rate:.2f}%\n👉 Evaluar alternativas")
        st["last_state"] = state

    today = now.strftime("%Y-%m-%d")
    if now.hour == DAILY_HOUR and st["last_daily"] != today:
        send(f"📊 Resumen diario Caución 1D\nTasa: {rate:.2f}%")
        st["last_daily"] = today

    save_state(st)

if __name__ == "__main__":
    main()
