"""Cinturón de robustez del webhook (portado de bienesraicesEnrique): eco de coexistencia + dedup wamid."""
from app.routers import webhook


def _payload(msg: dict, contacts=None):
    val = {"messages": [msg]}
    if contacts:
        val["contacts"] = contacts
    return {"entry": [{"changes": [{"value": val}]}]}


def test_extraer_inbound_normal():
    d = webhook._extraer(_payload(
        {"id": "wamid.1", "from": "5218110000001", "type": "text", "text": {"body": "hola"}}))
    assert d["direccion"] == "inbound"
    assert d["phone"] == "5218110000001" and d["text"] == "hola" and d["wamid"] == "wamid.1"


def test_eco_por_from_me():
    assert webhook._es_eco_saliente({"from_me": True, "to": "521"}) is True


def test_eco_por_direction_outbound():
    assert webhook._es_eco_saliente({"direction": "outbound", "to": "521"}) is True


def test_extraer_detecta_eco_saliente():
    d = webhook._extraer(_payload(
        {"id": "wamid.2", "echo": True, "to": "5218110000009", "type": "text",
         "text": {"body": "te atiendo yo"}}))
    assert d["direccion"] == "outbound"
    assert d["telefono_destino"] == "5218110000009" and d["wamid"] == "wamid.2"


def test_statuses_se_ignora():
    assert webhook._extraer({"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}) is None


def test_mensaje_normal_no_es_eco():
    assert webhook._es_eco_saliente(
        {"from": "5218110000001", "type": "text", "text": {"body": "hola"}}) is False


# ─────────── Formato Kapso v2 (el real de EMA) ───────────

def _kapso(msg, conv=None):
    return {"message": msg, "conversation": conv or {"phone_number": "+5218110000001"}}


def test_kapso_inbound():
    d = webhook._extraer(_kapso(
        {"id": "w1", "type": "text", "text": {"body": "hola"}, "kapso": {"direction": "inbound"}},
        {"phone_number": "+5218110000001", "contact_name": "Ana"}))
    assert d["direccion"] == "inbound" and d["phone"] == "5218110000001"
    assert d["text"] == "hola" and d["name"] == "Ana" and d["wamid"] == "w1"


def test_kapso_envuelto_en_data():
    d = webhook._extraer({"data": _kapso(
        {"id": "w2", "text": {"body": "hey"}, "kapso": {"direction": "inbound"}})})
    assert d["direccion"] == "inbound" and d["text"] == "hey"


def test_kapso_eco_bot_se_ignora():
    assert webhook._extraer(_kapso(
        {"id": "w3", "text": {"body": "eco"}, "kapso": {"direction": "outbound", "origin": "cloud_api"}})) is None


def test_kapso_humano_pausa_bot():
    d = webhook._extraer(_kapso(
        {"id": "w4", "text": {"body": "yo te atiendo"}, "kapso": {"direction": "outbound", "origin": "business_app"}}))
    assert d["direccion"] == "outbound" and d["telefono_destino"] == "5218110000001"
