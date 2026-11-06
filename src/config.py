"""Configuración central del agente: credenciales, modelos, rutas y parámetros."""
import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------- Modelos
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.mistral.ai/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "mistral-small-latest")
# Modelo de respaldo: se usa automáticamente si el principal falla
# (límite de uso 429, caída del servicio, modelo no disponible).
LLM_MODEL_RESPALDO = os.getenv("LLM_MODEL_RESPALDO", "ministral-8b-latest")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "mistral-embed")

# El plan gratuito de Mistral limita las solicitudes por segundo. Un agente
# hace varias llamadas por pregunta, así que se espacian con un limitador.
SOLICITUDES_POR_SEGUNDO = float(os.getenv("SOLICITUDES_POR_SEGUNDO", "0.5"))

# ---------------------------------------------------------------- Rutas
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(_RAIZ, "data")
INTERNAL_DIR = os.path.join(DATA_DIR, "internal")
EXTERNAL_DIR = os.path.join(DATA_DIR, "external")
USUARIOS_PATH = os.path.join(DATA_DIR, "usuarios.json")

INDEX_DIR = os.path.join(_RAIZ, "vector_store")
LOG_PATH = os.path.join(_RAIZ, "logs", "trazabilidad.jsonl")
PENDIENTES_PATH = os.path.join(_RAIZ, "logs", "consultas_pendientes.jsonl")
SALIDAS_DIR = os.path.join(_RAIZ, "salidas")      # documentos que escribe el agente
MEMORIA_DIR = os.path.join(_RAIZ, "memoria")      # memoria de largo plazo por usuario

# ---------------------------------------------------------------- Guardrail
# Distancia máxima (L2 al cuadrado, embeddings Mistral) para considerar que
# un fragmento recuperado respalda la respuesta. Los fragmentos sobre este
# umbral se descartan DENTRO de la herramienta de búsqueda: el modelo nunca
# los ve, así que no puede usarlos para "rellenar" una respuesta.
SIMILARITY_THRESHOLD = 0.9
FRAGMENTOS_POR_BUSQUEDA = 4

# Un documento cuya fecha declarada tenga más de estos años se marca como
# "revisar vigencia" (las guías MINSAL se actualizan periódicamente).
ANIOS_MAX_VIGENCIA = 5

# ---------------------------------------------------------------- Agente
# Límite de llamadas al modelo por pregunta: evita ciclos y controla costo.
MAX_LLAMADAS_MODELO_POR_PREGUNTA = 10
# Memoria de corto plazo: cuando la conversación supera este número de
# mensajes, los más antiguos se resumen para no perder coherencia ni
# exceder el contexto del modelo.
RESUMEN_DESDE_MENSAJES = 30
MENSAJES_A_CONSERVAR = 12
# Reintentos ante fallas de la API (con espera creciente: 2 s, 4 s).
REINTENTOS_MODELO = 2
ESPERA_INICIAL_REINTENTO_S = 2.0

# Herramientas permitidas por rol (toma de decisiones según quién consulta).
PERMISOS_POR_ROL = {
    "clinico": {
        "buscar_procedimientos_internos",
        "buscar_guias_minsal",
        "verificar_vigencia",
        "guardar_documento",
        "registrar_consulta_pendiente",
        "recordar_preferencia",
    },
    "administrativo": {
        "buscar_procedimientos_internos",
        "verificar_vigencia",
        "guardar_documento",
        "registrar_consulta_pendiente",
        "recordar_preferencia",
    },
}

# --- LangSmith (observabilidad y evaluación — ver docs/langsmith.md) ---
# LangChain/langsmith leen LANGSMITH_TRACING, LANGSMITH_API_KEY,
# LANGSMITH_ENDPOINT directo desde las variables de entorno (ya cargadas
# arriba por load_dotenv()); se exponen aquí solo para reutilizarlas desde
# scripts/evaluar_langsmith.py y para loguear si el tracing está activo.
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "cesfam-rag-agent")
