"""Utilidades de prueba: el agente se prueba SIN llamar a la API de Mistral.

- EmbeddingsDePrueba: embeddings deterministas por bolsa de palabras, para
  construir índices FAISS reales con los documentos de `data/`.
- ModeloGuionado: un "LLM" que devuelve respuestas predefinidas (incluidas
  llamadas a herramientas) y registra lo que recibió en cada llamada. Así se
  puede verificar cómo el agente orquesta herramientas, memoria y decisiones.
- ModeloCaido: simula una API que falla (por ejemplo, error 429).
"""
import hashlib
import math
import re
import unicodedata

import pytest
from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from src import config
from src.ingest import _cargar_markdown, crear_splitter

_STOP = {"de", "la", "el", "los", "las", "y", "en", "a", "que", "del", "por", "con", "para",
         "un", "una", "se", "al", "o", "es", "su", "sus", "como", "lo", "no", "segun"}


def _normalizar(texto: str) -> list[str]:
    texto = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return [p for p in re.findall(r"[a-z0-9]+", texto) if p not in _STOP and len(p) > 1]


class EmbeddingsDePrueba(Embeddings):
    DIM = 512

    def _vector(self, texto: str) -> list[float]:
        v = [0.0] * self.DIM
        for palabra in _normalizar(texto):
            v[int(hashlib.md5(palabra.encode()).hexdigest(), 16) % self.DIM] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    def embed_documents(self, textos):
        return [self._vector(t) for t in textos]

    def embed_query(self, texto):
        return self._vector(texto)


class ModeloGuionado(GenericFakeChatModel):
    llamadas: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.llamadas.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class ModeloCaido(ModeloGuionado):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.llamadas.append(list(messages))
        raise RuntimeError("Error code: 429 - Rate limit exceeded (simulado)")


def guion(*pasos):
    """Convierte una lista de pasos en mensajes del modelo.
    Un paso es un texto (respuesta final) o una lista de (herramienta, args)."""
    mensajes = []
    for i, paso in enumerate(pasos):
        if isinstance(paso, str):
            mensajes.append(AIMessage(content=paso, response_metadata={"model_name": config.LLM_MODEL}))
        elif isinstance(paso, AIMessage):
            mensajes.append(paso)
        else:
            mensajes.append(AIMessage(content="", tool_calls=[
                {"name": n, "args": a, "id": f"llamada{i}{j}"} for j, (n, a) in enumerate(paso)]))
    return ModeloGuionado(messages=iter(mensajes))


@pytest.fixture(autouse=True)
def entorno_aislado(tmp_path, monkeypatch):
    """Cada prueba escribe logs, memoria y documentos en una carpeta temporal."""
    monkeypatch.setattr(config, "LOG_PATH", str(tmp_path / "logs" / "trazabilidad.jsonl"))
    monkeypatch.setattr(config, "PENDIENTES_PATH", str(tmp_path / "logs" / "pendientes.jsonl"))
    monkeypatch.setattr(config, "SALIDAS_DIR", str(tmp_path / "salidas"))
    monkeypatch.setattr(config, "MEMORIA_DIR", str(tmp_path / "memoria"))
    monkeypatch.setattr(config, "ESPERA_INICIAL_REINTENTO_S", 0.0)
    # Con embeddings de bolsa de palabras las distancias son mayores que con
    # mistral-embed: se ajusta el umbral para probar la LÓGICA del guardrail.
    monkeypatch.setattr(config, "SIMILARITY_THRESHOLD", 1.6)
    return tmp_path


@pytest.fixture(scope="session")
def embeddings():
    return EmbeddingsDePrueba()


@pytest.fixture(scope="session")
def indices(embeddings):
    splitter = crear_splitter()
    resultado = {}
    for carpeta, tipo in ((config.INTERNAL_DIR, "interna"), (config.EXTERNAL_DIR, "externa")):
        chunks = splitter.split_documents(_cargar_markdown(carpeta, tipo))
        resultado[tipo] = FAISS.from_documents(chunks, embeddings)
    return resultado
