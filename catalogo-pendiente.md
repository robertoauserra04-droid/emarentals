# Cierre de catálogo — EMA Rentals (`emma-rentals`)

> Pendiente de `/proyecto-nuevo` Fase 3.3: añadir este cliente a las fichas de las funciones usadas.
> EMA Rentals es un **clon de `inmobiliaria-leads`** (mismo motor y arquitectura) con: dominio muebles,
> 3 canales activos, tono formal sin emojis, y **alerta simple al admin** (sin round-robin).

## Añadir `emma-rentals` a `presencia_por_repo` de estas fichas
- **A1** webhook-whatsapp — Kapso + HMAC + coexistencia (eco saliente `business_app`).
- **A3** multicanal — **los 3 canales activos**: WhatsApp (Kapso) + Instagram/Messenger (Sinch).
  Nota: primer cliente que junta el adaptador Sinch con el motor Bell de leads (viene de
  `inmobiliaria-leads` que ya lo traía cableado).
- **A4** multimedia-transcripción — notas de voz por los 3 canales.
- **B1** motor-ia — Bell G2 tool-calling + cinturón de robustez (`gpt-4o-mini`, temp 0.5, formal).
- **B2** comandos-vs-tools — tools mínimas `capturar_lead` + `alertar_asesor`.
- **B3** system-prompt-personalidad — **variante formal SIN emojis** (nueva en el catálogo: la
  mayoría de bots permiten 1 emoji; EMA lo prohíbe). Vale anotarla como variante de tono.
- **B4** memoria-historial.
- **B5** handoff-humano-contexto — **alerta proactiva al admin** (patrón `psicologia._alertar` +
  auto-pausa `aseguradora`), sin bandeja por asesor. Variante "alerta simple, no round-robin".
- **B6** triaje-orientacion — **clasificación de lead con guarda determinista de "buen lead"**
  (ticket + volumen + plazo largo). Variante nueva del criterio de calificación.
- **E2** pipeline-leads — kanban `nuevo→interesado→calificado→asignado→ganado/perdido`.
- **E5** gestion-clientes — ficha de lead ligera + bitácora/notas.
- **D1** base-de-datos, **D2** migraciones (Alembic 0001), **D4** catalogo-seed (categorías de muebles).
- **F1** panel-admin — **panel mínimo**: bandeja unificada 3 canales + kanban. Variante "recortado".
- **F2** auth-login, **F3** seguridad-hardening (HMAC Kapso+Sinch, validar_config), **F4** deploy.
- **E1** followup-leads — presente pero **APAGADO** (recovery_enabled=false).

## Posible ficha/variante NUEVA a documentar
- **Criterio "buen lead" por ticket+volumen+plazo** (guarda determinista en `bot/leads.py`): es un
  refinamiento del B6 que otros verticales de leads (inmobiliaria) podrían adoptar. Candidato a
  volverse variante documentada de B6.

## Estado
- Construido y probado: **25 tests pasan** + smoke de arranque OK (login/pipeline/conversaciones).
- **NO es git todavía** (igual que el molde en su fase inicial).
- Bloqueantes de prod: Kapso (número + plantilla `ema_alerta_lead`), Sinch (IG @emarentalsmty +
  Messenger), OPENAI_API_KEY, teléfonos de alerta. Ver README.
