"""Configuración por entorno. Sin defaults inseguros: los secretos vienen de env.

Regla de seguridad del proyecto (día 1): fail-fast si falta un secreto crítico en
producción; nada de `admin123` ni `CORS *` ni firma de webhook desactivada.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Núcleo ---
    database_url: str = "sqlite:///./emma.db"           # dev; en prod: Postgres por env
    secret_key: str = "dev-only-change-me"              # OBLIGATORIO override en prod
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    env: str = "dev"                                    # dev | prod

    # --- WhatsApp (Kapso, número PROPIO de EMA Rentals) ---
    kapso_api_key: str = ""
    kapso_phone_number_id: str = ""
    kapso_webhook_secret: str = ""                      # en prod SIEMPRE se verifica
    webhook_verify_token: str = "ema_webhook"

    # --- Sinch (Instagram + Messenger). WhatsApp sigue por Kapso. ---
    sinch_project_id: str = ""
    sinch_app_id: str = ""
    sinch_key_id: str = ""
    sinch_key_secret: str = ""
    sinch_region: str = "us"            # us | eu | br
    sinch_webhook_secret: str = ""      # para verificar la firma del webhook de Sinch

    # --- IA ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"                   # id VÁLIDO (no gpt-5.4)

    # --- Alerta de BUEN LEAD a los administradores ---
    # Teléfono(s) de los admins (52XXXXXXXXXX, coma-separados) que reciben el aviso por WhatsApp.
    alerta_admin_telefonos: str = ""
    alerta_admin_email: str = "admin@ema-rentals.com"  # respaldo por correo
    kapso_tpl_alerta_lead: str = "ema_alerta_lead"     # plantilla Kapso del aviso (0 variables o con vars)

    # --- Almacenamiento de media (Cloudflare R2) — opcional, para transcribir notas de voz ---
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_public_url: str = ""

    # --- Identidad / panel ---
    panel_url: str = "http://localhost:8000"
    public_base_url: str = "http://localhost:8000"
    # Números del dueño/coordinador (modo interno), normalizados 52XXXXXXXXXX, coma-separados.
    owner_phones: str = ""

    # --- Admin inicial (se crea al arrancar si no existe) ---
    admin_email: str = "admin@ema-rentals.local"
    admin_password: str = ""                            # vacío en prod => fail-fast
    admin_name: str = "Administrador"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


def validar_config() -> list[str]:
    """Puerta de activación: devuelve la lista de problemas de configuración.

    En prod, `main.py` aborta el arranque si esto no está vacío — así un tenant mal
    configurado (sin firma de webhook, con secret_key de dev, sin password admin) NUNCA
    se enciende. Patrón tomado de la aseguradora (validar_config).
    """
    problemas: list[str] = []
    if settings.env == "prod":
        if settings.secret_key == "dev-only-change-me":
            problemas.append("SECRET_KEY sigue en el default de dev")
        if not settings.kapso_webhook_secret:
            problemas.append("KAPSO_WEBHOOK_SECRET vacío: la firma del webhook no se puede verificar")
        if not settings.admin_password:
            problemas.append("ADMIN_PASSWORD vacío")
        if settings.database_url.startswith("sqlite"):
            problemas.append("DATABASE_URL apunta a SQLite en prod")
        if not settings.alerta_admin_telefonos and not settings.alerta_admin_email:
            problemas.append("Sin destino de alerta (ALERTA_ADMIN_TELEFONOS/EMAIL): no se avisará de buenos leads")
        # Sinch es opcional: si faltan credenciales, IG/Messenger simplemente no atienden (WhatsApp sí).
    return problemas
