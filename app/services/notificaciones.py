"""Alerta a los administradores de EMA Rentals cuando entra un BUEN LEAD.

Patrón portado de psicologia (`bot/handler._alertar_psicologa` → `notificaciones.enviar`):
cadena de respaldo plantilla Kapso → texto (si hay ventana 24h) → email → log. Así el aviso no
muere en silencio si una plantilla no está aprobada.

NO es una conversación con prospecto: se manda con db=None (no se persiste en la bandeja).
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def _telefonos_admin() -> list[str]:
    return [t.strip() for t in (settings.alerta_admin_telefonos or "").split(",") if t.strip()]


def _resumen_lead(lead) -> str:
    """Texto legible del lead para el aviso al asesor."""
    canal = {"whatsapp": "WhatsApp", "instagram": "Instagram",
             "messenger": "Messenger"}.get(lead.source or "whatsapp", lead.source)
    partes = [
        f"Nuevo prospecto calificado por {canal}",
        f"Nombre: {lead.name or 'sin nombre'}",
        f"Contacto: {lead.phone}",
    ]
    if lead.segmento:
        partes.append(f"Segmento: {lead.segmento}")
    if lead.necesidad:
        partes.append(f"Necesidad: {lead.necesidad.replace('_', ' ')}")
    if lead.plazo_meses:
        partes.append(f"Plazo: {lead.plazo_meses} meses")
    if lead.fecha_entrega:
        partes.append(f"Entrega: {lead.fecha_entrega}")
    if lead.zona:
        partes.append(f"Zona: {lead.zona}")
    if lead.presupuesto:
        partes.append(f"Presupuesto: {lead.presupuesto}")
    if lead.resumen:
        partes.append(f"Resumen: {lead.resumen}")
    partes.append(f"Score: {lead.score_calif or 0}/100")
    return "\n".join(partes)


def alertar_admin(lead) -> bool:
    """Avisa a los administradores de un buen lead. Devuelve True si al menos un canal salió.

    Idempotencia: el caller (handler) marca `lead.alertado_at` para no repetir el aviso.
    """
    telefonos = _telefonos_admin()
    resumen = _resumen_lead(lead)
    algun_envio = False

    for tel in telefonos:
        # 1) Plantilla Kapso (funciona aunque el admin no haya escrito en 24h).
        enviado = None
        try:
            from app.services import kapso
            enviado = kapso.send_template_vars_sync(
                tel, settings.kapso_tpl_alerta_lead,
                [lead.name or lead.phone, lead.segmento or "s/segmento", str(lead.score_calif or 0)])
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
