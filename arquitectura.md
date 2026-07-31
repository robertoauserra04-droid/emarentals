# Arquitectura — EMA Rentals (`emma-rentals`) · Fase 2

> Fase 2 de `/proyecto-nuevo`. El **cómo técnico**, ya con el `spec.md` aprobado.
> Read-only: este doc NO construye código todavía. Al final se pide OK para Fase 3.
>
> **Regla de oro del proyecto:** se **clona `inmobiliaria-leads`** (que ya es el motor Bell G2 + cinturón de
> robustez + pipeline de leads + **multicanal WhatsApp/Instagram/Messenger ya cableado**) y se le hacen
> **tres cambios**: (1) dominio muebles en vez de inmuebles, (2) **alerta simple al admin** en vez de
> round-robin, (3) **tono formal sin emojis**. Todo lo demás es recorte, no invención.

---

## 0. Hallazgo clave (verificado en código)

`inmobiliaria-leads` **ya tiene el multicanal construido y probado**:
`app/routers/sinch_webhook.py` normaliza Instagram y Messenger (Sinch Conversation API), transcribe notas de
voz, y dispara **el mismo `handler.handle_inbound`** que WhatsApp — los 3 canales comparten cerebro,
`Conversation` y bandeja. **Por lo tanto el multicanal para Emma es reuso directo, no trabajo nuevo.**
El proyecto se vuelve **~19 REUSAR + 0 NUEVAS de fondo** → solo adaptación de dominio + recorte de lo que
Emma no quiere. **Proyecto CHICO.**

### Mapa de reuso real (archivos leídos en `inmobiliaria-leads`)
| Pieza | Archivo fuente | Qué se hace para Emma |
|---|---|---|
| Multicanal IG/Messenger | `app/routers/sinch_webhook.py` + `app/services/sinch.py` | **reuso directo** (3 canales) |
| Webhook WhatsApp | `app/routers/webhook.py` (HMAC Kapso) | reuso directo |
| Motor IA G2 | `app/services/bot/ai.py` (loop 4 rondas, `gpt-4o-mini`) | reuso; cambian tools |
| Orquestador | `app/services/bot/handler.py` (`handle_inbound`) | reuso; quitar agendar/ficha |
| Guard anti-cifra | `app/services/bot/guards.py` (`fact_guard`) | reuso; deflexión muebles |
| CRUD lead + **guarda de etapa** | `app/services/bot/leads.py` (`validar_etapa`, `recompute_score_calif`, `apply_capturar_lead`) | **adaptar campos + umbral "buen lead"** |
| Modelo lead | `app/models/lead.py` (`InmoLead`) | adaptar campos a muebles |
| Prompt | `app/services/bot/prompt.py` | reescribir: formal, sin emojis, muebles |
| Pipeline/kanban | `app/routers/leads.py` + `marketing` | reuso; recortar |
| Bandeja conversaciones | `app/routers/conversaciones.py` + `app/models/messaging.py` | reuso directo |
| Recuperación (apagada) | `app/services/recovery.py` | reuso, **apagado** en BD |
| Auth/roles | `app/routers/auth.py` | reuso (admin/asesor) |
| Seguridad | `app/security/webhook.py` | reuso (HMAC Kapso + Sinch) |
| **Alerta al admin** | patrón `psicologia/bot/handler._alertar_psicologa` + `services/notificaciones.enviar` | **portar** (reemplaza `asignacion.py` RR) |
| Auto-pausa por humano | patrón `aseguradora`/`psicologia` (`origin=business_app`) | portar |

### Lo que se RECORTA del molde (Emma no lo quiere)
`app/routers/finanzas.py`, `reportes.py`, `metrics.py`, `propiedades.py`, `visitas.py`, `public_agenda.py`,
`demo.py`, `asesores.py`, `app/services/bot/asignacion.py` (round-robin), `app/services/bot/inmuebles.py`,
`app/models/gasto.py`, `propiedad.py`, `visita.py`, `campaign.py`, `asesor.py`. Y las tools
`agendar_visita` / `enviar_ficha` del bot. **Sin finanzas, sin reportes, sin agenda, sin cartera.**

---

## 1. Arquitectura base del proyecto

**Stack:** FastAPI + SQLAlchemy + **Alembic (día 1)** + Postgres (prod) / SQLite (dev) + **Kapso** (WhatsApp)
+ **Sinch** (Instagram/Messenger) + OpenAI `gpt-4o-mini`. Una sola arquitectura por capas (heredada del
molde, ya limpia):

```
app/
  config.py            # settings (env), SIN defaults inseguros
  database.py          # engine + SessionLocal + Base
  main.py              # FastAPI + montado de routers (SIN scheduler de recovery activo)
  ema_config.py        # identidad EMA Rentals: EMPRESA, BOT (tono formal), catálogo de categorías
  models/
    lead.py            # EmaLead (adaptado a muebles)
    messaging.py       # Conversation + ChatMessage (3 canales) — reuso directo
    user.py            # admin / asesor
  routers/
    webhook.py         # WhatsApp (Kapso, HMAC)
    sinch_webhook.py   # Instagram + Messenger (Sinch, firma) — reuso directo
    conversaciones.py  # bandeja unificada 3 canales + responder (=auto-pausa)
    leads.py           # kanban/lista de leads calificados
    auth.py            # login admin/asesor
  services/
    bot/               # ai.py · handler.py · guards.py · leads.py · prompt.py
    kapso.py           # cliente WhatsApp (envío + plantillas)
    sinch.py           # cliente IG/Messenger (envío + chunking 950 + firma)
    messaging_out.py   # envío saliente agnóstico por canal
    notificaciones.py  # alerta al admin (plantilla Kapso → texto → email → log)  [NUEVO/portado]
    transcription.py   # notas de voz
    recovery.py        # presente pero APAGADO (flag en BD)
  security/            # HMAC (Kapso + Sinch), rate-limit, headers
alembic/               # migraciones reales desde el commit 1
frontend/              # panel mínimo: bandeja + kanban (sin finanzas/reportes)
```

**Seguridad desde el día 1:**
- **HMAC / firma verificada en AMBOS webhooks** (Kapso `webhook.py` + Sinch `sinch.verify_signature`).
- **Rate-limit** en webhooks + headers de seguridad.
- Sin `admin123` / `CORS *` / `VERIFY_SIGNATURE=false`; secretos por env; fail-fast si falta uno crítico.
- **SDK OpenAI pineado** + **`gpt-4o-mini`** fijo por env. Lockfile.

---

## 2. Canales (A1/A3/A4) — reuso directo, los 3 habilitados

- **WhatsApp:** `routers/webhook.py` (Kapso, HMAC) sobre el número +52 81 8029 0428.
- **Instagram + Messenger:** `routers/sinch_webhook.py` (Sinch Conversation API). Normaliza el canal
  (`_CANAL`: instagram/messenger), resuelve la **identidad** (IG/Messenger usan PSID, no teléfono),
  transcribe audio, y llama al **mismo** `handle_inbound`. Salida agnóstica por canal con **chunking 950**
  (Sinch corta mensajes largos) en `services/sinch.py`.
- **Bandeja unificada:** los 3 canales escriben en `Conversation` + `ChatMessage` con su `channel`; el panel
  los muestra juntos.
- **Caveat conocido (no bloquea v1):** por IG/Messenger, Sinch **no entrega el referral del anuncio** → esos
  leads entran sin origen de campaña. Como Emma **no** hace atribución en v1, no afecta.

---

## 3. Motor conversacional (B1/B2) — reuso, tools recortadas

**`services/bot/ai.py`** — reuso del loop multi-ronda (`_MAX_RONDAS=4`, `tool_choice="auto"`, `gpt-4o-mini`,
timeout 20). **Tools v1 (solo dos):**
- `capturar_lead` — adaptada al dominio muebles (§4).
- `alertar_asesor` — es el `escalar_a_asesor` de Bell, renombrado; **no hace round-robin**: dispara la
  **alerta simple al admin** (§6).
- **Se ELIMINAN:** `agendar_visita`, `enviar_ficha`, y cualquier tool de Vella. El bot **no agenda, no
  cotiza, no manda fichas**.

**`services/bot/handler.py`** — reuso de `handle_inbound`: registra el lead antes de responder (entra al
kanban aunque el bot esté apagado), toggles de coexistencia (global/por-conversación), `fact_guard` sobre el
borrador, `_split_bubbles`. Se quita el bloque de agendar/ficha.

---

## 4. Clasificación del lead (B6) — el corazón, adaptado a "buen lead"

**Definición de buen lead (tu criterio, 2026-07-30):** un buen lead se distingue por **(a) buen ticket
final**, **(b) quiere mucho (volumen)** y **(c) quiere a futuro (plazo largo)**. Eso se baja a campos +
score + guarda determinista.

**Modelo `EmaLead`** (clon de `InmoLead`, campos de dominio cambiados):

| Campo inmobiliaria | Campo EMA Rentals | Valores | Señal |
|---|---|---|---|
| `tipo_inmueble` | `segmento` | residencial / oficina / **airbnb** / **corporativo** | ticket (airbnb/corporativo pesan más) |
| `operacion` | `necesidad` | pieza_suelta / paquete / casa_completa / oficina_completa | **volumen** ("quiere mucho") |
| — | `plazo_meses` | 3–24 | **futuro** ("quiere a futuro") |
| — | `fecha_entrega` | texto (ya / 1-4 sem / >1 mes) | urgencia real |
| `zona` | `zona` | Monterrey / resto de México | logística |
| `presupuesto` | `presupuesto` | rango declarado (texto, sin parseo duro) | ticket |
| `nivel_interes`, `estado`, `resumen`, `que_pregunto`, `motivo_perdida` | **igual** | | |

**Pipeline (etapas):** `nuevo → interesado → calificado → asignado → ganado/perdido`. El bot mueve hasta
**`calificado`** (= "buen lead", dispara alerta); de ahí lo maneja el asesor.

**Guarda de etapa determinista** (adaptación de `validar_etapa`) — el "casco" que evita que el LLM
sobrecalifique al curioso. Para saltar a **`calificado`** se exigen **señales reales de buen lead**:

```
def validar_etapa(lead, estado_propuesto, args) -> str | None:
    if estado_propuesto not in ESTADOS_VALIDOS:
        return None
    if estado_propuesto == "calificado":
        necesidad = args.get("necesidad") or lead.necesidad
        plazo     = args.get("plazo_meses") or lead.plazo_meses
        # señal de ticket/futuro: o segmento de alto valor, o plazo largo, o volumen alto
        ticket_ok = (
            (args.get("segmento") or lead.segmento) in ("airbnb", "corporativo")
            or (necesidad in ("paquete", "casa_completa", "oficina_completa"))
            or (plazo and int(plazo) >= 12)   # "quiere a futuro"
        )
        # mínimos para no alertar a un curioso: necesidad concreta + plazo + (fecha o zona)
        base_ok = necesidad and plazo and ((args.get("fecha_entrega") or lead.fecha_entrega)
                                            or (args.get("zona") or lead.zona))
        if not (base_ok and ticket_ok):
            return "interesado"   # tibio: no dispara alerta
    return estado_propuesto
```

**Score comercial `recompute_score_calif`** (0–100, visible en el kanban), reponderado a los 3 ejes:
```
segmento:   corporativo +30 · airbnb +25 · oficina +15 · residencial +10     # ticket
necesidad:  casa/oficina_completa +25 · paquete +15 · pieza_suelta +5        # volumen
plazo:      >=12m +20 · 6-11m +12 · 3-5m +5                                   # futuro
fecha:      ya +15 · 1-4sem +10 · >1mes +3                                     # urgencia
zona:       Monterrey +5 (entrega directa)
+ piso por etapa: interesado 35 · calificado 65 · ganado 100
```
> **Umbrales afinables:** dejaste dicho que darás más info para distinguir un buen lead. Los pesos y el gate
> de `ticket_ok` quedan en `ema_config.py` (constantes), fáciles de ajustar cuando definas, por ejemplo, el
> monto/volumen mínimo que para EMA cuenta como "buen ticket". Arrancamos con esta heurística.

**`apply_capturar_lead`** — reuso con semántica COALESCE (vacío no sobreescribe) + la guarda + recálculo de
score. Cuando el estado llega a `calificado`, el handler dispara la alerta (§6) una sola vez (idempotente).

---

## 5. System prompt (B3) — formal, SIN emojis, muebles

Reescritura de `prompt.py` desde `ema_config.py`:
- **Identidad:** asesora de **EMA Rentals**. Tono **formal, profesional y cálido**. **CERO emojis** (regla
  dura; se quita el `😊`/`🤝` del molde y el "Máximo un emoji" pasa a "Sin emojis").
- **Qué sabe:** renta de muebles, línea blanca y electrónica; segmentos residencial/oficina/Airbnb/
  corporativo; plazos 3–24 meses; Monterrey + entrega en todo México (grounding desde el catálogo seed, sin
  precios).
- **Su trabajo — calificar** (una pregunta por mensaje, en el orden que fluya): segmento → qué necesita →
  plazo → fecha de entrega → zona. Registra con `capturar_lead` en cuanto lo sepa.
- **Precio — regla dura (no-precio-en-frío):** no da cifras; explica que un asesor le arma la propuesta a la
  medida y sigue calificando. `fact_guard` respalda esto en código.
- **No agenda, no cierra:** cuando el lead califica o pide hablar con alguien, se despide formal y llama
  `alertar_asesor`. Regla de oro: nunca cerrar con un "no" seco.

Ejemplo de saludo (sin emoji): *"Hola, le saluda [nombre] de EMA Rentals. Con gusto le ayudo. ¿Para qué
espacio está buscando amueblar?"*

---

## 6. Alerta al admin + handoff (B5) — reemplaza el round-robin

Cuando el lead pasa a **`calificado`** (o el LLM llama `alertar_asesor`), en vez del `asignacion.py` de
round-robin, un servicio **`services/notificaciones.py`** (portado de `psicologia._alertar_psicologa`)
dispara la alerta:

1. **WhatsApp al teléfono de los admins** por **plantilla Kapso** (fuera de 24h obligatorio), con el
   **resumen** del lead: canal de origen, segmento, necesidad, plazo, fecha, zona, nombre/usuario. Cadena de
   respaldo: **plantilla → texto (si hay ventana 24h) → email (admin@ema-rentals.com) → log**.
2. **Marca en el panel:** el lead aparece como **caliente/calificado** en la bandeja y el kanban.

**Config:** `ALERTA_ADMIN_TELEFONOS` (uno o varios celulares) + `ALERTA_ADMIN_EMAIL` en env. Idempotencia:
se alerta **una vez** por lead al cruzar a `calificado` (bandera `alertado_at`), no en cada mensaje.

**Handoff / auto-pausa (patrón `aseguradora`/`psicologia`):** cuando un **asesor responde a mano** —desde
WhatsApp Business (`origin=business_app` en el eco de Kapso) o desde el panel (en IG/Messenger)— el bot
**se auto-pausa** en esa conversación (`bot_active=false` / bandera `humano:<id>`) y **se reactiva sola a las
48 h**. Así el humano retoma sin que el bot lo pise, en cualquiera de los 3 canales.

---

## 7. Panel mínimo + roles (F1/F2)

- **Auth 2 roles** (reuso): **admin** ve todo (bandeja global + kanban + config); **asesor** ve la bandeja y
  el kanban para retomar leads. (Sin bandeja-por-asesor porque no hay round-robin.)
- **Bandeja de conversaciones:** hilo unificado de los 3 canales; responder a mano = auto-pausa del bot.
- **Kanban/lista de leads:** `nuevo → interesado → calificado → asignado → ganado/perdido` con el resumen de
  calificación y el score. Los `calificado` resaltan como buen lead.
- **Recortado:** nada de finanzas, reportes financieros, propiedades, visitas, métricas pesadas, demos.

---

## 8. Recuperación de leads (E1) — presente, APAGADA

`services/recovery.py` se reusa pero **apagado en BD** (`recovery_enabled="false"`). Se prende cuando Emma lo
pida, dando de alta plantillas propias en Kapso. Arrancar limpio (sin `RecoveryEvent` viejos) para no
duplicar (riesgo conocido de la flota).

---

## 9. Orden de construcción (Fase 3)

**Reuso primero (seguro), adaptación después:**
1. **Base:** clonar `inmobiliaria-leads` → `emma-rentals`; **recortar** los routers/modelos/servicios de §0
   (finanzas, reportes, propiedades, visitas, agenda, demo, asesores, asignacion RR, campaign). Alembic día
   1, hardening, SDK/modelo pineados. Identidad = EMA Rentals (no "Bell", no la agencia inmobiliaria).
2. **Canales:** verificar que WhatsApp (Kapso) + Sinch (IG/Messenger) queden montados y firmando; los 3
   habilitados. (Es reuso, solo config de secretos.)
3. **Dominio muebles:** `EmaLead` con los campos nuevos + tool `capturar_lead` adaptada.
4. **Guarda "buen lead":** `validar_etapa` + `recompute_score_calif` reponderados (ticket/volumen/plazo),
   pesos en `ema_config.py`.
5. **Prompt formal sin emojis** + `fact_guard` con deflexión muebles.
6. **Alerta al admin** (`notificaciones.py` portado de psicología) + auto-pausa por intervención humana en
   los 3 canales.
7. **Panel mínimo** (bandeja + kanban) + roles admin/asesor.
8. **Verificación:** un lead entra por cada canal → el bot califica formal y sin emojis → un curioso se queda
   en `interesado` (no alerta) → un buen lead (ticket/volumen/plazo) pasa a `calificado`, alerta al admin y
   aparece caliente en el panel → si el asesor responde, el bot se calla. Tests como el molde (unit de la
   guarda + e2e del loop).

**Diseñado y apagado (v2):** round-robin a varios asesores (existe en el molde), follow-up automático,
atribución por campaña, cotización/catálogo con precios en chat.

---

## 10. Footguns evitados (checklist)
- [x] Una sola arquitectura por capas (heredada limpia del molde).
- [x] Alembic desde el commit 1.
- [x] G2 **con** cinturón (fact_guard + guarda de etapa determinista) — no Bell crudo.
- [x] SDK OpenAI pineado + `gpt-4o-mini` válido.
- [x] HMAC + rate-limit en **ambos** webhooks (Kapso y Sinch) + sin defaults inseguros.
- [x] Chunking 950 para IG/Messenger (Sinch trunca) — ya en el molde.
- [x] Recuperación apagada y limpia (riesgo de duplicados conocido).
- [x] Identidad = EMA Rentals; **sin emojis** por instrucción del cliente.
- [x] Alerta idempotente (una vez por lead) — no spamea al admin.

---

## 11. Bloqueantes antes de Fase 3 (fuera del código)
1. **Kapso:** alta del número WhatsApp de EMA + plantilla de **alerta al admin** aprobada por Meta.
2. **Sinch:** cuenta con Instagram (@emarentalsmty) + página de Facebook/Messenger conectadas y firmando.
3. **OpenAI:** clave (`gpt-4o-mini`).
4. **Alertas:** teléfono(s) de los admins + correo de respaldo (admin@ema-rentals.com).
5. **Catálogo:** confirmar categorías de muebles para el seed de grounding (sin precios).
6. **Umbral "buen lead":** la info extra que dijiste que darás (monto/volumen mínimo) para afinar los pesos.

---

**Estado:** Fase 2 (arquitectura) — lista para tu OK. Con el OK paso a Fase 3 (construir), reuso primero.
