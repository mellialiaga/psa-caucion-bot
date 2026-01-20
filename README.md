# 🧾 PSA Caución Bot
Alertas inteligentes para operar cauciones en pesos sin mirar la pantalla.

👉 Acceso, demo y pricing por WhatsApp  
📲 https://wa.me/5493517623486?text=Hola%20Pablo,%20vi%20el%20PSA%20Caución%20Bot%20y%20me%20interesa%20recibir%20información%20sobre%20el%20acceso%20al%20modo%20PRO,%20pricing%20y%20demo.%20Gracias.

---

**PSA Caución Bot** es un bot de Telegram que monitorea automáticamente tasas de caución (1D y 7D) y te avisa **solo cuando pasa algo que vale la pena**. Incluye un **dashboard simple** para ver la última tasa detectada y estado general.

---

## ✅ Qué hace

- 🔔 Alertas por cambios de nivel (baja / buena / rocket)
- 📌 Detecta oportunidades cuando **7D paga mejor que 1D**
- 📊 Resumen diario con estimación de ingreso (según capital)
- 👥 Multi-usuario (ideal si lo ofrecés como servicio)
- 🌐 Dashboard web (GitHub Pages)

---

## ⚙️ Requisitos

- GitHub repo (este)
- GitHub Actions habilitado
- Bot de Telegram (token)
- Chat ID (o lista multiusuario)

---

## 🚀 Instalación rápida (GitHub Actions)

### 1) Crear Secrets
Repo → **Settings** → **Secrets and variables** → **Actions** → **Secrets**

Crear:
- `TG_BOT_TOKEN`
- `TG_CHAT_ID` *(si es single user)*  
- `USERS_JSON` *(si es multiusuario, opcional)*

Ejemplo `USERS_JSON`:
```json
[
  {"name":"Pablo","chat_id":"123456789","capital":38901078.37},
  {"name":"Javier","chat_id":"987654321","capital":2000000}
]
