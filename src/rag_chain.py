"""Agente conversacional RAG del CESFAM Lo Franco.

Por qué "agente" y no un RAG simple (ver Propuesta EP1, sección 6):
  1. Enrutamiento: decide a qué colección de fuentes acudir según el tipo
     de consulta (protocolo interno vs. guía clínica/vacunación externa).
  2. Memoria de conversación: arrastra el historial de la sesión para
     resolver preguntas de seguimiento con contexto.
  3. Trazabilidad: registra cada interacción (usuario, pregunta, fuentes
     usadas, respuesta) en un log, exigido por el objetivo de citar
     fuente y fecha de vigencia en el 100% de las respuestas.
  4. Resguardo anti-alucinación: si no hay chunk suficientemente similar,
     el agente rehúsa responder en vez de completar con conocimiento
     general del modelo — crítico en un contexto clínico.
"""
import json
import os
from datetime import datetime, timezone

from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from . import config

SYSTEM_PROMPT = """Eres el asistente interno del CESFAM Lo Franco. Apoyas \
únicamente al personal clínico y administrativo del centro; NO interactúas \
con pacientes, no entregas diagnósticos ni reemplazas el criterio clínico.

Reglas obligatorias:
1. Responde EXCLUSIVAMENTE con la información contenida en el CONTEXTO \
entregado en el mensaje del usuario. No completes vacíos con conocimiento \
general del modelo.
2. Si el CONTEXTO no permite responder con certeza, dilo explícitamente en \
vez de arriesgar una respuesta parcial o inventada.
3. Toda afirmación debe cerrar con una línea "Fuente: <nombre del \
documento> (vigencia: <fecha>)" por cada documento citado.
4. Nunca proceses, solicites ni registres datos identificables de \
pacientes (nombre, RUT, domicilio, etc.).
"""

_MENSAJE_SIN_RESPALDO = (
    "No cuento con información suficiente en las fuentes documentales "
    "disponibles para responder esta consulta. Se recomienda consultar "
    "directamente la guía correspondiente o a un/a profesional con mayor "
    "experiencia."
)

_PLANTILLA_USUARIO = """Pregunta del personal: {pregunta}

CONTEXTO RECUPERADO:
{contexto}
"""

_CLAVES_EXTERNAS = (
    "ges", "guía", "guia", "minsal", "hipertensión", "hipertension",
    "presión", "presion", "diabetes", "glicemia", "hba1c",
    "vacuna", "pni", "inmunización", "inmunizacion", "esquema",
)
_CLAVES_INTERNAS = (
    "procedimiento", "interno", "turno", "agenda", "administrativo",
    "faq", "consulta frecuente", "box", "derivación", "derivacion",
)


def _cargar_indice(tipo: str, embeddings):
    ruta = os.path.join(config.INDEX_DIR, tipo)
    if not os.path.isdir(ruta):
        return None
    return FAISS.load_local(ruta, embeddings, allow_dangerous_deserialization=True)


def _enrutar(pregunta: str) -> list[str]:
    """Decide qué colección(es) de fuentes consultar según palabras clave.
    Ante ambigüedad, consulta ambas: en un contexto clínico es más seguro
    revisar de más que arriesgar dejar fuera una fuente relevante.
    """
    texto = pregunta.lower()
    quiere_externa = any(c in texto for c in _CLAVES_EXTERNAS)
    quiere_interna = any(c in texto for c in _CLAVES_INTERNAS)

    if quiere_externa and not quiere_interna:
        return ["externa"]
    if quiere_interna and not quiere_externa:
        return ["interna"]
    return ["interna", "externa"]


class AgenteCESFAM:
    """Agente con estado por sesión: memoria de conversación + acceso a
    los índices vectoriales ya construidos por `ingest.py`.
    """

    def __init__(self, usuario: str, rol: str):
        self.usuario = usuario
        self.rol = rol
        self.historial: list[dict] = []

        self.embeddings = OpenAIEmbeddings(
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            model=config.EMBEDDING_MODEL,
            check_embedding_ctx_length=False,
        )
        self.llm = ChatOpenAI(
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            model=config.LLM_MODEL,
            temperature=0.1,
        )
        self._indices = {
            "interna": _cargar_indice("interna", self.embeddings),
            "externa": _cargar_indice("externa", self.embeddings),
        }

    def _recuperar(self, pregunta: str, k: int = 4):
        resultados = []
        for tipo in _enrutar(pregunta):
            indice = self._indices.get(tipo)
            if indice is None:
                continue
            resultados.extend(indice.similarity_search_with_score(pregunta, k=k))
        resultados.sort(key=lambda par: par[1])  # menor distancia = más similar
        return resultados[:k]

    def preguntar(self, pregunta: str) -> dict:
        recuperados = self._recuperar(pregunta)
        hay_respaldo = bool(recuperados) and recuperados[0][1] <= config.SIMILARITY_THRESHOLD

        if not hay_respaldo:
            respuesta = _MENSAJE_SIN_RESPALDO
            fuentes_usadas = []
        else:
            contexto = "\n\n---\n\n".join(
                f"[{doc.metadata['tipo']}] {doc.metadata['fuente']} "
                f"(vigencia: {doc.metadata['vigencia']})\n{doc.page_content}"
                for doc, _ in recuperados
            )
            mensajes = [{"role": "system", "content": SYSTEM_PROMPT}]
            mensajes.extend(self.historial[-6:])
            mensajes.append({
                "role": "user",
                "content": _PLANTILLA_USUARIO.format(pregunta=pregunta, contexto=contexto),
            })

            respuesta = self.llm.invoke(mensajes).content
            fuentes_usadas = [
                {
                    "archivo": doc.metadata["archivo"],
                    "fuente": doc.metadata["fuente"],
                    "vigencia": doc.metadata["vigencia"],
                    "tipo": doc.metadata["tipo"],
                    "distancia": round(float(score), 4),
                }
                for doc, score in recuperados
            ]

        self.historial.append({"role": "user", "content": pregunta})
        self.historial.append({"role": "assistant", "content": respuesta})
        self._registrar_trazabilidad(pregunta, respuesta, fuentes_usadas)

        return {"respuesta": respuesta, "fuentes": fuentes_usadas}

    def _registrar_trazabilidad(self, pregunta: str, respuesta: str, fuentes: list[dict]) -> None:
        os.makedirs(os.path.dirname(config.LOG_PATH), exist_ok=True)
        registro = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "usuario": self.usuario,
            "rol": self.rol,
            "pregunta": pregunta,
            "respuesta": respuesta,
            "fuentes": fuentes,
        }
        with open(config.LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
