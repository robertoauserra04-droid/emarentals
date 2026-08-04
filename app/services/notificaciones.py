"""Alerta al directorio de contactos cuando un lead cae en una fase que notifica.

La FASE decide SI se avisa (su toggle de Notificación); a quién se le avisa es una lista global
(`ContactoAlerta`), la misma para todas las fases. Si el directorio está vacío se cae a
`ALERTA_ADMIN_TELEFONOS` del .env, para no dejar el aviso sin destino.

Cadena de respaldo (patrón portado de psicologia): plantilla Kapso → texto (si hay ventana 24h)
→ email → log. Así el aviso no muere en silencio si una plantilla no está aprobada.

NO es una conversación con prospecto: se manda con db=None (no se persiste en la bandeja).
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def _telefonos_admin() -> list[str]:
    return [t.strip() for t in (settings.alerta_admin_telefonos or "").split(",") if t.strip()]


def _destinos(db) -> list[str]:
    """El directorio de contactos; si está vacío, los teléfonos del .env."""
    if db is not None:
        from app.routers.fases import telefonos_alerta
        contactos = telefonos_alerta(db)
        if contactos:
            return contactos
        logger.warning("[alerta] el directorio de contactos está vacío: se usa "
                       "ALERTA_ADMIN_TELEFONOS")
    return _telefonos_admin()


def _resumen_lead(lead, incompleto: bool = False, fase=None) -> str:
    """Texto legible del lead para el aviso al asesor."""
    from app.services.bot import leads as leads_svc

    canal = {"whatsapp": "WhatsApp", "instagram": "Instagram",
             "messenger": "Messenger"}.get(lead.source or "whatsapp", lead.source)
    tipo = {"oficina": "Oficina", "departamento": "Departamento",
            "casa": "Casa"}.get(lead.tipo_propiedad or "", lead.tipo_propiedad or "sin definir")
    if incompleto:
        falta = leads_svc.falta_del_cuestionario(lead)
        encabezado = ("Pidió hablar con un asesor — cuestionario INCOMPLETO"
                      + (f", falta: {', '.join(falta)}" if falta else ""))
    else:
        encabezado = f"Nuevo prospecto calificado por {canal}"
    partes = [
        encabezado,
        f"Nombre: {lead.name or 'sin nombre'}",
        f"Contacto: {lead.phone}",
        f"Canal: {canal}",
        f"Tipo: {tipo}",
    ]
    # La fase va en el texto libre y no en la plantilla de WhatsApp: meterla en la plantilla
    # obligaría a reeditarla y reaprobarla en Meta.
    if fase is not None:
        partes.append(f"Clasificación: {fase.nombre}")
    if lead.tipo_propiedad == "oficina":
        det = []
        if lead.oficina_m2:
            det.append(f"{lead.oficina_m2} m²")
        if lead.oficina_personas:
            det.append(f"{lead.oficina_personas} personas")
        if det:
            partes.append("Oficina: " + ", ".join(det))
        if lead.tipo_oficina:
            partes.append("Categoría: " + leads_svc.TIPO_OFICINA_LBL.get(
                lead.tipo_oficina, lead.tipo_oficina))
    elif lead.recamaras is not None:
        partes.append(f"Recámaras: {lead.recamaras}")
    if lead.tiempo_renta:
        t = {"0-6": "6 meses o menos", "6-12": "6 a 12 meses", "12+": "12 meses o más"}
        partes.append(f"Tiempo: {t.get(lead.tiempo_renta, lead.tiempo_renta)}")
    if lead.zona:
        partes.append(f"Zona: {lead.zona}")
    if lead.resumen:
        partes.append(f"Resumen: {lead.resumen}")
    d = leads_svc.desglose_score(lead)
    if d["completo"]:
        partes.append(f"Prioridad: {d['total']}/100 "
                      f"(tamaño {d['tamano']} · plazo {d['plazo']} · tipo {d['tipo']})")
    return "\n".join(partes)


def alertar_admin(lead, db=None, fase=None, incompleto: bool = False) -> bool:
    """Avisa al directorio de contactos. Devuelve True si al menos un canal salió.

    Idempotencia: el caller (`fases_acciones`) marca `lead.alertado_at` para no repetir el aviso.
    `incompleto=True` = el prospecto pidió un asesor sin terminar el cuestionario.
    """
    telefonos = _destinos(db)
    resumen = _resumen_lead(lead, incompleto=incompleto, fase=fase)
    algun_envio = False

    for tel in telefonos:
        # 1) Plantilla Kapso (funciona aunque el admin no haya escrito en 24h).
        enviado = None
        try:
            from app.services import kapso
            tipo = {"oficina": "Oficina", "departamento": "Departamento",
                    "casa": "Casa"}.get(lead.tipo_propiedad or "", "propiedad")
            enviado = kapso.send_template_vars_sync(
                tel, settings.kapso_tpl_alerta_lead,
                [lead.name or lead.phone, tipo, str(lead.score_calif or 0)])
        except Exception as e:  # noqa: BLE001
            logger.warning("[alerta] plantilla a %s falló: %s", tel, e)
        # 2) Fallback a texto libre (si hay ventana de 24h abierta con el admin).
        if not enviado:
            try:
                from app.services import kapso
                enviado = kapso.send_text_sync(tel, resumen)
            except Exception as e:  # noqa: BLE001
                logger.warning("[alerta] texto a %s falló: %s", tel, e)
        algun_envio = algun_envio or bool(enviado)

    # 3) Respaldo por email (best-effort; si no hay SMTP configurado, solo loguea).
    if settings.alerta_admin_email:
        try:
            _enviar_email(settings.alerta_admin_email,
                          f"[EMA Rentals] Nuevo lead calificado: {lead.name or lead.phone}", resumen)
            algun_envio = True
        except Exception as e:  # noqa: BLE001
            logger.warning("[alerta] email falló: %s", e)

    if not algun_envio:
        # 4) Última red: queda en el log para no perder el aviso.
        logger.warning("[alerta] BUEN LEAD sin canal de aviso disponible:\n%s", resumen)
    return algun_envio


def _enviar_email(destino: str, asunto: str, cuerpo: str) -> None:
    """Envío de correo por SMTP si está configurado por env; si no, no-op con log.

    Mantiene el respaldo simple: EMA puede conectar SMTP más adelante sin tocar el flujo.
    """
    import os
    host = os.getenv("SMTP_HOST")
    if not host:
        logger.info("[alerta] SMTP no configurado; email a %s omitido (solo log)", destino)
        return
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(cuerpo)
    msg["Subject"] = asunto
    msg["From"] = os.getenv("SMTP_FROM", "no-reply@ema-rentals.com")
    msg["To"] = destino
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        if os.getenv("SMTP_USER"):
            s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS", ""))
        s.send_message(msg)
