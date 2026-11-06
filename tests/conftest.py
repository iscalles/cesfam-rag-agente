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
from langchain_core.outputs import ChatGeneration, ChatResult
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


class _ColaMensajes:
    """Envoltorio reutilizable sobre la lista de mensajes guionados.

    `langchain-core` serializa los argumentos de cada llamada al modelo
    (p. ej. al preparar la traza para LangSmith, incluso con
    LANGSMITH_TRACING=false: la serialización corre antes de decidir si
    se envía) iterando sobre este campo con un `for`. Si fuera un
    iterador normal (como `iter(lista)`), esa serialización lo agotaría
    antes de que el modelo "respondiera" de verdad, y el agente caería
    siempre al modelo de respaldo. `__iter__` siempre entrega una
    instantánea de lo que falta sin consumirlo; solo `sacar()` avanza.
    """

    def __init__(self, items):
        self._items = list(items)
        self._index = 0

    def __iter__(self):
        return iter(self._items[self._index:])

    def __next__(self):
        return self.sacar()

    def sacar(self):
        if self._index >= len(self._items):
            raise StopIteration
        item = self._items[self._index]
        self._index += 1
        return item


class ModeloGuionado(GenericFakeChatModel):
    llamadas: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.llamadas.append(list(messages))
        mensaje = self.messages.sacar()
        mensaje_ = AIMessage(content=mensaje) if isinstance(mensaje, str) else mensaje
        return ChatResult(generations=[ChatGeneration(message=mensaje_)])


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
    return ModeloGuionado(messages=_ColaMensajes(mensajes))


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
