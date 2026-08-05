# Flujo del bot — EMA Rentals

> Se ve desde el panel de Vella en **Trabajo → 🔀 Flujos**.
>
> Este archivo vive aquí (no en el panel) para que se actualice en el mismo commit que
> cambia el comportamiento. Si tocas `prompt.py` o `leads.py`, tócalo también.
>
> El detalle de las reglas de clasificación está en [`flow-clasificacion.md`](./flow-clasificacion.md).

EMA Rentals **no agenda, no cotiza y no maneja propiedades**. Filtra leads y avisa a un asesor.

---

## Camino principal

```mermaid
flowchart TD
    WA[WhatsApp · Kapso] --> WH[POST /api/webhooks/kapso]
    IG[Instagram / Messenger · Sinch] --> WH2[POST /api/webhooks/sinch]

    WH --> DEB[debounce 2 s<br/>agrupa la ráfaga de mensajes]
    WH2 --> H
    DEB --> H[handler.handle_inbound<br/>corre en un hilo aparte]

    H --> GUARD{¿el bot debe contestar?}
    GUARD -->|marcado 'no es lead'| FIN1[calla]
    GUARD -->|un asesor tomó el chat| FIN2[calla]
    GUARD -->|bot global apagado| FIN3[calla]
    GUARD -->|sí| CTX[carga historial + contexto vivo del panel]

    CTX --> IA[GPT con 2 tools:<br/>capturar_lead · alertar_asesor]
    IA --> GUARDA[fact_guard<br/>bloquea cifras inventadas]
    GUARDA --> Q{¿cuestionario completo?}

    Q -->|falta algo| SIGUE[hace LA siguiente pregunta<br/>y deja el lead en la antesala]
    Q -->|completo| CLASIF[clasifica: giro + nivel]

    CLASIF --> FASE[lo mueve a su fase del Kanban]
    FASE --> ACC[la FASE decide qué se notifica<br/>y a quién]
    ACC --> OFF[apaga el bot: lo toma un asesor]

    SIGUE --> OUT[respuesta en máx. 2 burbujas]
    OFF --> OUT
```

**Regla de oro:** nada se clasifica, se notifica ni apaga el bot hasta que el cuestionario
esté completo. Lo decide `leads.cuestionario_completo()`, que es la única fuente de verdad.

---

## El cuestionario

```mermaid
flowchart TD
    P1{¿oficina, departamento o casa?}
    P1 -->|casa| RC[¿cuántas recámaras?]
    P1 -->|departamento| RD[¿cuántas recámaras?]
    P1 -->|oficina| RO[¿cuántos m², o cuántas personas?]
    RC --> P2
    RD --> P2
    RO --> P2
    P2{¿por cuánto tiempo?}
    P2 --> L1[6 meses o menos]
    P2 --> L2[de 6 a 12 meses]
    P2 --> L3[12 meses o más]
    L1 --> OK[cuestionario completo]
    L2 --> OK
    L3 --> OK
```

---

## Qué hace ante cada situación

```mermaid
flowchart TD
    S1[Pide hablar con una persona] --> A1[alertar_asesor · marcado INCOMPLETO<br/>se avisa aunque la fase tenga el aviso apagado]
    S2[Pregunta precios] --> A1
    S3[Se niega a seguir contestando] --> A1
    S4[Dice que solo quiere información] --> A2[queda como Preguntón<br/>sale de ahí si luego completa el cuestionario]
    S5[Manda una nota de voz] --> A3[se transcribe y sigue el flujo normal]
    S6[Escribe y ya está clasificado] --> A4[el bot está apagado: contesta el asesor]
    S7[Manda 3 mensajes seguidos] --> A5[el debounce los junta en UN turno]
```

La excepción de la regla de oro es S1/S2/S3: ahí se avisa **siempre**, marcado como
incompleto y con la lista de lo que faltó. Si no, se apagaría el bot sin que nadie atienda
al prospecto.

---

## Herramientas del bot

| Tool | Cuándo la llama | Qué escribe |
|---|---|---|
| `capturar_lead` | en cuanto sabe algo nuevo | tipo de propiedad, recámaras/m², plazo, nombre |
| `alertar_asesor` | piden humano, piden precio, o se niegan | avisa y apaga el bot, marcado incompleto |

El cierre normal **no** usa `alertar_asesor`: cuando el cuestionario termina, el código lo
detecta solo y la fase decide qué se notifica.
