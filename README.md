# EMA Rentals — Filtro de leads multicanal (Vella)

Bot conversacional **formal y sin emojis** (motor Bell + cinturón de robustez, molde
`inmobiliaria-leads`) que atiende **WhatsApp, Instagram y Messenger**, **califica** al prospecto
de renta de muebles y, cuando es un **buen lead** (ticket + volumen + plazo largo), **avisa a los
administradores** para que un **asesor humano** retome. No agenda, no cotiza, no maneja finanzas.

> EMA Rentals: renta de muebles, línea blanca y electrónica. Monterrey + todo México. Planes 3–24 meses.
> Cambia la identidad del asistente en `app/ema_config.py` antes de producción.

## Qué hace (y qué NO)
- **Sí:** contesta los 3 canales 24/7 (texto y notas de voz), califica al lead, alerta al admin
  por WhatsApp + panel cuando es buen lead, cede la conversación al humano (auto-pausa), bandeja
  unificada + kanban de leads en el panel.
- **No:** agendar, cotizar precios en el chat, cobrar, reportes financieros, CRM pesado, round-robin.

## Arrancar (dev)
```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.example .env      # pon ADMIN_PASSWORD y OPENAI_API_KEY
uvicorn app.main:app --reload
```
Panel: http://localhost:8000 · API docs: http://localhost:8000/docs
En dev, sin `KAPSO_API_KEY` los envíos son no-op logueados y la firma del webhook se acepta con warning.

## Pruebas
```bash
python -m pytest tests/ -q      # 25 tests
```

## Canales
- **WhatsApp** → Kapso (`/webhook`, firma HMAC).
- **Instagram + Messenger** → Sinch Conversation API (`/webhook/sinch`, firma). Mismo cerebro y bandeja.

## Qué hace cada pieza
| Capa | Archivo | Qué |
|---|---|---|
| Bot | `app/services/bot/ai.py` | tool-calling (gpt-4o-mini): `capturar_lead` + `alertar_asesor`, loop 4 rondas |
| Bot | `app/services/bot/handler.py` | orquesta: registra lead, coexistencia, fact_guard, alerta al calificar |
| Bot | `app/services/bot/leads.py` | CRUD + **guarda de "buen lead"** (ticket/volumen/plazo) + score |
| Bot | `app/services/bot/prompt.py` | system prompt formal, sin emojis, no-precio-en-frío |
| Bot | `app/services/bot/guards.py` | `fact_guard` anti-cifra inventada |
| Alerta | `app/services/notificaciones.py` | WhatsApp (plantilla) → texto → email → log |
| Canal | `app/routers/webhook.py` | webhook Kapso + HMAC + coexistencia (eco saliente) |
| Canal | `app/routers/sinch_webhook.py` | webhook Sinch (Instagram + Messenger) |
| Panel | `app/routers/{leads,conversaciones,auth,usuarios}.py` | kanban, bandeja, login, usuarios |
| Recuperación | `app/services/recovery.py` | motor auto **APAGADO** (recovery_enabled=false) |

## El corazón — clasificación de "buen lead"
El bot conversa y llama `capturar_lead`; una **guarda determinista** (`app/services/bot/leads.py`)
solo marca `calificado` (→ alerta) si hay señales reales:
1. **Ticket** — segmento `airbnb`/`corporativo`, o necesidad `paquete`/`casa_completa`/`oficina_completa`.
2. **Volumen** — cuánto necesita.
3. **Futuro** — plazo ≥ 12 meses.
\+ mínimos (necesidad + plazo + fecha/zona) para no alertar a un curioso.
Los umbrales/pesos viven en `app/services/bot/leads.py` y `app/ema_config.py` (fáciles de afinar).

**Migración inline:** `app/main.py::_ensure_columns()` agrega en el arranque cualquier columna nueva del
modelo que falte (SQLite y Postgres). En prod, Alembic (`0001_baseline`).

## Antes de producción (bloqueantes)
1. **Identidad** del asistente en `app/ema_config.py`.
2. **Kapso:** alta del número + plantilla `ema_alerta_lead` **aprobada en Meta**.
3. **Sinch:** cuenta con Instagram (@emarentalsmty) + página de Messenger conectadas y firmando.
4. `ENV=prod` + `SECRET_KEY`, `KAPSO_WEBHOOK_SECRET`, `ADMIN_PASSWORD`, `DATABASE_URL` (Postgres),
   `OPENAI_API_KEY`, `ALERTA_ADMIN_TELEFONOS`. La puerta `validar_config()` **aborta el arranque** si falta.

## Apagado en v1 (diseñado, no cableado)
Round-robin a varios asesores · recuperación automática de leads fríos · cotización/catálogo con
precios en el chat · atribución por campaña. Ver `arquitectura.md`.
