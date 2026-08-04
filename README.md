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
| Bot | `app/services/bot/handler.py` | orquesta: registra lead, coexistencia, fact_guard, cede al asesor al terminar |
| Bot | `app/services/bot/leads.py` | CRUD + **cuestionario_completo** + clasificación + score de prioridad |
| Bot | `app/services/bot/prompt.py` | system prompt formal, sin emojis, no-precio-en-frío |
| Bot | `app/services/bot/guards.py` | `fact_guard` anti-cifra inventada |
| **Fases** | `app/routers/fases.py` | **el centro de mando**: toggles, destinatarios, mensaje de cierre |
| **Fases** | `app/services/fases_acciones.py` | ejecuta lo que la fase manda al entrar un lead |
| Alerta | `app/services/notificaciones.py` | a los destinatarios de la fase: plantilla → texto → email → log |
| Visibilidad | `app/services/visibilidad.py` | filtro único: leads ocultos y contactos "no es lead" |
| Contexto | `app/services/contexto.py` | separa **datos** (información) de **reglas** (comportamiento) |
| Canal | `app/routers/webhook.py` | webhook Kapso + HMAC + coexistencia (eco saliente) |
| Canal | `app/routers/sinch_webhook.py` | webhook Sinch (Instagram + Messenger) |
| Panel | `app/routers/{leads,conversaciones,auth,usuarios}.py` | kanban, bandeja, historial, login, usuarios |
| Recuperación | `app/services/recovery.py` | selección por fase, pero motor **APAGADO** (sin scheduler) |

## El corazón — el cuestionario manda
Documentado a detalle en **[`flow-clasificacion.md`](flow-clasificacion.md)**. En corto:

El bot hace dos preguntas (tipo de propiedad + su dato ligado → tiempo de renta) y **nada pasa
hasta terminarlas**: ni clasificar, ni notificar, ni apagar el bot. Lo decide
`leads.cuestionario_completo()`. La única excepción es que el prospecto pida hablar con una
persona: ahí se escala igual, marcado como incompleto.

Clasificación (reglas de EMA, deterministas): casa siempre · departamento con 2+ recámaras ·
oficina de 100 m²+ o 20 personas+. Lo demás cae en Low Priority.

**Qué pasa después lo decide la FASE**, no el bot: cada columna del Kanban tiene sus toggles de
Notificación y Recuperación, sus destinatarios y su mensaje de cierre, todo editable desde el panel.

El **score (0-100)** es solo para priorizar dentro de una columna — no dispara nada. Se reparte
45 tamaño / 35 plazo / 20 tipo; las tablas están al inicio de `app/services/bot/leads.py` y el
panel las muestra en el icono "i" de cada fase, leídas de esas mismas constantes.

## Contexto del bot — qué sí y qué no
La pantalla de Contexto acepta dos tipos: **dato** (información que el bot usa para responder) y
**regla** (cómo debe comportarse, entra con las reglas obligatorias del prompt). Lo que **no** va
ahí son los mensajes que dependen de la clasificación: eso necesita ser determinista y vive en el
**mensaje de cierre de cada fase**, enviado por código.

## Historial y contactos que no son leads
`Historial` lista a todas las personas que han escrito, incluidas las que se quitaron del tablero.
Nada se borra nunca (política del proyecto: sin DELETE físico).
- **Ocultar** saca un lead del Kanban, Bandeja, Métricas y Resumen. Reversible.
- **"No es lead"** marca el **teléfono** de forma permanente: el bot deja de contestarle para
  siempre, aunque escriba de nuevo. Su conversación se sigue guardando.

**Migración inline:** `app/main.py::_ensure_columns()` agrega en el arranque cualquier columna nueva del
modelo que falte (SQLite y Postgres). En prod, Alembic (`0001_baseline`).

## Antes de producción (bloqueantes)
1. **Identidad** del asistente en `app/ema_config.py`.
2. **Kapso:** alta del número + plantilla `ema_alerta_lead` **aprobada en Meta**.
3. **Sinch:** cuenta con Instagram (@emarentalsmty) + página de Messenger conectadas y firmando.
4. `ENV=prod` + `SECRET_KEY`, `KAPSO_WEBHOOK_SECRET`, `ADMIN_PASSWORD`, `DATABASE_URL` (Postgres),
   `OPENAI_API_KEY`, `ALERTA_ADMIN_TELEFONOS`. La puerta `validar_config()` **aborta el arranque** si falta.
   Los teléfonos de `ALERTA_ADMIN_TELEFONOS` se migran solos al directorio de contactos en el
   primer arranque; de ahí en adelante los destinatarios se administran por fase desde el panel.

## Apagado en v1 (diseñado, no cableado)
Round-robin a varios asesores · cotización/catálogo con precios en el chat · atribución por campaña.
Ver `arquitectura.md`.

**Recuperación de leads:** el toggle por fase ya guarda a quién recuperar y `recovery._elegibles`
lo lee, pero el motor **no manda nada todavía**. Faltan tres piezas: no hay scheduler (el
`_recovery_loop` que menciona `main.py` nunca se escribió), las plantillas `inmo_recuperacion_*`
son del proyecto inmobiliario y EMA necesita las suyas aprobadas en Meta, y `recovery_min_score`
se expone en el router pero nunca se lee.
