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
