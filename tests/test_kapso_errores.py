"""Port del fix de bienesraicesEnrique: el 400 de Kapso debe exponer el motivo real de Meta."""
import pytest

from app.services import kapso


class _Resp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def json(self):
        return {}


class _Client:
    """httpx.Client falso: devuelve la respuesta fijada al hacer .post()."""
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, *a, **k):
        return self._resp


def test_400_expone_el_motivo_de_meta(monkeypatch):
    """send_text_sync debe lanzar KapsoSendError con el code de Meta, no un '400' pelón."""
    monkeypatch.setattr(kapso.settings, "kapso_api_key", "k", raising=False)
    monkeypatch.setattr(kapso.settings, "kapso_phone_number_id", "123", raising=False)
    cuerpo = '{"error":{"code":131047,"message":"Re-engagement message"}}'
    monkeypatch.setattr(kapso.httpx, "Client", lambda *a, **k: _Client(_Resp(400, cuerpo)))
    with pytest.raises(kapso.KapsoSendError) as ei:
        kapso.send_text_sync("526672341632", "hola")
    assert "131047" in str(ei.value) and ei.value.status_code == 400 and ei.value.permanente is True


def test_5xx_es_transitorio(monkeypatch):
    monkeypatch.setattr(kapso.settings, "kapso_api_key", "k", raising=False)
    monkeypatch.setattr(kapso.settings, "kapso_phone_number_id", "123", raising=False)
    monkeypatch.setattr(kapso.httpx, "Client", lambda *a, **k: _Client(_Resp(503, "Service Unavailable")))
    with pytest.raises(kapso.KapsoSendError) as ei:
        kapso.send_text_sync("526672341632", "hola")
    assert ei.value.permanente is False
