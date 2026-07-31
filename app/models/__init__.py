"""Import central de modelos para que Base.metadata los conozca (create_all / Alembic)."""
from app.models.lead import EmaLead, AppSetting, RecoveryEvent, ContextoBot, Fase  # noqa: F401
from app.models.lead_evento import LeadEvento, LeadNota  # noqa: F401
from app.models.messaging import Conversation, ChatMessage, MessageDirection  # noqa: F401
from app.models.user import User  # noqa: F401
