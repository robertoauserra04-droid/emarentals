# Flujo de clasificación — EMA Rentals

Este documento es la fuente de verdad del cuestionario y de las reglas de clasificación.

Vive aquí porque el flujo está partido en **dos archivos que hay que mantener sincronizados a
mano** y ya se desincronizaron una vez:

| Pieza | Archivo | Qué manda |
|---|---|---|
| Las **preguntas** | `app/services/bot/prompt.py` | texto que lee el modelo |
| Las **reglas** | `app/services/bot/leads.py` | código determinista que decide |

> Si cambias una, cambia la otra **y este documento**. El desajuste anterior fue justo ese: el
> prompt preguntaba las recámaras de una casa pero el código daba el cuestionario por terminado
> sin ellas, así que el bot cerraba saltándose una pregunta.

---

## El cuestionario

Dos preguntas, una a la vez, en orden. La primera tiene una pregunta ligada según la respuesta.

```
PREGUNTA 1 — ¿oficina, departamento o casa?
   │
   ├─ CASA ─────────→ ¿cuántas recámaras tiene la casa?
   ├─ DEPARTAMENTO ─→ ¿cuántas recámaras tiene el departamento?
   └─ OFICINA ──────→ ¿cuántos m² aproximadamente, o cuántas personas trabajarán ahí?

PREGUNTA 2 — ¿por cuánto tiempo?
   6 meses o menos  ·  de 6 a 12 meses  ·  12 meses o más
```

**El cuestionario está completo** cuando hay tipo de propiedad **+** su dato ligado **+** tiempo
de renta. Lo decide `leads.cuestionario_completo()`; `leads.falta_del_cuestionario()` dice qué
falta y se usa tanto en el prompt como en la alerta al asesor.

---

## Regla de oro

**Nada pasa hasta que el cuestionario esté completo.** Ni clasificar, ni notificar, ni apagar el
bot, ni acomodar en una columna del Kanban.

La única excepción: si el prospecto **pide hablar con una persona**, pide precios o se niega a
seguir contestando, el bot llama a `alertar_asesor`, se apaga y se avisa **marcado como
incompleto** (con la lista de lo que falta). Se avisa siempre, tenga la fase la notificación
encendida o no — si no, acabaríamos de apagar el bot sin que nadie atienda al prospecto.

El bot **no** llama a `alertar_asesor` para cerrar. Cuando termina el cuestionario, el código lo
detecta solo.

---

## Clasificación

Dos ejes: **giro** (residencial / oficina) y **nivel** (baja / media / alta).

**Paso 1 — ¿cumple el umbral?** Reglas del cliente, en `leads.evaluar_prospecto()`:

| Tipo | Cumple el umbral si… |
|---|---|
| **Casa** | siempre |
| **Departamento** | 2 o más recámaras |
| **Oficina** | 100 m² o más, **o** 20 personas o más |

**Paso 2 — el nivel sale del plazo**, en `leads.fase_calificada()`:

| | Residencial | Oficina |
|---|---|---|
| cumple umbral **+ 12 meses o más** | Residencial Bueno | Oficina Bueno |
| cumple umbral **+ menos de 12 meses** | Residencial Normal | Oficina Mid |
| **no** cumple umbral (cualquier plazo) | Residencial Baja Prioridad | Oficina Baja Prioridad |

Como la casa siempre cumple el umbral, **una casa nunca cae en baja prioridad** — que es la regla
que dio el cliente.

**Mientras el cuestionario está a medias** el lead espera en la antesala de su giro
(`leads.fase_incompleta()`): Interesado Residencial o Interesado Oficina. Si todavía no sabemos el
giro, se queda en Nuevo — sin tipo de propiedad no hay carril que elegir.

**Fuera de la matriz:**
- **Preguntón** — el bot marcó `solo_informacion` porque la persona dijo que no busca rentar o se
  negó a contestar. Si después completa el cuestionario, mandan los datos y sale de ahí.
- **Descartado** — el bot registró un `motivo_perdida`.

**Ganado y Perdido NO son fases.** Cerrar una venta o perder un prospecto es un desenlace, no una
columna: se marcan con `es_venta` y `motivo_perdida`, y el lead se queda donde está. Mientras
tenga uno de los dos, el bot ya no lo mueve de fase.

Las oficinas además se separan por plazo (`tipo_oficina`, en `leads.TIPO_OFICINA_LBL`):

| Clave interna | Cómo se lee |
|---|---|
| `tipo1` | Oficina · renta corta (hasta 1 año) |
| `tipo2` | Oficina · renta larga (12 meses o más) |

> Las claves internas `tipo1`/`tipo2` **no** se cambian (romperían los datos existentes); lo que
> cambió es cómo se muestran, porque "Tipo 1 / Tipo 2" no le decía nada a nadie en EMA.

---

## Score de prioridad (0-100)

**Solo sirve para ordenar leads dentro de una columna.** No dispara alertas, no mueve fases y
nada automático depende de él. Vive en `leads.recompute_score_calif()` y sus tablas están como
constantes al inicio del módulo — para afinarlo, edita las tablas, no la lógica.

**El score y la fase pueden discrepar, a propósito.** Una casa de 1 recámara a 6 meses cae en
Residencial Bueno (regla de EMA) con score 35: le toca a un asesor, pero de último. La fase dice
*a quién le toca*; el score dice *a cuál atender primero*.

Mientras el cuestionario esté incompleto el score es **0** ("Sin clasificar").

### Tamaño — 45 puntos

| Oficina | pts | | Casa / Departamento | pts |
|---|---|---|---|---|
| ≥300 m² o ≥40 personas | 45 | | ≥4 recámaras | 45 |
| ≥150 m² o ≥25 personas | 36 | | 3 recámaras | 36 |
| ≥100 m² o ≥20 personas | 28 | | 2 recámaras | 27 |
| ≥50 m² o ≥10 personas | 15 | | 1 recámara | 12 |
| menor | 6 | | | |

### Plazo — 35 puntos
`12+` → 35 · `6-12` → 20 · `0-6` → 7

### Tipo de propiedad — 20 puntos
oficina → 20 · casa → 16 · departamento → 11

Máximo real 100, mínimo 24. Bandas del panel: **≥75 alta · 50-74 media · 24-49 baja · 0 sin
clasificar**.

---

## Columnas creadas a mano desde el panel

Una fase que el admin agrega desde el panel nace con `rol="custom"` y una clave derivada del
nombre, así que **el bot no puede rutear hacia ella** y los leads acaban todos en la fase de
entrada. Fue exactamente lo que pasó en producción: Nuevo acumulaba todo y las demás columnas
estaban en cero.

`fases._adoptar_columnas_manuales()` corrige eso al arrancar: empareja por nombre (ignorando
acentos y mayúsculas) contra `_ADOPTAR_POR_NOMBRE` y le asigna a cada columna su clave y su rol.
Si agregas una fase base con otro nombre, súmala a ese mapa.

## Qué pasa al entrar a una fase

Lo decide la **fase**, no el bot: cada una tiene sus toggles de Notificación y Recuperación, sus
destinatarios y su mensaje de cierre. Ver `app/routers/fases.py`.

## Dónde NO va la configuración del flujo

La pantalla de **Contexto** sirve para dos cosas: **datos** que el bot debe saber y **reglas** de
comportamiento. **No** sirve para mensajes que dependen de la clasificación — eso necesita ser
determinista y vive en el `mensaje_cierre` de cada fase.
