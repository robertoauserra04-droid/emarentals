# Actualización 2026-08 — Bot de EMA Rentals

> Documento de trabajo. Explica qué hace hoy el bot, qué va a cambiar, por qué, y qué se va a
> notar. Se puede leer sin conocer el código: cada parte va primero en simple y después en técnico.
>
> **Revisión del 19-ago-2026.** Todo lo que sigue está verificado contra el código de hoy
> (`main` en `4979e5a`), con los números de línea de hoy. Respecto de la versión anterior cambian
> tres cosas: (1) los cuatro defectos que traía quedaron **confirmados uno por uno** contra el
> archivo, no repetidos de un reporte previo; (2) aparecen **dos defectos nuevos** —B-5 y B-6— que
> salieron de revisar la observabilidad que se instaló el 4 de agosto, y **B-5 vale más dinero que
> el cambio de modelo del §5**; (3) se corrige una afirmación falsa del §12: el archivo
> `tests/test_prompt_caching.py` que decía copiar de `colegiolizardi` **no existe en ese repo**.

---

## ⚠ Antes de cualquier otra cosa: aquí no hay bloqueo de seguridad

En esta tanda, `salon-ai` tiene 37 endpoints de negocio sin autenticación y un webhook que no
verifica firma, y por eso su documento arranca con un bloque de alto en rojo. **Este repo no lo
necesita**, y conviene dejarlo escrito para que nadie asuma lo contrario por analogía:

- **Los 11 routers del panel exigen sesión.** Todos los endpoints de `leads`, `conversaciones`,
  `fases`, `contexto`, `config`, `metrics`, `usuarios` y `recovery` declaran
  `Depends(auth.current_user)`; `auth` expone además `solo_dueno` y `requiere_seccion`.
- **Los dos webhooks verifican firma.** Kapso por HMAC-SHA256 en
  [app/security/webhook.py](app/security/webhook.py) —acepta hex, `sha256=` y base64, y en `prod`
  **rechaza si no hay secreto**—, y Sinch por `sinch.verify_signature`
  ([sinch_webhook.py:50](app/routers/sinch_webhook.py#L50)).
- **La extracción de la caja negra está cerrada por default.** `/api/fleet/caja-negra` compara la
  llave con `hmac.compare_digest` y responde **404** —no 401— si falta o no empata, para no revelar
  siquiera que la ruta existe ([app/routers/fleet.py:37-40](app/routers/fleet.py#L37-L40)).

Es decir: aquí el trabajo del documento **sí es la conversación y el costo**, porque la base sobre
la que corre está bien puesta.

---

---

## Estado de la implementación — 19-ago-2026

**Los seis defectos están corregidos en el working tree, sin desplegar y sin commit.** Los cambios
son cinco archivos de código y tres de test:

| Defecto | Qué se hizo | Archivo |
|---|---|---|
| B-6 | Se graba `tokens_cacheados` junto a los tres campos que ya estaban | `app/caja_ia.py` |
| B-2 | `falta_del_cuestionario` mira cada campo por separado; `_estado_lead` deja de usar `not tipo_propiedad` como prueba de "no sé nada" | `bot/leads.py` · `bot/prompt.py` |
| B-5 | Fuera la hora al minuto: queda solo la fecha, que cambia una vez al día | `bot/prompt.py` |
| B-4 | `strict: true` + `additionalProperties: false` + todo en `required` con `null` admitido, en las dos tools | `bot/ai.py` |
| B-1 | Segunda red: un valor fuera de catálogo se **descarta** en vez de guardarse, y el catálogo se **lee del esquema** en vez de copiarse | `bot/leads.py` |
| B-3 | `presupuesto` sale del bucle de guardado. **`uso` se queda**: no era código muerto | `bot/leads.py` |
| B-7 | `_turno_real()`: el turno de relleno deja de contar como turno heredado | `observabilidad.py` |

**Tres cosas se descubrieron al implementar, y ya están corregidas arriba:**

1. **El score del caso canónico no era 83 sino 91** (y el roto no era 48 sino 56). Una oficina de
   200 m² cae en el escalón `(150, 25, 36)` de `TABLA_TAMANO_OFICINA`, no en el de 28. La pérdida
   de 35 puntos por el plazo sí estaba bien calculada.
2. **`uso` no era código muerto.** Lo lee `clasificacion.perfil_de` como una de las dos variables
   del cuadrante 2×2, y el panel lo edita. Solo el *score* lo ignora.
3. **Quitar la hora bastó para arreglar B-5**, sin reordenar el prompt: lo variable
   (`_estado_lead`, `primer_contacto`) ya estaba en el tercio de abajo, y el catálogo, el estilo y
   el cuestionario —que es el grueso— quedan en un prefijo estable de más de 2 000 caracteres.
   Lo verifica `test_prompt_caching.py`.
4. **Los 2 tests que fallaban al correr las dos suites juntas no eran ruido de tests: era B-7**,
   un defecto real de la caja negra que se manifestaba primero ahí. Arreglado en la fachada
   —`observabilidad.py` es el único archivo de la caja que se edita por repo, verificado: tiene
   14 hashes distintos en la flota, mientras que `caja_negra.py` es idéntico en 15—. Ahora las
   dos suites corren juntas: **156 tests en verde**.

**Los documentos del repo quedaron sincronizados con el código**, que es la promesa de este repo:
`flujo-bot.md` (lo obliga su propia cabecera: *"si tocas prompt.py o leads.py, tócalo también"*) y
`flow-clasificacion.md` ganaron la garantía del esquema estricto y la regla de que el cuestionario
no tiene orden obligatorio; el `README.md` corrigió el conteo de tests —decía 100— y ganó la línea
que pedía el §12 sobre no simplificar la estructura documental.

**Pendiente de decisión, no de código:** `spec.md` §B6 describe una clasificación que **no es la
implementada** —habla de segmento casa/oficina/Airbnb/corporativo, necesidad, fecha de entrega y
plazos de 3 a 24 meses— cuando el bot real hace dos preguntas: tipo de propiedad + dato ligado, y
plazo en tres rangos. Es el mismo defecto que en `salon-ai` figura como B-8: un documento que
describe un bot que no existe. No lo reescribí porque `spec.md` es lo que se acordó con el cliente
y decidir si se actualiza al código o el código a él no es una decisión técnica.

**Lo que falta y no depende de código:** desplegar en el orden del §Verificación, barrer la base en
busca de leads viejos mal clasificados (§11), y propagar el arreglo de B-6 al resto de la flota —
los **15** `caja_ia.py` del workspace son byte a byte idénticos (mismo md5), así que el cambio va a
la plantilla de `_vella_sistema` y de ahí se re-copia. Si se parcha repo por repo, la siguiente
sincronización lo borra.

---

## 0. Resumen: qué ya está bien y qué le falta

### Ya está bien — no se toca

- **Este repo es la referencia de documentación de toda la flota.** Es el único de los quince donde
  los estados están **definidos en código, documentados en prosa y probados con tests**, las tres
  cosas a la vez: `spec.md` (qué debe hacer), `arquitectura.md` (cómo está armado), `flujo-bot.md` +
  `flow-clasificacion.md` (qué pasa en un turno) y `tests/test_fases.py` (lo mismo, ejecutable).
  **Los otros dos proyectos de esta tanda —`bienesraices` y `aseguradora`— van a copiar este
  formato.** No se toca; se propaga.
- **La regla de oro está bien puesta y bien defendida.** Nada se clasifica, se notifica ni apaga el
  bot hasta que `leads.cuestionario_completo()` sea `True`. Hay **una sola** función que responde
  "¿ya terminó?", y aparece citada por nombre en el `flujo-bot.md`, explicada en el `spec.md` y
  ejercida en los tests. No se puede desincronizar el documento del código sin que un test se ponga
  rojo.
- **La clasificación la decide el código, no el modelo.** El bot pregunta; `evaluar_prospecto()` y
  `fase_calificada()` deciden. El prompt le dice explícitamente al modelo que **no** diga si es buen
  prospecto ni mencione categorías internas.
- **La fase manda sobre el bot.** Cuando un lead cae en su columna, es *la fase* la que decide qué
  se notifica, a quién y con qué mensaje de cierre — configurable desde el panel. Y si la fase ya
  mandó su cierre, **la despedida del modelo se tira**
  ([handler.py:209-215](app/services/bot/handler.py#L209-L215)). El determinista gana.
- **Compuerta anti-invención de cifras.** `fact_guard` bloquea montos y porcentajes que no estén en
  el prompt ni en el historial, y los sustituye por una deflexión que remite al asesor.
- **El cierre del loop está bien hecho.** Cuando se agotan las 4 rondas, la llamada final se hace
  **sin `tools`** ([ai.py:165-168](app/services/bot/ai.py#L165-L168)).
  *(Corregido el 19-ago: la versión anterior añadía que `bienesraices` "tiene ese mismo cierre mal
  formado y le devuelve un error técnico al cliente". **Ya no es cierto.** Su
  `app/bot/engine.py:186-188` cierra con `tools=tools, tool_choice="none"`, que es la otra forma
  correcta de hacerlo, y el docstring de arriba documenta el arreglo en pasado. Las dos formas
  valen; no hay nada que copiarle ni que corregirle en ese punto.)*
- **126 funciones de test** repartidas en 12 archivos, incluidos `test_fases.py`,
  `test_fases_acciones.py` y `test_score_calif.py`, más `tests_caja_negra/` (25 más). Eran 103
  antes de esta actualización; los 23 nuevos son los de regresión de B-1, B-2, B-3 y B-5.
- **El bot dejó de ser invisible.** Desde `48df483` (4-ago) está instalada la caja negra completa:
  SQLite aparte con retención de 24 h, middleware por acción del panel, `vella_fleet.py` para que
  los errores lleguen al tablero, y `caja_ia.instrumentar_openai()` envolviendo `Completions.create`
  ([main.py:73-74](app/main.py#L73-L74)) para que **cada llamada al modelo** quede grabada con su
  prompt exacto, su respuesta y sus tokens. El turno del bot se abre dentro de `handle_inbound` y no
  en el middleware, porque aquí el turno corre fuera del request (webhook → debounce 2 s →
  `asyncio.to_thread`): un detalle que en otros repos se hizo mal.
- **Seguridad de base bien puesta** (bloque de arriba): auth en los 11 routers del panel, firma
  verificada en los dos webhooks, extracción de la caja negra cerrada por default.

### Qué le falta — por impacto

1. **El esquema de captura no está garantizado, y aquí eso es peor que en el resto de la flota.**
   `apply_capturar_lead` guarda **lo que llegue, sin validar contra el enum**. Un `tiempo_renta`
   escrito como `"un año"` en vez de `"12+"` se guarda tal cual, y el lead termina en la columna
   equivocada del Kanban — con el aviso equivocado y el mensaje de cierre equivocado. Detalle en el
   §2 y en B-1.
2. **Bug concreto: quien contesta el plazo antes que el tipo de propiedad es re-preguntado.** El
   prompt le dice al modelo *"TODAVÍA NO SABES NADA de este prospecto"* aunque el plazo ya esté
   capturado (B-2).
3. **Un campo fantasma:** `presupuesto` se intenta leer en el guardado y **no existe en el
   esquema**, así que nunca llega. Es código muerto con aspecto de funcionalidad (B-3).
   *(Corregido el 19-ago: la versión anterior decía que `uso` tampoco servía. **Es falso** —`uso`
   es una de las dos variables del cuadrante 2×2, `clasificacion.py:56`, y se edita desde el
   panel. Se queda.)*
4. **La caché de prompt no pega nunca, y eso vale más que el cambio de modelo.** El system prompt
   mete la hora **al minuto** en su tercera línea, así que el prefijo cambia cada 60 segundos y
   OpenAI no puede reutilizar nada. Todo el ahorro que promete el §5 vive en la columna de caché:
   mientras esto siga así, **ese ahorro no existe, se cambie o no de modelo**. Confirmado hoy, ya no
   es una sospecha (B-5).
5. **El ahorro no se puede medir.** La caja negra ya graba tokens de entrada y de salida de cada
   llamada, pero **no graba `cached_tokens`**, que es justo el número que decide si este trabajo
   sirvió (B-6).
6. **El modelo se puede bajar a `gpt-5-nano`** una vez hecho el punto 1 — $0.05 por millón de
   tokens contra los $0.15 de hoy, y **quince veces menos** en la columna que de verdad manda, la de
   caché. Con la salvedad del punto 4: sin arreglar la caché, se cobra la tarifa de entrada normal.
7. **La recuperación sigue apagada.** Está construida y sin encender: faltan el scheduler, las
   plantillas de EMA aprobadas en Meta y leer `recovery_min_score`. No es parte de este cambio, pero
   conviene que quede escrito que sigue pendiente.

---

### 0b. Defectos concretos, verificados contra el código de hoy

Estos salieron de leer el código; ninguno estaba en un reporte previo. Cada uno se volvió a abrir
en el archivo el 19-ago-2026 contra `main` en `4979e5a`, y las líneas que se citan son las de hoy.
Se distinguen los **bugs** (algo está mal) de las **mejoras de diseño** (algo puede estar mejor).

| # | Defecto | Dónde | Efecto hoy | Se corrige con |
|---|---|---|---|---|
| **B-1** | `apply_capturar_lead` guarda los campos de texto **sin validar contra el enum** del esquema | [app/services/bot/leads.py:253-258](app/services/bot/leads.py#L253-L258) | Un `tiempo_renta="un año"` se guarda literal. `cuestionario_completo()` da `True` (el campo no está vacío) y el lead se clasifica: `TABLA_PLAZO.get("un año", 0)` = **0 puntos** de plazo, y `fase_calificada` lo manda a *Normal/Mid* en vez de *Bueno* porque no es exactamente `"12+"`. **Cae en la columna equivocada → se dispara el aviso de otra fase y el prospecto recibe el mensaje de cierre de otra fase.** Nadie se entera | `strict: true` en el esquema (§9) |
| **B-2** | `falta_del_cuestionario` devuelve `["tipo de propiedad", "tiempo de renta"]` en cuanto falta el tipo, **sin mirar si el plazo ya está capturado** | [app/services/bot/leads.py:154-156](app/services/bot/leads.py#L154-L156) | Quien abre con *"necesito amueblar por un año"* recibe el prompt que dice **"TODAVÍA NO SABES NADA de este prospecto"** ([prompt.py:31-32](app/services/bot/prompt.py#L31-L32)) y el bot le vuelve a preguntar el plazo que acaba de dar. Un turno perdido y una mala impresión de arranque | Que el listado mire cada campo por separado (§9) |
| **B-3** | `apply_capturar_lead` recorre `"presupuesto"` en su lista de campos de texto, pero **`presupuesto` no existe en `CAPTURAR_LEAD_TOOL`** | [leads.py:254](app/services/bot/leads.py#L254) vs [ai.py:36-87](app/services/bot/ai.py#L36-L87) | La columna `EmaLead.presupuesto` nunca se llena por el bot: es código muerto que parece funcionalidad. **Corrección del 19-ago:** la versión anterior decía además que `uso` no lo leía nadie. Es falso — `uso` es una de las dos variables del cuadrante 2×2 ([clasificacion.py:56](app/services/clasificacion.py#L56)) y se edita desde el panel ([index.html:725-726](frontend/index.html#L725-L726)). Lo que no lo lee es el *score*, que es otra cosa | Sacar `presupuesto` del bucle de guardado; **`uso` se queda** (§9) |
| **B-4** | El esquema tiene `"required": []`, sin `additionalProperties` y sin `strict` | [app/services/bot/ai.py:88](app/services/bot/ai.py#L88) | Es la causa raíz de B-1. **Bloquea el cambio de modelo** | `strict: true` (§9) |
| **B-5** | El system prompt mete la hora **al minuto** en su **tercera línea**, así que el prefijo cacheable cambia cada 60 segundos | [prompt.py:63](app/services/bot/prompt.py#L63) construye `fecha_hoy` con `{ahora.hour:02d}:{ahora.minute:02d}`, y [prompt.py:74](app/services/bot/prompt.py#L74) lo inserta justo debajo de la línea de identidad | **La caché de OpenAI no pega nunca.** Este prompt trae identidad, catálogo, estilo, cuestionario completo y contexto vivo del panel, y se reenvía entero en cada mensaje **y en cada ronda del loop** (hasta 4). Todo eso se paga a tarifa de entrada completa. Es el defecto más caro del repo: **anula por sí solo el ahorro que promete el §5** | Mover la hora al **final** del prompt, o dejar solo la fecha (§9) |
| **B-6** | `caja_ia` graba `prompt_tokens` y `completion_tokens`, pero **no** `usage.prompt_tokens_details.cached_tokens` | [app/caja_ia.py:72-77](app/caja_ia.py#L72-L77) | No hay forma de comprobar si la caché pegó ni cuánto se ahorró: se despliega a ciegas y el §5 queda sin evidencia. **Ojo:** `caja_ia.py` es un drop-in que la flota copia byte a byte, así que el cambio se decide para los 18 repos, no solo para este | Añadir el campo en la plantilla de `_vella_sistema` y re-copiar (§9) |
| **B-7** | `registrar()` sin turno abierto inventa un turno huérfano `{"turno_id": "sin-turno"}` **y lo deja pegado en el contextvar**, sin resetearlo. `_turno_o_actual` preguntaba `is not None`, así que después del primer evento suelto creía que SIEMPRE hay turno heredado | [caja_negra.py:183-185](app/caja_negra.py#L183-L185) leído desde [observabilidad.py:157](observabilidad.py#L157) | Un handler con `@accion_panel` que corre **fuera de un request** —un job, un comando de consola— emite `accion_ejecutada` (la forma que significa "dentro del turno de un request") en vez de `accion_panel`, y sus eventos se archivan bajo el turno de relleno. El visor recibe la forma equivocada y el timeline no cuadra. El docstring de `accion_panel` distingue los dos casos a propósito | `_turno_real()` en la fachada (§9) |
| **D-1** | *(diseño)* La recuperación está construida y apagada | `app/services/recovery.py` | Los leads que dejan de contestar no reciben nada. Es una decisión tomada, no un olvido — pero lleva meses así | Encender cuando estén las plantillas de Meta (§12) |

**El orden obligatorio es B-4 → B-1.** B-1 no se corrige "arreglando el guardado": se corrige
impidiendo que llegue el valor malo. Validar en `apply_capturar_lead` sería poner una segunda red
donde lo que falta es que no se caiga nadie.

**B-6 va antes que B-5, y B-5 antes que el cambio de modelo.** El orden no es estético: si se
arregla la caché sin haber instrumentado `cached_tokens`, no habrá manera de demostrar que funcionó;
y si se cambia el modelo antes de arreglar la caché, se estará midiendo el ahorro equivocado. B-6 es
una línea, B-5 es mover un renglón de sitio, y entre los dos desbloquean la única cifra que este
documento promete.

**Qué es lo urgente, en una frase:** B-1 le cuesta a EMA **leads**, y B-5 le cuesta **dinero en cada
mensaje**. Son independientes entre sí y pueden ir en paralelo; ninguno depende del otro.

**Cómo verificar que B-1 es real:** un test que llame
`apply_capturar_lead(lead, {"tipo_propiedad": "oficina", "oficina_m2": 200, "tiempo_renta": "un año"})`
y revise en qué fase quedó el lead. Hoy queda en **`oficina_mid`**; debería quedar en
**`oficina_bueno`**. Ese test falla con el código de hoy: esa es la prueba.

**Cómo verificar que B-2 es real:** un test que capture solo `tiempo_renta="12+"` y llame a
`falta_del_cuestionario(lead)`. Hoy devuelve `["tipo de propiedad", "tiempo de renta"]` — con el
plazo ya guardado en el lead.

**Cómo verificar que B-5 es real, sin desplegar nada:**

```python
>>> from app.services.bot.prompt import build_system_prompt
>>> a = build_system_prompt()
>>> # ...esperar a que cambie el minuto...
>>> b = build_system_prompt()
>>> a == b
False
>>> a.split("\n")[2]      # la tercera línea del prompt
'Fecha y hora actual: martes 19/8/2026, 14:07.'
```

Dos prefijos distintos para el mismo prompt, con el mismo lead y el mismo catálogo. La caché de
OpenAI empata **prefijos exactos**: si el minuto 3 de arriba cambia, no hay nada que reutilizar
aunque las 400 líneas siguientes sean idénticas. Esa es la prueba.

**Cómo verificar que B-6 es real:** `grep -n "cached" app/caja_ia.py` no devuelve nada. Los tres
campos que sí graba están en [caja_ia.py:74-76](app/caja_ia.py#L74-L76).

---

## 1. Qué es este bot hoy

Atiende por **tres canales** —WhatsApp (Kapso), Instagram y Messenger (Sinch)— a gente que pregunta
por rentar muebles, línea blanca y electrónica a EMA Rentals (Monterrey, entrega en todo México).

Su trabajo es exactamente uno: **filtrar**. Hace un cuestionario corto y formal, el código clasifica
al prospecto, lo coloca en su columna del Kanban, avisa a quien esa columna diga que hay que avisar,
y **se apaga** para que lo tome un asesor humano.

**No agenda, no cotiza, no da precios y no cierra.** Tono formal, de usted, y **cero emojis** por
instrucción explícita del cliente.

---

## 2. Cómo funciona ahora, en simple

Cuando alguien escribe, pasa esto por dentro:

1. Se juntan los mensajes de la ráfaga durante 2 segundos, para no contestar línea por línea.
2. **El lead se registra siempre**, aunque el bot no vaya a contestar. Entra al Kanban de todas
   formas.
3. Se revisa si el bot debe hablar: si el contacto está marcado como "no es lead", si un asesor tomó
   la conversación, o si el bot global está apagado, se calla.
4. Se arma un **texto de instrucciones**: quién es, el catálogo, el tono, el cuestionario, **lo que
   ya sabe de esta persona y lo que le falta preguntar**, y el contexto vivo que el equipo subió
   desde el panel.
5. Se le entrega ese texto más la conversación previa, junto con **dos herramientas**:
   `capturar_lead` (registrar lo que dijo) y `alertar_asesor` (pedir un humano). Se permiten hasta
   **4 vueltas**.
6. Cada vez que el modelo llama `capturar_lead`, el código guarda los datos, **clasifica** si el
   cuestionario ya está completo, y le devuelve al modelo un texto que dice qué falta todavía.
7. Antes de enviar, `fact_guard` revisa que no haya inventado cifras.
8. Si el cuestionario quedó completo, **el código** apaga el bot, mueve el lead a su fase, y la fase
   decide a quién se avisa y qué mensaje de cierre recibe el prospecto.

> **Herramienta / "function calling":** es la forma de que el modelo pida acciones al sistema en vez
> de inventarse las respuestas. El modelo no toca la base de datos: dice "quiero llamar a
> `capturar_lead` con estos valores", y el código decide qué pasa.

### La conversación de ejemplo

**Una persona escribe para amueblar oficinas de una empresa que se muda.**

> **Prospecto:** Buenas tardes, necesito amueblar unas oficinas nuevas

Por dentro: primer contacto, no hay nada capturado. El bot se presenta y hace la pregunta 1.

> **Bot:** Hola, le saluda Ema de EMA Rentals. Con gusto le ayudo. ¿La renta es para una oficina, un
> departamento o una casa?

> **Prospecto:** Oficina, son como 200 metros. La necesitamos por un año

Aquí está el punto débil. El modelo entiende perfectamente las tres cosas y llama `capturar_lead`.
Pero **nada garantiza que use los valores del catálogo**. Si devuelve esto:

```json
{"tipo_propiedad": "oficina", "oficina_m2": 200, "tiempo_renta": "un año"}
```

pasa lo siguiente en [app/services/bot/leads.py:253-258](app/services/bot/leads.py#L253-L258):

```python
for campo in ("tipo_propiedad", "tiempo_renta", "uso", "zona", "presupuesto", ...):
    v = args.get(campo)
    if v not in (None, ""):
        setattr(lead, campo, v)     # <-- se guarda TAL CUAL, sin mirar el enum
```

Y a partir de ahí el error se propaga solo, en cascada y en silencio:

1. `cuestionario_completo(lead)` → **`True`**. El campo no está vacío, así que para el sistema el
   plazo está contestado.
2. `evaluar_prospecto` → **buen prospecto** (200 m² ≥ 100). Correcto.
3. `fase_calificada(lead, bueno=True)` → pregunta `if lead.tiempo_renta == "12+"`. Como dice
   `"un año"`, **no empata**, y cae en la rama de abajo: **`oficina_mid`** en vez de
   **`oficina_bueno`**.
4. `TABLA_PLAZO.get("un año", 0)` → **0 puntos** de plazo en el score. El lead pierde los 35 puntos
   que le tocaban.
5. `fases_acciones.al_entrar_a_fase` ejecuta **lo que tenga configurado la fase Oficina Mid**: puede
   ser otro destinatario, u otro mensaje de cierre, o ninguna notificación si esa columna la tiene
   apagada.

> **Bot:** Perfecto, gracias por la información. En unos momentos un asesor se pondrá en contacto
> con usted.

**Todo salió "bien" y todo salió mal.** El prospecto tuvo una conversación impecable. Pero un lead
de oficina de 200 m² a doce meses —de los mejores que puede recibir EMA— quedó en la columna de
segunda prioridad, con score bajo, y quizá sin que se avisara a nadie. Y **nada en el panel lo
delata**: el lead se ve completo, con sus datos correctos, en la columna equivocada.

Este es el peor efecto del problema en toda la tanda, precisamente porque este repo hace bien lo
demás: como la fase manda sobre la notificación y sobre el cierre, un dato mal formado no se queda
en el dato — **cambia a quién se le avisa y qué se le dice al prospecto**.

---

## 3. Cómo funciona ahora, en técnico

| Pieza | Dónde | Qué hace |
|---|---|---|
| Motor conversacional | `app/services/bot/ai.py` | Loop de tool-calling, `_MAX_RONDAS = 4`, `max_tokens=400`, `temperature=0.5`, timeout 20 s |
| Orquestador del turno | `app/services/bot/handler.py` | Compuertas de coexistencia, handlers, decisión de cierre, burbujas (máx. 2) |
| Herramientas | `app/services/bot/ai.py` | 2: `capturar_lead`, `alertar_asesor` |
| Calificación | `app/services/bot/leads.py` | `falta_del_cuestionario` · `evaluar_prospecto` · `fase_calificada` · `desglose_score` |
| Acciones de fase | `app/services/fases_acciones.py` | `al_entrar_a_fase`: qué se notifica y qué cierre se manda |
| Guardas | `app/services/bot/guards.py` | `fact_guard` (dinero / % / miles) |
| Prompt | `app/services/bot/prompt.py` | Reconstruido por turno: catálogo + sabidos/faltan + contexto vivo del panel |
| Modelo | `app/config.py:33` | `openai_model: str = "gpt-4o-mini"`, override por `OPENAI_MODEL` |
| Observabilidad | `observabilidad.py` · `app/caja_negra*.py` | Caja negra local (SQLite aparte, 24 h), middleware por acción del panel, `vella_fleet.py` al tablero |
| Instrumentación de IA | `app/caja_ia.py` | Envuelve `Completions.create`: graba `prompt_ia`, `respuesta_ia` y `metrica/tokens_ia` de **cada** llamada, sin tocar el motor |
| Extracción para Vella | `app/routers/fleet.py` | `/api/fleet/caja-negra`, header `X-Fleet-Key`, 404 si falta la llave |
| Firma de webhooks | `app/security/webhook.py` · `app/services/sinch.py` | HMAC-SHA256 en Kapso (hex / `sha256=` / base64) y firma propia en Sinch |

**El cuestionario, que es todo el producto:**

```
tipo de propiedad ──┬─ casa / departamento → ¿cuántas recámaras?
                    └─ oficina             → ¿cuántos m², o cuántas personas?
                                ↓
                       ¿por cuánto tiempo?  (0-6 / 6-12 / 12+)
                                ↓
                        cuestionario completo
```

**El punto crítico**, en [app/services/bot/ai.py:56-61 y :88](app/services/bot/ai.py#L56-L61):

```python
"tiempo_renta": {
    "type": "string",
    "enum": ["0-6", "6-12", "12+"],
    "description": "Cuánto tiempo quiere rentar: '0-6' (6 meses o menos), ...",
},
...
"required": [],
```

Tres cosas que **no** están:

1. **No hay `"strict": true`.** Sin eso, ese `enum` es *documentación para el modelo*, no una
   restricción de la API. El modelo lo respeta la mayoría de las veces; "la mayoría de las veces" no
   es una garantía.
2. **No hay `additionalProperties: false`.** El modelo puede inventar un campo que nadie lee.
3. **`required` está vacío**, correcto para el diseño (todos los campos son opcionales) pero
   incompatible con `strict` tal cual — ver §9.

Y a diferencia de `bienesraices` —que valida contra catálogo y descarta lo que no empata— **aquí no
hay segunda red**: lo que el modelo mande, se guarda.

---

## 4. Qué cambia

| | Antes | Después |
|---|---|---|
| Qué garantiza el esquema | nada; el `enum` es una sugerencia | la API **obliga**: enums, tipos y campos exactos |
| Un `tiempo_renta` mal escrito | se guarda literal → fase, score, aviso y cierre equivocados | no puede ocurrir |
| Campos inventados por el modelo | se ignoran sin dejar rastro | la API los rechaza antes de generarlos |
| De qué depende que el JSON salga bien | del tamaño y la calidad del modelo | del esquema (el modelo casi deja de importar) |
| Plazo contestado antes que el tipo | el bot lo vuelve a preguntar (B-2) | se conserva y no se repregunta |
| Caché del prompt | no pega nunca: la hora al minuto rompe el prefijo cada 60 s | prefijo estable; se paga la tarifa cacheada |
| Medición del ahorro | imposible: `cached_tokens` no se graba | queda en la caja negra, turno por turno |
| Modelo | `gpt-4o-mini` | `gpt-5-nano` |
| Rondas del loop | 4 | 4 (sin cambio, pero ya medibles) |
| Documentación | ya es la referencia de la flota | sin cambios; se propaga a los otros dos |

### La misma conversación, con el diseño nuevo

> **Prospecto:** Oficina, son como 200 metros. La necesitamos por un año

1. El modelo llama `capturar_lead`. Con `strict: true`, **el JSON no puede salir de otra forma**:

   ```json
   {"nombre": null, "tipo_propiedad": "oficina", "recamaras": null, "oficina_m2": 200,
    "oficina_personas": null, "tiempo_renta": "12+", "uso": null, "zona": null, ...}
   ```

   `tiempo_renta` **tiene** que ser `0-6`, `6-12` o `12+`. La API no permite generar `"un año"`; el
   modelo se ve obligado a hacer la traducción que ya sabe hacer.
2. `cuestionario_completo` → `True`. `evaluar_prospecto` → buen prospecto.
3. `fase_calificada` → `lead.tiempo_renta == "12+"` → **`oficina_bueno`**. La columna correcta.
4. Score: 36 (tamaño — 200 m² cae en el escalón de 150+, no en el de 100) + 35 (plazo 12+) +
   20 (tipo oficina) = **91**, en vez de los 56 de hoy. Los 35 que se pierden son exactamente los
   del plazo. *(Corregido el 19-ago: la versión anterior decía 83 y 48, calculados con el escalón
   equivocado de `TABLA_TAMANO_OFICINA`. La diferencia entre ambos —35— sí estaba bien.)*
5. La fase *Oficina Bueno* dispara su aviso a quien esté configurado y manda **su** mensaje de
   cierre.

> **Bot:** Perfecto, gracias por la información. En unos momentos un asesor se pondrá en contacto
> con usted.

**El prospecto ve exactamente lo mismo. El equipo ve otra cosa completamente distinta:** el mejor
lead de la semana en la columna donde le toca, priorizado arriba, y con el aviso saliendo a quien
debía recibirlo.

---

## 5. Motor: qué modelo va a usar

**Hoy:** `gpt-4o-mini` (`app/config.py:33`; verificar que Railway no lo esté sobreescribiendo).
**Va a usar:** `gpt-5-nano`.

> **Token:** la unidad en que se cobra. Un mensaje de WhatsApp son decenas; las instrucciones del
> bot, miles. **Caché:** si el texto del inicio se repite idéntico entre llamadas, se cobra mucho
> más barato.

| Modelo | Entrada /1M | En caché /1M | Salida /1M |
|---|---:|---:|---:|
| gpt-4o-mini (hoy) | $0.15 | $0.075 | $0.60 |
| gpt-5-mini | $0.25 | $0.025 | $2.00 |
| **gpt-5-nano** | **$0.05** | **$0.005** | $0.40 |

**Lo que domina es la columna de caché**, porque el system prompt de este bot se reenvía entero en
cada mensaje y en cada ronda del loop: trae la identidad, el catálogo, el estilo, el cuestionario
completo y el contexto vivo del panel. Ahí `gpt-5-nano` cuesta **quince veces menos** que lo que se
paga hoy.

> **Advertencia que cambia el cálculo.** Esa columna **hoy no se está usando**. La hora al minuto en
> la tercera línea del prompt rompe el prefijo cada 60 segundos (B-5), así que todo se paga a tarifa
> de entrada completa: los $0.15 de la primera columna, no los $0.075 de la segunda. Cambiar el
> modelo sin arreglar B-5 baja de $0.15 a $0.05 —una mejora real, tres veces— pero deja sobre la
> mesa el otro factor diez. **Arreglados los dos, el costo de entrada pasa de $0.15 a $0.005: treinta
> veces menos.** Por eso B-5 va primero: es un renglón de código y vale más que el cambio de motor.

**Por qué nano alcanza para este bot.** Porque aquí el modelo hace menos trabajo que en cualquier
otro proyecto de la flota. No calcula disponibilidad, no da precios, no decide si alguien es buen
prospecto, no elige la columna del Kanban, no escribe el mensaje de cierre cuando la fase tiene uno.
Le quedan tres tareas: entender español, hacer **una** pregunta a la vez, y traducir lo que le
dijeron a un JSON. Las dos primeras nano las hace bien. La tercera es justo la que `strict` deja de
depender del modelo.

**El orden importa: primero `strict`, después el modelo.** Si se hacen juntos y algo sale distinto,
no habrá forma de saber cuál lo causó. Van en despliegues separados.

---

## 6. Esto va a funcionar como en CesarJavier

El patrón se copia de **`CesarJavier`** (el consultorio dental de 5 dentistas con una sola agenda),
donde la idea ya está probada en producción — aunque aplicada a otro campo.

> **Nota de verificación (19-ago).** Todo lo demás en este documento está comprobado contra código
> que tengo delante. Esta sección **no**: `CesarJavier` no está clonado en el workspace, así que ni
> el diseño de `slot_id` firmados con HMAC ni el commit `c7d091e` se pudieron abrir. El commit
> `e54b2e8` de `DorianSalon` que se cita más abajo **sí existe y su título coincide**. Si esta
> sección va a sustentar una decisión, vale clonar el repo y confirmarla; el argumento del §9 no
> depende de ella —se sostiene solo con B-1, que sí está verificado aquí—.

**Qué resolvió CesarJavier.** El problema recurrente de toda la flota es que el modelo mencione algo
que no existe: una hora que no está libre, un tratamiento que no está en el catálogo. Casi todos los
repos lo atacan con un *guard*: dejan que el modelo escriba y después revisan el texto. CesarJavier
hizo lo contrario: sus horarios no son texto que el modelo redacta, son `slot_id` firmados con HMAC,
y el modelo solo puede elegir entre los tres que se le entregaron. **Una hora inventada no se
bloquea — no existe.**

**Por qué eso funciona mejor que un guard.** Porque un guard es una lista de casos que alguien
pensó, y siempre falta uno. La prueba está en el historial de la propia flota: el commit `e54b2e8`
de `DorianSalon` documenta que el guardrail cubría la frase *"ya está agendada"* pero no *"está
agendada"*, y por ese hueco pasó a producción una confirmación de una cita que nunca se creó. Nadie
había sido descuidado; simplemente no se puede enumerar todas las formas de escribir algo mal.

**Por qué aplica aquí, aunque este bot no tenga agenda.** El paralelo es exacto:

| | `CesarJavier` | `emarentals` hoy | `emarentals` con `strict` |
|---|---|---|---|
| Qué puede emitir el modelo | solo 3 `slot_id` firmados | cualquier cadena en `tiempo_renta` | solo `0-6`, `6-12` o `12+` |
| Quién lo hace cumplir | la firma HMAC | **nadie** | la API de OpenAI |
| Qué pasa si se equivoca | imposible equivocarse | se guarda y contamina fase, score, aviso y cierre | imposible equivocarse |

Es la misma jugada —mover la garantía del texto al esquema— aplicada al campo donde este proyecto
tiene el hueco. Y cuesta unas líneas de JSON en vez de una reescritura: EMA no necesita ni holds ni
TTL ni firmas, porque no reserva nada.

**Un segundo caso lo confirma, en un giro distinto.** El commit `c7d091e` de CesarJavier lleva por
título *"El tratamiento se resuelve en código cuando el modelo no lo anota"*. Mismo diagnóstico, otro
campo: **cuando el modelo llena el dato, a veces lo llena mal; cuando lo llena una restricción, no.**

---

## 7. Qué tipo de bot necesita

**Sigue siendo function calling con dos tools.** No cambia el mecanismo ni el número de
herramientas; cambia que el esquema de `capturar_lead` pase de descriptivo a obligatorio.

**No necesita ser multi-agente**, y vale explicar por qué, porque la idea suena razonable: un agente
central que le pregunte a un "agente clasificador" y a un "agente de catálogo".

Un agente clasificador **sigue siendo un modelo emitiendo un veredicto**. Aquí el veredicto ya lo
emiten funciones (`evaluar_prospecto`, `fase_calificada`, `desglose_score`), que son estrictamente
mejores: no alucinan, son reproducibles, y se prueban con un test — que es exactamente lo que
`test_score_calif.py` hace hoy. Mover eso a un sub-agente sería un retroceso disfrazado de
arquitectura.

Los costos también son reales: cada sub-agente es otro prefijo de prompt que se paga entero (justo
lo que esta actualización busca abaratar), suma latencia en un canal donde la gente espera
respuesta, y convierte "el lead quedó en la columna rara" en "¿cuál de los tres se equivocó?".

**La división que sí paga es por tipo de trabajo, no por dominio:**

| Tarea | Quién debe hacerla |
|---|---|
| Entender qué escribió el prospecto | el modelo |
| Clasificar, puntuar, decidir la fase y el aviso | **el código** |
| Redactar la pregunta que sigue, formal y sin emojis | el modelo |

Entender y redactar son tareas de lenguaje. Clasificar y puntuar son aritmética. Este repo ya tiene
esa frontera mejor puesta que nadie; `strict` solo la sella del lado de la entrada.

---

## 8. Qué flujo necesita para funcionar bien

```
Prospecto escribe (WhatsApp · Instagram · Messenger)
   ↓
[CÓDIGO]  debounce 2 s: junta la ráfaga en UN turno
   ↓
[CÓDIGO]  registra el lead SIEMPRE (entra al Kanban aunque el bot calle)
   ↓
[CÓDIGO]  ¿'no es lead'? ¿un asesor tomó el chat? ¿bot global apagado? → calla
   ↓
[CÓDIGO]  arma el prompt con LO QUE YA SABE y LO QUE FALTA (falta_del_cuestionario)
   ↓
[MODELO]  hace UNA pregunta y llama capturar_lead con lo que le dijeron
   ↓
[API]     ←── LO NUEVO: strict garantiza enums, tipos y campos exactos
   ↓
[CÓDIGO]  guarda · ¿cuestionario completo?
          ├─ NO  → le dice al modelo qué falta; el lead espera en la antesala
          └─ SÍ  → evaluar_prospecto → fase_calificada → score
   ↓
[CÓDIGO]  fact_guard revisa que no haya inventado cifras
   ↓
[CÓDIGO]  apaga el bot · la FASE decide a quién se avisa y qué cierre recibe el prospecto
          └─ si la fase mandó su cierre, la despedida del modelo se TIRA
```

**La frontera está donde empieza la decisión.** Todo lo que se pueda decidir con una regla —si es
buen prospecto, en qué columna cae, cuántos puntos vale, a quién se avisa— lo hace el código. El
modelo pregunta y redacta.

Lo que `strict` cambia es **el punto de entrada de esa frontera**: hoy el código recibe algo que
podría venir mal formado y lo guarda de todas formas; mañana recibe algo que ya viene con la forma
correcta.

---

## 9. Qué hay que cambiar

**En `app/services/bot/ai.py` — el esquema de `capturar_lead` (B-4, y con él B-1)**

Añadir `"strict": true` a la función, `"additionalProperties": false` al objeto, y **todas** las
propiedades en `"required"`. Esto último parece contradecir el diseño (todos los campos son
opcionales) y no lo hace: en modo estricto, "opcional" se expresa permitiendo `null`. Es decir,
`"type": ["string", "null"]` en vez de `"type": "string"`, y el `null` incluido en el `enum`.

```json
"parameters": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "tipo_propiedad":   {"type": ["string", "null"],
                         "enum": ["oficina", "departamento", "casa", null], ...},
    "recamaras":        {"type": ["integer", "null"], ...},
    "oficina_m2":       {"type": ["integer", "null"], ...},
    "oficina_personas": {"type": ["integer", "null"], ...},
    "tiempo_renta":     {"type": ["string", "null"],
                         "enum": ["0-6", "6-12", "12+", null], ...},
    "uso":              {"type": ["string", "null"], "enum": ["reventa", "propio", null], ...},
    "solo_informacion": {"type": ["boolean", "null"], ...},
    ...
  },
  "required": ["nombre", "tipo_propiedad", "recamaras", "oficina_m2", "oficina_personas",
               "tiempo_renta", "uso", "zona", "nivel_interes", "que_pregunto",
               "resumen", "motivo_perdida", "solo_informacion"]
}
```

Lo mismo en `ALERTAR_ASESOR_TOOL` (es trivial: un solo campo).

`apply_capturar_lead` **no hay que reescribirlo**: ya salta los valores vacíos con
`if v not in (None, "")`, que es exactamente el contrato que produce el esquema estricto. Vale, sí,
añadir una validación de enum como red de seguridad —no estorba y protege el día en que alguien
agregue un campo sin `strict`— pero la garantía real está en el esquema.

**En `app/services/bot/leads.py` — el listado de faltantes (B-2)**

Hoy:

```python
if not tp:
    return ["tipo de propiedad", "tiempo de renta"]
```

Debe mirar cada campo por su cuenta: si falta el tipo, pedir el tipo; y añadir el plazo **solo si
`lead.tiempo_renta` está vacío**. Con eso, `_estado_lead` deja de decir "TODAVÍA NO SABES NADA"
cuando sí sabe algo, y el bot deja de repreguntar lo que le acaban de contestar.

**En `app/services/bot/leads.py` y el esquema — los campos fantasma (B-3)**

Decidir una de dos, y dejarlo escrito:
- `presupuesto`: o se agrega al esquema de `capturar_lead` y se usa en el score, o se quita del
  bucle de `apply_capturar_lead` y de `EmaLead`.
- `uso` (reventa/propio): **se queda en el esquema, y no era código muerto.** Es una de las dos
  variables del cuadrante 2×2 junto con `ticket_mensual` (`clasificacion.perfil_de`), y el panel
  lo muestra como dos botones editables en el perfil del lead. Que el bot lo capture le ahorra al
  asesor la mitad de ese trabajo. Lo que sigue abierto es si además debería **pesar en el score**
  —un operador de Airbnb o un desarrollador probablemente vale más que alguien amueblando su
  depa—, pero eso cambia las tablas que el panel expone en su popover de ayuda: es una decisión
  de negocio de EMA, no de esta actualización.

**En `app/services/bot/prompt.py` — la hora que rompe la caché (B-5)**

Hoy, en [prompt.py:72-74](app/services/bot/prompt.py#L72-L74), el prompt abre así:

```
Eres {nombre}, asesora de {EMPRESA['nombre']}. {BOT['tono']}
...
Fecha y hora actual: {fecha_hoy}.      ← tercera línea, cambia cada minuto
```

Dos arreglos posibles, y conviene elegir a conciencia:

- **El barato y suficiente:** quitar la hora y dejar solo la fecha
  (`{_DIAS[...]} {día}/{mes}/{año}`). El prefijo pasa a cambiar **una vez al día** en vez de 1 440.
  Este bot no agenda ni consulta disponibilidad: la hora exacta no la usa para nada.
- **El completo:** dejar la hora pero **moverla al final** del system prompt, después del catálogo,
  el estilo y el cuestionario. Así todo lo estable —que es casi todo— queda en un prefijo idéntico y
  cacheable, y solo la cola cambia.

Recomendación: **la primera**, por simple, y porque `colegiolizardi` —que sí resuelve bien este
punto— sencillamente no mete la hora en su prompt.

**En `app/caja_ia.py` — medir el ahorro (B-6)**

En el bloque `metrica` de [caja_ia.py:72-77](app/caja_ia.py#L72-L77), junto a los tres campos que ya
se graban:

```python
"tokens_cacheados": getattr(getattr(uso, "prompt_tokens_details", None), "cached_tokens", None),
```

Va con `getattr` anidado a propósito: los modelos que no reportan el detalle devuelven `None` en vez
de romper. **Este archivo es un drop-in de flota**: el cambio se hace en
`_vella_sistema/plantillas/caja-negra` y se re-copia, no se parcha solo aquí — si se parcha suelto,
la siguiente re-copia lo borra sin que nadie se entere.

**En `tests/` — los casos de regresión**

1. `apply_capturar_lead(lead, {"tipo_propiedad":"oficina","oficina_m2":200,"tiempo_renta":"un año"})`
   → hoy termina en `oficina_mid` con score 56. Con el esquema estricto ese valor no puede llegar;
   el test se escribe contra el esquema (que `"un año"` no valide) y contra la fase resultante con
   `"12+"`.
2. `falta_del_cuestionario` con solo `tiempo_renta="12+"` → **no** debe listar "tiempo de renta".
   Hoy sí lo lista: esa es la prueba de B-2.
3. **`test_prompt_caching.py`, escrito aquí desde cero** (no existe en ningún repo de la flota, ver
   §12): dos llamadas a `build_system_prompt()` con el mismo lead deben producir **el mismo prefijo**
   —comparar los primeros ~2 000 caracteres— aunque cambie el minuto. Es el candado que impide que
   alguien vuelva a meter un dato volátil arriba del prompt sin darse cuenta.

---

## 10. Qué se va a notar y qué no

### Lo que va a notar el prospecto

Casi nada, y eso es lo correcto: **este cambio no es para el prospecto, es para el equipo.** Lo
único visible es que quien conteste el plazo antes que el tipo de propiedad ya no verá al bot
preguntárselo dos veces (B-2).

### Lo que va a notar el equipo de EMA

- **Los leads caen en la columna correcta.** Un dato traducido mal deja de mandar a un prospecto de
  primera a la columna de segunda.
- **Los avisos salen a quien deben.** Como el aviso lo decide la fase, la fase correcta significa el
  destinatario correcto.
- **El score deja de tener agujeros.** Un plazo mal escrito ya no cuesta 35 puntos en silencio.
- **El mensaje de cierre que recibe el prospecto es el de su fase real.**

### Lo que no va a cambiar

- El tono formal, de usted, sin emojis
- El cuestionario, su orden y la regla de una pregunta por mensaje
- La regla de oro: nada se clasifica ni se notifica hasta que el cuestionario esté completo
- Los umbrales de buen prospecto (casa siempre; depa ≥2 recámaras; oficina ≥100 m² o ≥20 personas)
- Las tablas del score y el popover de ayuda del panel
- Los tres canales y el manejo de notas de voz
- La recuperación sigue apagada

### Lo que cambia por dentro y no se ve

- El costo por conversación baja de forma notable — y por primera vez **se puede demostrar**, porque
  la caja negra pasa a grabar cuántos tokens vinieron de caché
- Un campo inventado por el modelo ya no puede llegar al handler
- El system prompt deja de ser un texto nuevo cada minuto y pasa a ser un prefijo estable

---

## 11. Riesgos

| Riesgo | Probabilidad | Cómo se detecta antes de desplegar |
|---|---|---|
| Que el cambio de variable no tome efecto | **Alta si se olvida** | El cliente de OpenAI se instancia dentro de `_client()` con `settings` cargado al importar el módulo: **hace falta redeploy completo**, no basta cambiar la variable en Railway |
| Que `gpt-5-nano` escriba más seco y se sienta menos cálido | Media | El tono aquí ya es formal y sin emojis, así que el margen de degradación es menor que en un salón. Aun así va en despliegue aparte: correr los 103 tests y revisar 10 conversaciones reales |
| Que `strict` rompa una llamada por un enum mal declarado | Baja | La API responde con un error explícito de esquema. Se prueba en local antes de subir |
| Que haya leads viejos ya mal clasificados en la base | **Media, y ya ocurrió** | Este es el punto ciego: `strict` evita los futuros, **no arregla los pasados**. Ver abajo |
| Que al arreglar B-2 el bot deje de preguntar algo | Baja | El test de regresión cubre las cuatro combinaciones (con/sin tipo × con/sin plazo) |
| Que quitar la hora del prompt afecte alguna respuesta | **Muy baja** | Este bot no agenda, no cotiza y no consulta disponibilidad: no hay una sola regla que dependa de la hora. La fecha se conserva |
| Que el arreglo de `caja_ia` se pierda en la siguiente re-copia del drop-in | **Media si se parcha suelto** | El cambio va en `_vella_sistema/plantillas/caja-negra` y se re-copia desde ahí. Si se edita solo este repo, la próxima sincronización lo borra sin aviso |

**Sobre los leads ya mal clasificados.** Si B-1 ha estado ocurriendo, hay leads en el Kanban en la
columna equivocada **desde hace meses**, y arreglar el esquema no los mueve. Vale una consulta antes
de desplegar: buscar leads con `tiempo_renta` que no sea exactamente `0-6`, `6-12` o `12+`, y lo
mismo con `tipo_propiedad`. Si aparecen, se reclasifican con `apply_capturar_lead` corriendo sobre
los valores normalizados. Es un script de una tarde y probablemente recupere leads buenos que están
enterrados.

**El riesgo que no existe:** que se le diga algo equivocado al prospecto. `fact_guard` no cambia, el
prompt no cambia y las reglas de negocio no cambian. Este trabajo mejora lo que el bot **captura**,
no lo que **dice**.

---

## 12. Recomendaciones adicionales

**Este repo es el molde documental; conviene decirlo dentro del propio repo.** Los otros dos
proyectos de esta tanda van a copiar `spec.md` + `arquitectura.md` + `flujo-bot.md` + `test_fases.py`
de aquí. Una línea en el `README.md` que lo diga evita que alguien "simplifique" esa estructura sin
saber que es la referencia.

**Decidir qué pasa con `uso`.** Hoy se le pide al modelo que capture reventa-vs-propio en cada
llamada y nadie lo lee. O pesa en el score —un operador de Airbnb o un desarrollador probablemente
vale más que alguien amueblando su departamento— o sale del esquema. Ambas son mejores que la
situación actual.

**Encender la recuperación, o dejar escrito por qué no.** Está construida y apagada desde hace
meses. Faltan el scheduler, las plantillas de EMA aprobadas en Meta y leer `recovery_min_score`. Si
no se va a encender esta temporada, conviene que el `spec.md` lo diga como decisión, no como
pendiente — un pendiente viejo se lee como un olvido.

**~~Auditar el bug de caché~~ → ya está auditado, y es peor de lo que decía la versión anterior.**
La versión previa de este documento dejaba esto como sospecha (*"si esa cadena va cerca del inicio
del prompt…"*) y remitía a copiar `tests/test_prompt_caching.py` de `colegiolizardi`. **Las dos cosas
hay que corregirlas:**

- **La sospecha está confirmada.** La hora al minuto no va "cerca del inicio": va en la **tercera
  línea**, [prompt.py:74](app/services/bot/prompt.py#L74). La caché no pega nunca. Por eso dejó de
  ser una recomendación del §12 y subió a **B-5**, con su arreglo en el §9.
- **Ese archivo no existe.** `colegiolizardi` **no tiene** `tests/test_prompt_caching.py`
  —sus cinco archivos de test son `test_ai_tools`, `test_fact_guard`, `test_recuperacion`,
  `test_secuencia` y `conftest`— y tampoco tiene el bug: sencillamente **no mete la hora en su
  prompt** (`grep -rn "hour" --include=*.py` no encuentra nada en su generador). No hay archivo que
  copiar; el test se escribe aquí desde cero (§9, caso 3), y **este repo pasa a ser el que lo
  aporta** al resto de la flota.

**Medir en qué ronda cierra el loop — ya se puede, y sin escribir código.** El tope son 4 y nadie
sabe cuántas se usan de verdad. Desde `48df483` esto es una consulta, no un proyecto: `caja_ia`
graba un evento `metrica/tokens_ia` **por cada llamada al modelo**, y el turno del bot es un turno de
la caja negra, así que **contar los eventos de un turno da las rondas de ese turno**. Con dos tools y
un cuestionario de dos preguntas, es plausible que cierre casi siempre en 1 o 2 — y cada ronda de
menos es un system prompt entero de menos. Vale sacar el número antes de tocar nada: si ya cierra en
1, bajar `_MAX_RONDAS` no ahorra un peso y no hay por qué tocarlo.

---

## Verificación antes de desplegar

1. **Los 103 tests en verde**, más los dos casos de regresión nuevos del §9.
2. **El caso canónico de este proyecto:** un prospecto de oficina de 200 m² a doce meses, escrito en
   lenguaje natural ("como 200 metros", "por un año"). Verificar que (a) `tiempo_renta` quede en
   `"12+"`, (b) la fase resultante sea `oficina_bueno`, (c) el score sea **91** (36+35+20), y (d) el aviso salga al
   destinatario que esa fase tiene configurado.
3. **Los casos del cuestionario a la inversa:** contestar el plazo antes que el tipo de propiedad, y
   verificar que el bot **no** lo vuelva a preguntar.
4. **Barrer la base** en busca de valores fuera de catálogo en `tiempo_renta` y `tipo_propiedad`
   (§11), y reclasificar lo que aparezca.
5. **Medir `cached_tokens` y rondas** antes y después. Sin esa medición no hay forma de saber si el
   ahorro se materializó — y hoy **no se puede medir**, porque el campo no se graba (B-6). Este paso
   arranca instrumentando, no midiendo: primero B-6, después una línea base de unos días, después
   B-5, después volver a mirar. Las rondas sí se pueden contar desde ya, con los eventos
   `metrica/tokens_ia` por turno de la caja negra.
6. **El prefijo del prompt es estable**: `test_prompt_caching.py` en verde, y una comprobación a mano
   de que dos turnos separados por más de un minuto generan el mismo encabezado.
7. **El cambio de modelo va al final**, después de que `strict` y el arreglo de caché lleven unos
   días estables. Tres cambios en un despliegue son tres sospechosos cuando algo salga distinto.

**Orden de despliegue sugerido, uno por uno:**

| # | Cambio | Por qué en ese lugar |
|---|---|---|
| 1 | B-6 — grabar `cached_tokens` | Sin esto, los pasos 3 y 5 no se pueden evaluar. Es una línea y no cambia comportamiento |
| 2 | B-2 — el listado de faltantes | Independiente de todo lo demás, arregla un bug visible para el prospecto |
| 3 | B-5 — la hora fuera del prefijo | El de mayor retorno por línea tocada. Medir antes y después |
| 4 | B-4 + B-1 — `strict` en el esquema | El que evita leads en la columna equivocada. Va solo, y con los tests nuevos |
| 5 | Barrido de la base (§11) | Después de `strict`, para no reclasificar mientras todavía entran valores malos |
| 6 | B-3 — decidir `uso` y `presupuesto` | Limpieza; no urge, pero no debe quedarse sin decidir |
| 7 | `gpt-5-nano` | Al final, con todo lo demás estable y medido |
</content>
