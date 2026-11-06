"""Prueba de integración HTTP con un servidor local que imita la API de Mistral.

Usa los clientes REALES (ChatOpenAI y OpenAIEmbeddings de `src/agente.py`)
apuntando a un servidor falso en localhost. Verifica que:
- las solicitudes tengan el formato que acepta Mistral (prompt de sistema en
  texto plano, herramientas en formato function-calling, respuestas de
  herramientas con su tool_call_id);
- un error 429 del modelo principal active el modelo de respaldo.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src import config
from src.agente import AgenteCESFAM, crear_modelo

MODELO_PRINCIPAL = "mistral-small-latest"
MODELO_RESPALDO = "ministral-8b-latest"


class ServidorMistralFalso(BaseHTTPRequestHandler):
    solicitudes: list = []
    principal_caido = False

    def log_message(self, *args):
        pass

    def _responder(self, codigo, cuerpo):
        datos = json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_POST(self):
        cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).solicitudes.append((self.path, cuerpo))
        if self.path.endswith("/embeddings"):
            entradas = cuerpo["input"] if isinstance(cuerpo["input"], list) else [cuerpo["input"]]
            return self._responder(200, {"object": "list", "model": "mistral-embed", "data": [
                {"object": "embedding", "index": i, "embedding": [0.1] * 8} for i, _ in enumerate(entradas)]})
        if cuerpo["model"] == MODELO_PRINCIPAL and type(self).principal_caido:
            return self._responder(429, {"object": "error", "message": "Rate limit exceeded",
                                         "type": "rate_limited", "code": "1300"})
        ya_uso_herramienta = any(m["role"] == "tool" for m in cuerpo["messages"])
        if not ya_uso_herramienta:
            mensaje = {"role": "assistant", "content": "", "tool_calls": [{
                "id": "abc123XYZ", "type": "function",
                "function": {"name": "verificar_vigencia", "arguments": json.dumps({"documento": "PNI"})}}]}
            fin = "tool_calls"
        else:
            mensaje = {"role": "assistant", "content": "El calendario PNI está vigente (2026)."}
            fin = "stop"
        self._responder(200, {"id": "x", "object": "chat.completion", "model": cuerpo["model"], "created": 0,
                              "choices": [{"index": 0, "message": mensaje, "finish_reason": fin}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


@pytest.fixture
def api_falsa(monkeypatch):
    servidor = HTTPServer(("127.0.0.1", 0), ServidorMistralFalso)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    ServidorMistralFalso.solicitudes = []
    ServidorMistralFalso.principal_caido = False
    for variable in ("NO_PROXY", "no_proxy"):          # localhost sin proxy corporativo
        monkeypatch.setenv(variable, "127.0.0.1,localhost")
    monkeypatch.setattr(config, "LLM_BASE_URL", f"http://127.0.0.1:{servidor.server_port}/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(config, "LLM_MODEL", MODELO_PRINCIPAL)
    monkeypatch.setattr(config, "LLM_MODEL_RESPALDO", MODELO_RESPALDO)
    yield ServidorMistralFalso
    servidor.shutdown()


def _agente(indices, embeddings):
    return AgenteCESFAM("enfermera.jefa", "clinico",
                        modelo=crear_modelo(MODELO_PRINCIPAL),
                        modelo_respaldo=crear_modelo(MODELO_RESPALDO),
                        embeddings=embeddings, indices=indices)


def test_formato_de_solicitudes_compatible_con_mistral(api_falsa, indices, embeddings):
    r = _agente(indices, embeddings).preguntar("¿Está vigente el calendario PNI?")
    assert "vigente" in r["respuesta"]
    assert [p["herramienta"] for p in r["pasos"]] == ["verificar_vigencia"]

    chats = [c for ruta, c in api_falsa.solicitudes if ruta.endswith("/chat/completions")]
    assert len(chats) == 2
    for cuerpo in chats:
        sistema = cuerpo["messages"][0]
        assert sistema["role"] == "system" and isinstance(sistema["content"], str)
        nombres = {t["function"]["name"] for t in cuerpo["tools"]}
        assert {"buscar_guias_minsal", "guardar_documento", "write_todos"} <= nombres
    llamada = next(m for m in chats[1]["messages"] if m["role"] == "assistant")
    assert "name" not in llamada                     # campo que la API no espera
    respuesta_herramienta = next(m for m in chats[1]["messages"] if m["role"] == "tool")
    assert respuesta_herramienta["tool_call_id"] == "abc123XYZ"


def test_error_429_cambia_al_modelo_de_respaldo(api_falsa, indices, embeddings):
    api_falsa.principal_caido = True
    r = _agente(indices, embeddings).preguntar("¿Está vigente el calendario PNI?")
    assert "vigente" in r["respuesta"]
    assert MODELO_RESPALDO in r["modelos"]
    assert any("modelo de respaldo" in d for d in r["decisiones"])
