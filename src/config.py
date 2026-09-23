"""Configuración central del agente: credenciales, rutas y parámetros."""
import os

from dotenv import load_dotenv

load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.mistral.ai/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "mistral-small-latest")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "mistral-embed")

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(_RAIZ, "data")
INTERNAL_DIR = os.path.join(DATA_DIR, "internal")
EXTERNAL_DIR = os.path.join(DATA_DIR, "external")
USUARIOS_PATH = os.path.join(DATA_DIR, "usuarios.json")

INDEX_DIR = os.path.join(_RAIZ, "vector_store")
LOG_PATH = os.path.join(_RAIZ, "logs", "trazabilidad.jsonl")

# Distancia máxima (L2, embeddings Mistral) para considerar que un chunk
# recuperado sí respalda la respuesta. Es un resguardo adicional al propio
# criterio del LLM: si ni el chunk más cercano cumple este umbral, el
# agente rehúsa responder en vez de arriesgar una alucinación clínica.
# Ajustar empíricamente según pruebas (ver docs/prompts.md).
SIMILARITY_THRESHOLD = 0.9
