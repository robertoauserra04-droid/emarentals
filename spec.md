# Spec — EMA Rentals (nombre de trabajo: `emma-rentals`) · v1

> Fase 1 de `/proyecto-nuevo`. Documento de **comportamiento** (el "qué"), no de código.
>
> **Cliente:** EMA Rentals (ema-rentals.com, IG @emarentalsmty) — **renta de muebles, línea blanca y
> electrónica** (leasing de mobiliario). Sede **Monterrey, N.L.**; entrega en todo México. Plazos 3–24 meses.
> **Segmentos:** residencial, oficinas, **amueblado para Airbnb**, **proyectos corporativos** (torres).
> **Contacto real:** WhatsApp +52 81 8029 0428 · IG @emarentalsmty · admin@ema-rentals.com · L–S 8am–10pm.
>
> **Qué es esta plataforma (alcance deliberadamente chico):** un **filtro de leads multicanal**. El bot
> atiende WhatsApp + Instagram + Messenger, **conversa para calificar** al prospecto, y cuando es un **buen
> lead** dispara una **alerta a los administradores** para que un **asesor humano** retome. **Nada más:**
> sin finanzas, sin cobros, sin agendar, sin CRM pesado, sin reportes financieros.
>
> **Motor (decisión del usuario):** se clona la **superficie conversacional de Bell** (`vella-panel`, **G2
> function-calling**, `gpt-4o-mini`, tool de captura de lead + pipeline `MarketingLead`) con el **cinturón de
> robustez** ya probado en `psicologia` e `inmobiliaria-leads` (`fact_guard` + guarda de etapa determinista).
> **`inmobiliaria-leads` es el molde directo de este proyecto** — mismo objetivo (filtrar leads con un bot
> agradable), misma arquitectura; **la diferencia es el giro (muebles, no inmuebles), los 3 canales activos,
> el tono formal sin emojis, y que aquí NO hay round-robin (alerta simple a admins).**

---

## El corazón (lo que este sistema resuelve)

Emma recibe prospectos por **tres canales** (WhatsApp, Instagram, Messenger) y no todos valen lo mismo:
hay quien "solo pregunta precio" y quien de verdad va a amueblar un depa por un año. El dolor es **separar
al bueno del curioso sin quemar tiempo del equipo**. Por eso:

1. **El bot contesta todo, 24/7, en los 3 canales** — texto y notas de voz (las transcribe).
2. **El bot conversa y califica solo** — de forma formal y agradable saca las señales que definen un buen
   lead (segmento, qué necesita, plazo, fecha, zona) y clasifica al prospecto con una **guarda determinista**
   que evita sobrecalificar al curioso.
3. **Cuando el lead califica, alerta a los administradores** (WhatsApp de Emma + marca en el panel) para que
   **un asesor humano** retome la conversación. **El bot no cierra ni negocia; entrega el lead calentado.**

---

## Alcance v1 (decisiones cerradas 2026-07-30)

- **Canales:** **WhatsApp + Instagram + Messenger, los tres habilitados.** WhatsApp por Kapso;
  IG/Messenger por **Sinch Conversation API** (patrón `colegiolizardi`/`bienesraices`, ya probado).
- **Motor:** superficie conversacional de Bell (G2 tool-calling) + cinturón de robustez (molde
  `inmobiliaria-leads`/`psicologia`). Agradable **pero formal y SIN emojis** (empresa formal).
- **Autonomía del bot: "califica y entrega".** Conversa, califica, y cuando el lead es bueno **alerta y
  cede**. NO agenda, NO cotiza precios en frío, NO negocia, NO cierra. Eso lo hace el humano.
- **Ruteo: alerta simple a administradores** (no round-robin). Se avisa al equipo y el primer asesor
  disponible retoma. (El round-robin queda diseñado y apagado por si crece el equipo.)
- **Alerta de lead bueno:** por **WhatsApp al teléfono de Emma** (plantilla, patrón `psicologia._alertar`)
  **+ notificación en el panel** (el lead aparece marcado como caliente en la bandeja).
- **Panel: mínimo.** Solo **bandeja de conversaciones** (los 3 canales) + **lista/kanban de leads
  calificados**. **Sin** cobros, **sin** reportes financieros, **sin** CRM pesado.
- **Handoff = auto-pausa:** cuando un asesor responde a mano (desde WhatsApp Business o desde el panel),
  el bot **se calla** en esa conversación y la reactiva sola a las 48 h (patrón `aseguradora`/`psicologia`).

---

## El loop (así se usa)

1. **Entra un lead** por WhatsApp, Instagram o Messenger. El bot contesta al instante — texto o nota de voz.
2. **El bot conversa y califica solo:** de forma formal saca **segmento** (casa / oficina / Airbnb /
   corporativo), **qué necesita** (sala, recámara, comedor, línea blanca, casa completa…), **plazo**
   (3–24 meses), **fecha de entrega** deseada y **zona** (Monterrey o resto de México). Llama a la tool de
   captura para clasificar.
3. **Clasifica:** el lead cae en su etapa (`nuevo → interesado → calificado`). El LLM propone la etapa vía
   tool y una **guarda determinista** la valida: para marcar **calificado** se exigen señales mínimas
   (necesidad concreta + plazo definido + fecha/zona) — el que "solo pregunta precio" se queda en
   *interesado* y **no** dispara alerta.
4. **Entrega:** cuando el lead **califica**, se dispara la **alerta a los admins** (WhatsApp + panel) con el
   resumen de lo que quiere. Un **asesor humano** retoma; el bot se auto-pausa en esa conversación.
5. **Recupera:** al que dejó de contestar, el bot le hace **follow-up** (recuperación de leads) para no
   perder lo que las campañas trajeron. (Apagado por default, se prende cuando Emma lo pida.)

---

## Menú de funciones

**Balance: ~17 REUSAR + 1 NUEVA (multicanal ya existe en la flota, aquí se ensambla) → proyecto CHICO.**
Casi todo se toma directo de `inmobiliaria-leads` (que ya hizo este mismo port) + el adaptador Sinch de
`colegiolizardi`. La única pieza "nueva" para el molde de leads es **encender los 3 canales juntos**, que ya
está resuelta en otros verticales.

### 🟢 REUSAR — motor de leads (`inmobiliaria-leads`/`vella-panel`) + multicanal (`colegiolizardi`)
| Cód | Función | Parte de | Ajuste |
|---|---|---|---|
| A1 | Webhook WhatsApp (Kapso) + coexistencia bot/humano | inmobiliaria-leads / vella-panel | igual; firma HMAC Kapso |
| A2 | Envío saliente | vella-panel | igual |
| **A3** | **Multicanal Instagram + Messenger (Sinch)** | **colegiolizardi / bienesraices** | **encender IG+Messenger junto a WhatsApp**; sender agnóstico + chunking 950 |
| A4 | Multimedia + transcripción de audios | psicologia / salones | contestar notas de voz del lead |
| B1 | Motor IA conversacional Bell (G2 tool-calling) + cinturón de robustez | vella-panel + inmobiliaria-leads | formal, sin emojis; `gpt-4o-mini` |
| B2 | Tools (`capturar_lead`, `alertar_asesor`) | inmobiliaria-leads | tools del giro muebles |
| B3 | System prompt EMA Rentals | inmobiliaria-leads / Enrique | **tono formal, sin emojis**, no-precio-en-frío |
| B4 | Memoria / historial por conversación | vella-panel | igual |
| B5 | **Handoff + alerta al admin** (lead caliente) | psicologia (`_alertar`) + aseguradora (auto-pausa) | alerta a WhatsApp de Emma + auto-pausa cuando el asesor responde |
| B6 | **Clasificación del lead** (bot propone etapa + guarda determinista) | inmobiliaria-leads | **el corazón**; señales del giro muebles |
| D1 | Base de datos + ORM | vella-panel / inmobiliaria-leads | igual |
| D2 | Migraciones Alembic (día 1) | inmobiliaria-leads / chips | igual, sin footgun |
| D4 | Catálogo + seed (qué rentan) | Enrique / chips | seed ligero de categorías para grounding (sin precios en chat) |
| E1 | Recuperación / follow-up de leads | vella-panel (Bell) | apagado por default |
| E2 | Pipeline / kanban (`MarketingLead`: nuevo→interesado→calificado→asignado→ganado/perdido) | inmobiliaria-leads | etapas del filtro de leads |
| E5 | Gestión de leads (ficha con contexto) | vella-panel / inmobiliaria-leads | ligera |
| F1 | Panel admin **mínimo** (bandeja 3 canales + kanban de leads) | inmobiliaria-leads / vella-panel | **sin finanzas ni reportes** |
| F2 | Auth login (admin/asesor) | vella-panel / aura | roles simples |
| F3 | Seguridad / hardening | aura / inmobiliaria-leads | rate-limit + HMAC (Kapso y Sinch) + sin defaults inseguros |
| F4 | Deploy Railway + Alembic | chips / inmobiliaria-leads | igual |
| — | `fact_guard` (anti-cifra inventada) | vella-panel (nativo de Bell) | no da precios/plazos inventados → deflecta al asesor |

### 🔴 NUEVA — lo que el molde de leads no traía y aquí sí se necesita
| # | Función | Por qué | Tamaño |
|---|---|---|---|
| 1 | **Ensamble multicanal 3-en-1 (WA+IG+Messenger) en un bot de leads** | `inmobiliaria-leads` es WhatsApp-only; los colegios tienen Sinch pero no el motor Bell de leads. Aquí se **junta** el adaptador Sinch con el motor de captura de lead por primera vez. El código de cada pieza ya existe y está probado; lo nuevo es el ensamble. | chica–media |

### ⚫ Fuera de v1 (diseñado, apagado — sin rehacer nada después)
- **Round-robin a varios asesores** con bandeja por asesor (existe en `inmobiliaria-leads`; se prende si el
  equipo crece).
- **Follow-up automático** de leads fríos (código presente, apagado en BD por default).
- **Cotización / catálogo con precios en el chat** (el bot NO cotiza en v1; deflecta al asesor).
- **Atribución por campaña / anuncio** (Sinch no trae referral en IG/Messenger — limitación conocida).
- **Agendar visita / entrega** desde el bot.

---

## Detalle por función

**Multicanal (A3, REUSAR/ENSAMBLE — la diferencia grande).** WhatsApp entra por **Kapso** (webhook con
firma HMAC, patrón de la flota). Instagram y Messenger entran por **Sinch Conversation API**: webhook
`/webhooks/sinch` → normaliza el canal (distingue IG vs Messenger) → el mismo motor de IA responde → sender
**agnóstico por canal** con **chunking a 950 chars** (Sinch corta mensajes largos). Se copia el adaptador de
`colegiolizardi` (la mejor referencia de multicanal). **Caveat conocido:** por IG/Messenger Sinch **no
entrega el referral del anuncio**, así que esos leads entran sin origen de campaña (no afecta v1 porque no
hacemos atribución). Los tres canales quedan **habilitados** desde el día 1.

**Motor conversacional (B1/B2, REUSAR).** Se clona el bot **Bell** de `inmobiliaria-leads` (G2 tool-calling,
`gpt-4o-mini`): conversa natural, no es un formulario. Loop de tools (máx. 4 rondas) con `capturar_lead` y
`alertar_asesor`. Encima, el **cinturón de robustez** ya probado: `fact_guard` (no inventa cifras) + guarda
de etapa (no sobrecalifica). **Tono formal y sin emojis** por instrucción del cliente (empresa formal).

**System prompt (B3, REUSAR/ajuste).** Personalidad de EMA Rentals: formal, cálida, profesional, **cero
emojis**. Sabe qué rentan (muebles, línea blanca, electrónica; residencial/oficina/Airbnb/corporativo;
plazos 3–24 meses; Monterrey + México) para responder con propiedad, pero **no cotiza precios en frío** —
si preguntan precio antes de calificar, explica que un asesor le pasa la propuesta a la medida y sigue
sacando las señales. Regla dura: **el bot no cierra ni agenda; solo filtra y entrega.**

**Clasificación del lead (B6, REUSAR — el corazón).** Conversando, el bot propone la clasificación vía
`capturar_lead` con las señales del giro:
- **Segmento:** casa / oficina / **Airbnb** / **corporativo** (Airbnb y corporativo pesan más).
- **Necesidad:** pieza suelta vs paquete vs casa/oficina completa.
- **Plazo:** 3–24 meses (plazo definido y largo = mejor señal).
- **Fecha de entrega** deseada (urgencia real vs "solo viendo").
- **Zona:** Monterrey (entrega directa) o resto de México (logística).

Una **guarda determinista en código** valida el salto a **calificado**: exige señales mínimas
(p. ej. **necesidad concreta + plazo definido + (fecha o zona)**). Si no las tiene, el lead se queda en
*interesado* y **no** dispara alerta. Así se filtra al curioso sin que el LLM sobrecalifique.

**Handoff + alerta (B5, REUSAR).** Cuando el lead pasa a **calificado**, se dispara la alerta:
1. **WhatsApp al teléfono de Emma** por plantilla (patrón `psicologia._alertar_psicologa`
   → `notificaciones.enviar`: plantilla Kapso → texto → email de respaldo → log), con resumen del lead
   (canal de origen, segmento, qué necesita, plazo, fecha, zona) y el nombre/usuario del prospecto.
2. **Marca en el panel:** el lead aparece como **caliente** en la bandeja.
Cuando un **asesor responde a mano** (desde WhatsApp Business o desde el panel), el bot **se auto-pausa** en
esa conversación (detecta `origin=business_app` en el eco de Kapso; en IG/Messenger, la respuesta desde el
panel) y la **reactiva sola a las 48 h** (patrón `aseguradora`/`psicologia`). Así el humano retoma sin que el
bot lo pise.

**Pipeline / kanban (E2, REUSAR).** `MarketingLead`: `nuevo → interesado → calificado → asignado →
ganado/perdido`. El bot mueve hasta *calificado*; de ahí lo maneja el asesor en el panel. El kanban es la
vista principal del panel (lista + tablero), sin nada financiero.

**Panel mínimo (F1, REUSAR/recorte).** Dos cosas y nada más:
- **Bandeja de conversaciones** unificada de los 3 canales (ver el hilo, responder a mano = auto-pausa).
- **Kanban/lista de leads** con su clasificación y el resumen de calificación.
Se recorta del panel de `inmobiliaria-leads` todo lo financiero/reportes/CRM pesado.

**Seguridad (F3, REUSAR).** Desde el día 1: rate-limit, verificación de **firma HMAC en ambos webhooks**
(Kapso y Sinch), sin `admin123` / `CORS *` / `VERIFY_SIGNATURE=false`. Dependencias pineadas (un SDK de
OpenAI, `gpt-4o-mini`), Alembic desde el inicio.

**Recuperación de leads (E1, REUSAR, apagado).** El follow-up al lead que dejó de contestar queda
construido pero **apagado en BD**; se prende cuando Emma lo pida (con plantilla aprobada en Kapso).

---

## Lo que este sistema NO hace (por diseño, para que quede claro)
- No cobra, no maneja pagos ni depósitos, no lleva finanzas.
- No agenda entregas ni visitas.
- No cotiza precios en el chat (deflecta al asesor).
- No cierra ni negocia — solo filtra y entrega el lead calentado.
- No tiene reportes financieros ni CRM pesado; el panel es bandeja + kanban de leads y ya.

---

## Pendientes para arrancar (fuera del código)
- Alta del número de WhatsApp en **Kapso** + plantilla de alerta al admin aprobada por Meta.
- Cuenta **Sinch** con Instagram (@emarentalsmty) y la página de Facebook/Messenger conectadas.
- Clave de **OpenAI** (`gpt-4o-mini`).
- Teléfono(s) destino de la **alerta** (celular de los admins) + correo de respaldo (admin@ema-rentals.com).
- Confirmar el catálogo de categorías para el seed de grounding (sin precios).
