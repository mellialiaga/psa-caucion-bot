# PSA Caución Bot

> Monitoreo automatizado de tasas de caución BYMA con alertas inteligentes vía Telegram y dashboard en tiempo real.

[![Workflow](https://github.com/mellialiaga/psa-caucion-bot/actions/workflows/update-dashboard.yml/badge.svg)](https://github.com/mellialiaga/psa-caucion-bot/actions/workflows/update-dashboard.yml)
[![Dashboard](https://img.shields.io/badge/dashboard-live-brightgreen)](https://mellialiaga.github.io/psa-caucion-bot/)
[![Python](https://img.shields.io/badge/python-3.11-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

---

## ¿Qué es?

**PSA Caución Bot** es un sistema serverless que monitorea las tasas de caución a 1 y 7 días de BYMA cada 10 minutos durante el horario de mercado, clasifica la oportunidad por bandas percentílicas y envía alertas a Telegram **sólo cuando hay algo accionable**. Sin suscripciones, sin infraestructura propia: corre 100% en GitHub Actions y publica el dashboard en GitHub Pages.

---

## Características

| Funcionalidad | Detalle |
|---|---|
| **Scraping BYMA** | POST autenticado a la API pública de BYMA con reintentos automáticos (3 intentos, 5 s entre intentos) |
| **Clasificación por bandas** | Percentiles p40 / p60 / p75 sobre ventana de 60 días: `BAJA`, `ACEPTABLE`, `BUENA`, `EXCELENTE` |
| **Alertas inteligentes** | Notifica en cambio de banda siempre; entre cambios, respeta dedup de 30 min |
| **Spread 7D–1D** | Detecta automáticamente cuando el plazo largo supera el umbral configurable |
| **Multi-usuario** | Soporta lista de destinatarios con capital individual para estimar renta diaria |
| **Histórico append-only** | CSV con todas las lecturas; base para los percentiles y el dashboard |
| **Dashboard web** | GitHub Pages con series de tiempo, KPIs y percentiles actualizados en cada run |

---

## Arquitectura

```
GitHub Actions (cron: cada 10 min, lun–vie 11:00–17:00 AR)
        │
        ▼
caucion_alerta.py
        │
        ├── make_byma_session()                   # Inicializa sesión HTTP con cookies
        ├── fetch_byma_cauciones_with_retry()     # Scraping con reintentos
        ├── parse_rates()                         # Extrae TNA 1D y 7D
        ├── append_history()                      # Escribe docs/data/history.csv
        ├── build_dashboard()                     # Genera docs/data/dashboard.json
        │       └── compute_percentiles() → classify_band()
        └── should_notify() → send_telegram()
                │
                ▼
        Alertas Telegram + commit de datos → GitHub Pages
```

---

## Setup

### Prerrequisitos

- Cuenta GitHub con Actions habilitado
- Bot de Telegram (`@BotFather` → `/newbot`) y el `chat_id` de cada destinatario

### 1. Fork o clone

```bash
git clone https://github.com/mellialiaga/psa-caucion-bot.git
cd psa-caucion-bot
```

### 2. Configurar Secrets

En el repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Descripción |
|---|---|
| `TG_BOT_TOKEN` | Token del bot de Telegram |
| `TG_CHAT_ID` | Chat ID del destinatario (modo single-user) |
| `USERS_JSON` | Lista JSON de usuarios (modo multi-user, ver abajo) |

**Modo multi-usuario** — formato de `USERS_JSON`:

```json
[
  { "name": "Pablo",  "chat_id": "123456789", "capital": 5000000 },
  { "name": "Javier", "chat_id": "987654321", "capital": 2000000 }
]
```

> Si `USERS_JSON` está definido, tiene precedencia sobre `TG_CHAT_ID`.

### 3. Habilitar GitHub Pages

**Settings → Pages → Source: Deploy from a branch → Branch: `main` / `docs`**

El dashboard queda en `https://<usuario>.github.io/<repo>/`.

---

## Variables de entorno opcionales

| Variable | Default | Descripción |
|---|---|---|
| `PCTL_WINDOW_DAYS` | `60` | Días de historial para calcular percentiles |
| `MIN_POINTS_FOR_PCTLS` | `20` | Mínimo de puntos para activar percentiles |
| `LOOKBACK_DAYS` | `180` | Ventana de datos mostrada en el dashboard |
| `SPREAD_ALERT_MIN` | `0.50` | Umbral de spread 7D–1D para destacar en la alerta |
| `NOTIFY_ON_BAND_CHANGE_ONLY` | `1` | `1` = notifica sólo en cambio de banda; `0` = siempre |
| `DEDUP_MINUTES` | `30` | Minutos de silencio entre notificaciones (sin cambio de banda) |

---

## Ejemplo de alerta Telegram

```
PSA Caucion Bot
Hora: 14:30 hs AR

[BUENA] 1D: 38.50% TNA
7D: 40.10% TNA
Spread 7D-1D: +1.60%

Percentiles 60d (n=87):
   p40=35.20%  p60=38.00%  p75=41.50%

Renta diaria est.: $5,274
```

---

## Estructura del repositorio

```
psa-caucion-bot/
├── caucion_alerta.py               # Script principal
├── requirements.txt                # requests>=2.31.0
├── .github/
│   └── workflows/
│       └── update-dashboard.yml   # Cron + commit de datos
└── docs/
    ├── index.html                  # Dashboard (GitHub Pages)
    └── data/
        ├── dashboard.json          # Generado en cada run
        ├── history.csv             # Histórico append-only
        └── state.json              # Estado de última notificación
```

---

## Dashboard

**[→ Ver dashboard en vivo](https://mellialiaga.github.io/psa-caucion-bot/)**

Muestra TNA 1D / 7D actual, banda de mercado, percentiles del período y series históricas interactivas. Se actualiza automáticamente con cada ejecución del workflow.

---

## Licencia

MIT — libre uso, modificación y distribución con atribución.

---

> Desarrollado por [@mellialiaga](https://github.com/mellialiaga). Las tasas mostradas provienen de la API pública de BYMA y son de carácter informativo. No constituyen asesoramiento financiero.
