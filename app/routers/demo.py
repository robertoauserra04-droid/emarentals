"""Datos de demostración: siembra 30 leads con conversaciones realistas (formal, sin emojis)
para ver cómo se vería el panel. Todo se marca `es_demo=True` y se puede borrar de un golpe.

Endpoints (solo admin):
  POST   /api/demo/seed   → inserta los 30 leads + conversaciones (idempotente: limpia antes)
  DELETE /api/demo        → borra todo lo marcado como demo
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.lead import EmaLead
from app.models.messaging import ChatMessage, Conversation, MessageDirection
from app.models.user import User
from app.services import auth
from app.services.bot import leads as leads_svc

router = APIRouter(prefix="/api/demo", tags=["demo"])

_IN = MessageDirection.inbound
_OUT = MessageDirection.outbound

# (nombre, canal, segmento, necesidad, plazo, fecha_entrega, zona, estado, nivel, presupuesto, resumen)
_LEADS = [
    ("Regina Salazar", "whatsapp", "corporativo", "oficina_completa", 24, "1-4sem", "Monterrey", "calificado", "Alto", "por definir", "Amueblar 40 estaciones para torre corporativa nueva"),
    ("Grupo Altavista", "whatsapp", "corporativo", "oficina_completa", 18, "ya", "CDMX", "asignado", "Alto", "por definir", "Proyecto corporativo, 3 pisos, entrega urgente"),
    ("Airbnb Valle Oriente", "instagram", "airbnb", "casa_completa", 12, "1-4sem", "Monterrey", "calificado", "Alto", "5-8 mil/mes", "Amueblar depa completo para renta vacacional"),
    ("Fernanda Múzquiz", "whatsapp", "residencial", "casa_completa", 18, "1-4sem", "San Pedro", "calificado", "Alto", "por definir", "Casa completa por año y medio, se muda por trabajo"),
    ("Hostal Barrio Antiguo", "messenger", "airbnb", "paquete", 12, "1-4sem", "Monterrey", "asignado", "Alto", "por definir", "6 habitaciones para hospedaje, camas y línea blanca"),
    ("Diego Treviño", "whatsapp", "residencial", "paquete", 12, "ya", "Guadalupe", "calificado", "Alto", "3-4 mil/mes", "Sala, comedor y refri por un año"),
    ("Consultoría Nexus", "instagram", "corporativo", "oficina_completa", 24, ">1mes", "Querétaro", "calificado", "Medio", "por definir", "Oficina nueva, planean amueblar en 2 meses"),
    ("Mariana Cázares", "whatsapp", "residencial", "casa_completa", 12, "1-4sem", "Monterrey", "asignado", "Alto", "por definir", "Recién casada, casa completa por un año"),
    ("Airbnb Cumbres", "instagram", "airbnb", "paquete", 12, "1-4sem", "Monterrey", "calificado", "Alto", "por definir", "Dos propiedades para renta corta"),
    ("Renata Villarreal", "whatsapp", "residencial", "paquete", 6, "1-4sem", "Apodaca", "interesado", "Medio", None, "Pregunta por sala y recámara, aún viendo tiempos"),
    ("Oscar Lozano", "messenger", "oficina", "oficina_completa", 12, ">1mes", "Monterrey", "interesado", "Medio", None, "Oficina chica, 6 escritorios, evaluando"),
    ("Paulina Garza", "whatsapp", "residencial", "pieza_suelta", 6, "1-4sem", "Monterrey", "interesado", "Medio", None, "Quiere una lavadora y secadora por medio año"),
    ("Sofía Menchaca", "instagram", "residencial", "pieza_suelta", 3, "ya", "San Nicolás", "interesado", "Bajo", None, "Solo un refrigerador por unos meses"),
    ("Andrés Fuentes", "whatsapp", "residencial", "paquete", 6, ">1mes", "Santa Catarina", "interesado", "Medio", None, "Sala y comedor, todavía calculando presupuesto"),
    ("Valeria Cantú", "messenger", "residencial", "pieza_suelta", 3, "1-4sem", "Monterrey", "interesado", "Bajo", None, "Pregunta por una cama individual"),
    ("Rodrigo Elizondo", "whatsapp", "oficina", "paquete", 6, ">1mes", "Monterrey", "interesado", "Medio", None, "Sala de espera para su despacho"),
    ("Carla Benavides", "instagram", "residencial", None, None, None, "Monterrey", "nuevo", None, None, None),
    ("Emilio Zúñiga", "whatsapp", "residencial", None, None, None, None, "nuevo", None, None, None),
    ("Daniela Ríos", "messenger", "residencial", None, None, None, "Monterrey", "nuevo", None, None, None),
    ("Jorge Maldonado", "whatsapp", None, None, None, None, None, "nuevo", None, None, None),
    ("Ximena Peña", "instagram", "airbnb", None, None, None, "Cancún", "nuevo", None, None, None),
    ("Luis Carrillo", "whatsapp", "residencial", "pieza_suelta", 3, "ya", "Monterrey", "nuevo", None, None, None),
    ("Adriana Solís", "whatsapp", "residencial", "casa_completa", 12, "1-4sem", "Monterrey", "ganado", "Alto", "por definir", "Cerró casa completa por un año"),
    ("Torre Insignia", "instagram", "corporativo", "oficina_completa", 24, "1-4sem", "Monterrey", "ganado", "Alto", "por definir", "Contrato corporativo cerrado, 2 pisos"),
    ("Airbnb Centro MTY", "messenger", "airbnb", "paquete", 12, "ya", "Monterrey", "ganado", "Alto", "por definir", "Cerró amueblado de 4 unidades"),
    ("Héctor Nava", "whatsapp", "residencial", "pieza_suelta", 3, ">1mes", "Escobedo", "perdido", "Bajo", None, "Solo comparaba precios, no avanzó"),
    ("Gabriela Ponce", "instagram", "residencial", "pieza_suelta", 3, ">1mes", "Monterrey", "perdido", "Bajo", None, "Buscaba comprar, no rentar"),
    ("Iván Robledo", "messenger", "residencial", "paquete", 6, ">1mes", "Juárez", "perdido", "Medio", None, "Se enfrió, dejó de contestar"),
    ("Natalia Ávila", "whatsapp", "oficina", "oficina_completa", 12, "1-4sem", "Monterrey", "calificado", "Alto", "por definir", "Coworking nuevo, 20 lugares"),
    ("Bruno Salinas", "whatsapp", "residencial", "casa_completa", 18, "1-4sem", "San Pedro", "calificado", "Alto", "por definir", "Casa completa por año y medio, mudanza por trabajo"),
]

_SEG_TXT = {"residencial": "mi casa", "oficina": "mi oficina", "airbnb": "una propiedad de Airbnb",
            "corporativo": "un proyecto corporativo"}
_NEC_TXT = {"pieza_suelta": "una pieza", "paquete": "un paquete de muebles",
            "casa_completa": "amueblar toda la casa", "oficina_completa": "amueblar la oficina completa"}


def _conversacion(l: dict) -> list[tuple]:
    """Arma una conversación realista según la etapa. (direction, body)."""
    nombre = l["name"].split(" ")[0]
    seg = _SEG_TXT.get(l["segmento"] or "", "un espacio")
    nec = _NEC_TXT.get(l["necesidad"] or "", "amueblar")
    zona = l["zona"] or "mi zona"
    plazo = l["plazo_meses"]
    estado = l["estado"]

    saludo_in = f"Hola, buenas tardes. Me interesa rentar muebles para {seg}."
    presenta = "Hola, le saluda Ana de EMA Rentals. Con gusto le ayudo. ¿Para qué espacio está buscando amueblar?"

    if estado == "nuevo":
        return [(_IN, saludo_in), (_OUT, presenta)]

    base = [
        (_IN, saludo_in),
        (_OUT, presenta),
        (_IN, f"Es para {seg}. Quisiera {nec}."),
        (_OUT, "Con mucho gusto. ¿Por cuánto tiempo lo necesitaría? Nuestros planes van de 3 a 24 meses."),
    ]
    if plazo:
        base.append((_IN, f"Lo necesito por unos {plazo} meses."))
        base.append((_OUT, f"Perfecto. ¿En qué zona sería la entrega? Estamos en Monterrey y damos servicio en todo México."))
        base.append((_IN, f"En {zona}."))

    if estado in ("interesado",):
        base.append((_OUT, "Muy bien. ¿Para cuándo le gustaría tener la entrega? Con eso le preparo la mejor opción."))
        return base

    if estado in ("calificado", "asignado", "ganado"):
        base.append((_OUT, "Excelente, con esos datos ya tengo una idea clara de lo que necesita. "
                           "Con gusto le paso con un asesor que le prepara la propuesta a la medida."))
        if estado in ("asignado", "ganado"):
            base.append((_OUT, f"Buen día {nombre}, le saluda un asesor de EMA Rentals. Ya vi su solicitud, "
                               "con gusto le comparto la propuesta y resolvemos cualquier duda."))
        if estado == "ganado":
            base.append((_IN, "Me parece muy bien, quedo atento para cerrar."))
        return base

    if estado == "perdido":
        base.append((_OUT, "Con gusto le preparo una propuesta. ¿Para cuándo la necesitaría?"))
        base.append((_IN, "Déjame lo checo y te aviso, gracias."))
        return base
    return base


def _limpiar_demo(db: Session) -> int:
    demo = db.query(EmaLead).filter(EmaLead.es_demo == True).all()  # noqa: E712
    n = len(demo)
    phones = [l.phone for l in demo]
    if phones:
        db.query(ChatMessage).filter(ChatMessage.phone.in_(phones)).delete(synchronize_session=False)
        db.query(Conversation).filter(Conversation.phone.in_(phones)).delete(synchronize_session=False)
        db.query(EmaLead).filter(EmaLead.es_demo == True).delete(synchronize_session=False)  # noqa: E712
    db.commit()
    return n


@router.post("/seed")
def seed(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    """Siembra 30 leads demo con conversaciones. Idempotente (limpia los demo previos)."""
    _limpiar_demo(db)
    ahora = datetime.now(timezone.utc)
    creados = 0
    for i, row in enumerate(_LEADS):
        (nombre, canal, seg, nec, plazo, fecha, zona, estado, nivel, presu, resumen) = row
        phone = f"52818{2000000 + i:07d}"   # teléfonos demo, no colisionan con reales
        l = EmaLead(
            phone=phone, name=nombre, source=canal, segmento=seg, necesidad=nec,
            plazo_meses=plazo, fecha_entrega=fecha, zona=zona, estado=estado,
            nivel_interes=nivel, presupuesto=presu, resumen=resumen, es_demo=True,
            message_count=0, bot_active=(estado not in ("asignado", "ganado")),
            last_message_at=ahora - timedelta(hours=i * 3),
        )
        if estado in ("calificado", "asignado", "ganado"):
            l.escalated = True
            l.alertado_at = ahora - timedelta(hours=i * 3, minutes=5)
        leads_svc.recompute_score_calif(l)
        db.add(l)

        # Conversación + bandeja
        conv = _conversacion({"name": nombre, "segmento": seg, "necesidad": nec,
                              "zona": zona, "plazo_meses": plazo, "estado": estado})
        db.add(Conversation(phone=phone, name=nombre, channel=canal,
                            bot_active=(estado not in ("asignado", "ganado")),
                            last_message_at=ahora - timedelta(hours=i * 3)))
        t0 = ahora - timedelta(hours=i * 3, minutes=len(conv) * 2)
        for j, (direction, body) in enumerate(conv):
            db.add(ChatMessage(phone=phone, channel=canal, direction=direction, body=body,
                               created_at=t0 + timedelta(minutes=j * 2)))
        l.message_count = sum(1 for d, _ in conv if d == _IN)
        creados += 1
    db.commit()
    return {"ok": True, "creados": creados}


@router.delete("")
def reset(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    """Borra todos los datos de demostración."""
    n = _limpiar_demo(db)
    return {"ok": True, "borrados": n}
