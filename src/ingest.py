"""Ingesta de documentos fuente: carga, chunking, generación de embeddings
(Mistral) y construcción de un índice FAISS por cada colección de fuentes
(interna / externa).

Cada archivo .md en data/internal o data/external debe iniciar con dos
líneas de metadata que el parser reconoce:

    # Fuente: <nombre citable del documento>
    # Vigencia: <fecha o versión vigente>

Esas líneas son las que luego se citan en cada respuesta del agente.

Uso:
    python -m src.ingest
"""
import glob
import os

from langchain_classic.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

from . import config


def _cargar_markdown(carpeta: str, tipo_fuente: str) -> list[Document]:
    documentos = []
    for ruta in sorted(glob.glob(os.path.join(carpeta, "*.md"))):
        with open(ruta, "r", encoding="utf-8") as f:
            texto = f.read()

        fuente = os.path.basename(ruta)
        vigencia = "sin fecha declarada"
        for linea in texto.splitlines()[:6]:
            if linea.lower().startswith("# fuente:"):
                fuente = linea.split(":", 1)[1].strip()
            if linea.lower().startswith("# vigencia:"):
                vigencia = linea.split(":", 1)[1].strip()

        documentos.append(
            Document(
                page_content=texto,
                metadata={
                    "archivo": os.path.basename(ruta),
                    "fuente": fuente,
                    "vigencia": vigencia,
                    "tipo": tipo_fuente,
                },
            )
        )
    return documentos


def construir_indices() -> None:
    embeddings = OpenAIEmbeddings(
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        model=config.EMBEDDING_MODEL,
        check_embedding_ctx_length=False,
    )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=120,
        separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " "],
    )

    os.makedirs(config.INDEX_DIR, exist_ok=True)

    for carpeta, tipo_fuente in (
        (config.INTERNAL_DIR, "interna"),
        (config.EXTERNAL_DIR, "externa"),
    ):
        documentos = _cargar_markdown(carpeta, tipo_fuente)
        if not documentos:
            print(f"[ingest] Sin documentos en {carpeta}, se omite.")
            continue

        chunks = splitter.split_documents(documentos)
        print(f"[ingest] {tipo_fuente}: {len(documentos)} documento(s) -> {len(chunks)} chunk(s)")

        indice = FAISS.from_documents(chunks, embeddings)
        destino = os.path.join(config.INDEX_DIR, tipo_fuente)
        indice.save_local(destino)
        print(f"[ingest] Índice '{tipo_fuente}' guardado en {destino}")


if __name__ == "__main__":
    if not config.LLM_API_KEY:
        raise SystemExit(
            "Falta LLM_API_KEY. Copia .env.example a .env y completa tu API key de Mistral."
        )
    construir_indices()
