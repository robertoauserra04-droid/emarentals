"""EmaLead — prospecto de EMA Rentals (clon simplificado de MarketingLead de Bell).

Filtro de leads: el bot conversa por WhatsApp/Instagram/Messenger, clasifica al prospecto y,
cuando es un BUEN LEAD (ticket + volumen + plazo largo), avisa a los administradores. NO hay
finanzas, agenda, propiedades, asesores (round-robin) ni campañas: esto es solo filtrar leads.

Campos de dominio (muebles): segmento, necesidad, plazo_meses, fecha_entrega, zona, presupuesto.
Conserva el aparato de recuperación (score, recovery_*) pero APAGADO por config (recovery off v1).
"""
from sqlalchemy import (Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint,
                        ForeignKey)
from sqlalchemy.sql import func

from app.database import Base

# Claves de las fases base. La fuente de verdad es la tabla `fases` (el admin agrega y quita
# columnas), así que esto es solo referencia — no se valida contra ello en runtime.
# Ganado y Perdido NO están: son desenlaces (`es_venta` / `motivo_perdida`), no columnas.
ESTADOS_VALIDOS = {"nuevo", "descartado", "pregunton",
                   "interesado_residencial", "interesado_oficina",
                   "residencial_baja", "oficina_baja",
                   "residencial_normal", "oficina_mid",
                   "residencial_bueno", "oficina_bueno"}


class AppSetting(Base):
    __tablename__ = "app_settings"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


class EmaLead(Base):
    """Persona que escribe a EMA Rentals por cualquiera de los 3 canales (prospecto)."""
    __tablename__ = "ema_leads"

    id            = Column(Integer, primary_key=True)
    phone         = Column(String, unique=True, index=True, nullable=False)  # o PSID de IG/Messenger
    name          = Column(String, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    message_count = Column(Integer, default=0)

    # Control del bot / coexistencia con asesores (auto-pausa cuando el humano entra)
    bot_active          = Column(Boolean, default=True)
    source              = Column(String, default="whatsapp")  # whatsapp | instagram | messenger
    escalated           = Column(Boolean, default=False)
    escalation_resolved = Column(Boolean, default=False)

    # Clasificación conversacional (la llena la IA con `capturar_lead`)
    nivel_interes  = Column(String, nullable=True)     # Alto / Medio / Bajo
    estado         = Column(String, default="nuevo")   # ver ESTADOS_VALIDOS
    que_pregunto   = Column(Text, nullable=True)
    resumen        = Column(Text, nullable=True)
    motivo_perdida = Column(Text, nullable=True)

    # --- Marca / modelo comercial (EMA Rentals renta de muebles · EMA Office mobiliario de oficina) ---
    marca         = Column(String, nullable=True)   # rentals (muebles casa/Airbnb) / office (oficina/corporativo)
    modelo        = Column(String, nullable=True)   # renta / venta (EMA Office hace ambos; Rentals solo renta)

    # --- Flujo de calificación del bot (renta) ---
    tipo_propiedad   = Column(String, nullable=True)   # oficina / departamento / casa
    recamaras        = Column(Integer, nullable=True)  # depto/casa: nº de recámaras
    oficina_m2       = Column(Integer, nullable=True)  # oficina: m² aproximados
    oficina_personas = Column(Integer, nullable=True)  # oficina: personas que trabajarán
    tiempo_renta     = Column(String, nullable=True)   # 0-6 / 6-12 / 12+ (meses)
    tipo_oficina     = Column(String, nullable=True)   # tipo1 (0-12m) / tipo2 (12+m) — solo oficinas
    es_buen_prospecto = Column(Boolean, default=False) # lo calcula el bot al filtrar

    # --- Dominio previo (se conserva; segmento se deriva de tipo_propiedad) ---
    segmento      = Column(String, nullable=True)   # residencial / oficina / airbnb / corporativo
    necesidad     = Column(String, nullable=True)   # pieza_suelta / paquete / casa_completa / oficina_completa
    plazo_meses   = Column(Integer, nullable=True)  # (heredado)
    fecha_entrega = Column(String, nullable=True)   # ya / 1-4sem / >1mes (texto)
    zona          = Column(String, nullable=True)   # Monterrey / ciudad-estado (texto libre)
    presupuesto   = Column(String, nullable=True)   # rango declarado (texto)

    # --- Perfil estratégico (cuadrante 2×2 Ticket × Uso) — se calcula solo al guardar ---
    ticket_mensual   = Column(Integer, nullable=True)   # MXN/mes (0 si es venta puntual sin mensualidad)
    uso              = Column(String, nullable=True)    # reventa (para sus clientes/unidades) / propio
    potencial_escala = Column(String, nullable=True)    # 1-3 / 4-6 / 7-15 / +15 (unidades potenciales)
    urgencia_cierre  = Column(String, nullable=True)    # ya / media / baja
    estructura       = Column(String, nullable=True)    # persona_fisica / empresa (informativo)
    perfil           = Column(String, nullable=True)    # socio_estrategico / aliado_operativo / cliente_premium / cliente_estandar / sin_clasificar
    horas_estimadas  = Column(Integer, nullable=True)   # horas/semana estimadas

    # Alerta al admin: se dispara UNA sola vez al calificar (idempotencia).
    alertado_at   = Column(DateTime(timezone=True), nullable=True)

    # Conversión (la marca el asesor a mano en el panel; sin finanzas)
    es_venta     = Column(Boolean, default=False)
    monto_cierre = Column(String, nullable=True)     # texto libre, informativo
    es_demo      = Column(Boolean, default=False)    # dato sembrado de demostración (borrable)

    last_message_at = Column(DateTime(timezone=True), nullable=True)
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Calificación COMERCIAL determinista (0-100, se ve subir por fase en el Kanban).
    score_calif = Column(Integer, default=0)

    # Recuperación AUTOMÁTICA (reuso de Bell) — presente pero APAGADA por config en v1.
    score             = Column(Integer, default=0)
    score_updated_at  = Column(DateTime(timezone=True), nullable=True)
    recovery_attempts = Column(Integer, default=0)
    recovery_cycle    = Column(Integer, default=0)
    next_recovery_at  = Column(DateTime(timezone=True), nullable=True)
    last_recovery_at  = Column(DateTime(timezone=True), nullable=True)
    opted_out         = Column(Boolean, default=False)
    recovery_paused   = Column(Boolean, default=False)

    last_reactivation_at = Column(DateTime(timezone=True), nullable=True)
    reactivation_count   = Column(Integer, default=0)

    timezone = Column(String, nullable=True)   # IANA; NULL = default MX

    # Clave de la fase cuyo mensaje de cierre ya se envió: evita repetirlo en cada turno.
    cierre_enviado_en = Column(String, nullable=True)

    # Nota: sacar un lead del tablero lo BORRA con todo su rastro (`app/services/purga.py`), que es
    # la única excepción a la política de sin DELETE físico del proyecto (ver app/routers/usuarios.py).
    # Las columnas `oculto`/`oculto_at` de la vieja sección Historial ya no existen en el modelo;
    # si quedaron en una base vieja, se ignoran.


class Fase(Base):
    """Fase del pipeline (columna del Kanban). Configurable: el admin renombra, recolora, reordena
    y agrega/quita columnas. La `clave` es ESTABLE (el bot y el código la usan); el `nombre` es la
    etiqueta visible que se puede cambiar sin romper nada. `rol` fija el papel de cada fase base
    (entrada, pregunton, descartado, interesado_*, baja_*, normal_*/mid_*, bueno_*) y es lo que usa
    `resolver_clave` para el fallback; `custom` es una columna extra que agregó el admin."""
    __tablename__ = "fases"

    id         = Column(Integer, primary_key=True)
    clave      = Column(String, unique=True, nullable=False)
    nombre     = Column(String, nullable=False)
    color      = Column(String, default="#8c725d")
    orden      = Column(Integer, default=0)
    rol        = Column(String, default="custom")   # entrada|pipeline|lowpri|bueno_*|ganado|perdido|custom
    activa     = Column(Boolean, default=True)
    descripcion = Column(Text, nullable=True)   # qué es la fase (editable)
    criterios   = Column(Text, nullable=True)   # qué debe pasar / tener el lead para estar aquí (editable)

    # ── Qué pasa cuando un lead cae aquí. La FASE manda: antes esto estaba hardcodeado dentro
    #    del bot (`es_buen_prospecto`) y no se podía configurar sin tocar código.
    notificar      = Column(Boolean, default=False)  # avisar al directorio de contactos
    recuperar      = Column(Boolean, default=False)  # incluir en el motor de recuperación (aún sin motor)
    mensaje_cierre = Column(Text, nullable=True)     # lo manda el CÓDIGO al entrar el lead, no el modelo


class ContactoNoLead(Base):
    """Teléfonos que NO se tratan como prospectos: proveedores, el número del dueño, spam.

    Es una tabla propia y no una bandera en el lead, a propósito: así la marca sobrevive a que se
    oculte, se reinicie o se borre el lead. En aseguradora esto es por conversación y por eso el
    contacto reaparece cuando se abre un caso nuevo.

    Efectos: el bot no le contesta, sale del Kanban/Bandeja/Métricas/Resumen, pero su conversación
    se sigue guardando y se ve en Historial. Reversible.
    """
    __tablename__ = "contactos_no_lead"

    id         = Column(Integer, primary_key=True)
    telefono   = Column(String, unique=True, index=True, nullable=False)
    motivo     = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ContactoAlerta(Base):
    """Directorio de a quién se le avisa. Es una lista GLOBAL: cuando un lead cae en una fase con
    la notificación encendida, el aviso va a todos los contactos activos de aquí."""
    __tablename__ = "contactos_alerta"

    id       = Column(Integer, primary_key=True)
    nombre   = Column(String, nullable=False)
    telefono = Column(String, unique=True, nullable=False)
    activo   = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())



class ContextoBot(Base):
    """Contexto que alimenta al bot. Lo escribe el equipo desde el panel; el bot lo lee en cada
    turno e incorpora los activos a su system prompt (grounding). Editable en caliente."""
    __tablename__ = "contexto_bot"

    id         = Column(Integer, primary_key=True)
    titulo     = Column(String, nullable=False)
    contenido  = Column(Text, nullable=False)
    # "dato"  = información del negocio (precios, cobertura, plazos) → el bot la usa para responder.
    # "regla" = cómo debe comportarse → entra junto a las reglas duras, con peso de instrucción.
    tipo       = Column(String, default="dato")
    activo     = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RecoveryEvent(Base):
    """Cada intento de recuperación (auto o manual). Idempotencia dura por el unique."""
    __tablename__ = "recovery_events"

    id            = Column(Integer, primary_key=True)
    lead_id       = Column(Integer, ForeignKey("ema_leads.id"), index=True)
    phone         = Column(String, index=True)
    cycle         = Column(Integer, default=0)
    attempt_no    = Column(Integer, nullable=False)
    template_name = Column(String, nullable=True)
    status        = Column(String, default="pending")   # pending·sent·failed·dry_run
    via           = Column(String, default="auto")      # auto·manual
    error         = Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    sent_at       = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("lead_id", "cycle", "attempt_no", name="ux_recovery_lead_cycle_attempt"),
    )
