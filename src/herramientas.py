"""Herramientas del agente CESFAM.

El agente (LLM) decide cuál usar en cada paso. Se agrupan en tres tipos,
como pide la EP2:

CONSULTA     buscar_procedimientos_internos  -> índice FAISS interno
             buscar_guias_minsal             -> índice FAISS externo
RAZONAMIENTO verificar_vigencia              -> calcula antigüedad de un documento
             write_todos (TodoListMiddleware, en agente.py) -> plan de pasos
ESCRITURA    guardar_documento               -> escribe un .md en salidas/
             registrar_consulta_pendiente    -> anota vacíos de la base documental
             recordar_preferencia            -> escribe en la memoria de largo plazo

Las herramientas de búsqueda aplican el guardrail del EP1 por dentro: los
fragmentos que no superan el umbral de similitud se descartan antes de que
el modelo los vea. Además devuelven un "artifact" con las fuentes, que la
interfaz muestra como tarjetas sin depender de lo que escriba el modelo.
"""
import glob
import json
import os
import re
from datetime import datetime

from langchain_core.tools import tool

from . import config
from .privacidad import anonimizar, contiene_rut

SIN_RESPALDO = "SIN_RESPALDO"


# ---------------------------------------------------------------- vigencia
def _anio_declarado(vigencia: str) -> int | None:
    anios = re.findall(r"\b(?:19|20)\d{2}\b", vigencia or "")
    return int(anios[0]) if anios else None


def estado_vigencia(vigencia: str, hoy: datetime | None = None) -> dict:
    """Razonamiento simple y explicable sobre la vigencia de un documento."""
    hoy = hoy or datetime.now()
    anio = _anio_declarado(vigencia)
    if anio is None:
        return {"estado": "sin_fecha", "anio": None, "antiguedad": None,
                "texto": "sin fecha declarada: verificar con la fuente oficial"}
    antiguedad = hoy.year - anio
    if antiguedad > config.ANIOS_MAX_VIGENCIA:
        return {"estado": "revisar", "anio": anio, "antiguedad": antiguedad,
                "texto": f"REVISAR: documento de {anio} ({antiguedad} años); "
                         "confirmar si MINSAL publicó una versión más reciente"}
    return {"estado": "vigente", "anio": anio, "antiguedad": antiguedad,
            "texto": f"vigente ({anio})"}


def catalogo_documentos() -> list[dict]:
    """Lee el encabezado (# Fuente / # Vigencia) de cada documento cargado."""
    docs = []
    for carpeta, tipo in ((config.INTERNAL_DIR, "interna"), (config.EXTERNAL_DIR, "externa")):
        for ruta in sorted(glob.glob(os.path.join(carpeta, "*.md"))):
            with open(ruta, "r", encoding="utf-8") as f:
                cabecera = f.read().splitlines()[:6]
            fuente, vigencia = os.path.basename(ruta), "sin fecha declarada"
            for linea in cabecera:
                if linea.lower().startswith("# fuente:"):
                    fuente = linea.split(":", 1)[1].strip()
                if linea.lower().startswith("# vigencia:"):
                    vigencia = linea.split(":", 1)[1].strip()
            docs.append({"archivo": os.path.basename(ruta), "fuente": fuente,
                         "vigencia": vigencia, "tipo": tipo})
    return docs


def _palabras(texto: str) -> set[str]:
    return {p for p in re.findall(r"[a-záéíóúñ0-9]+", texto.lower()) if len(p) > 2}


# ---------------------------------------------------------------- fábrica
def crear_herramientas(*, indices: dict, embeddings, usuario: str, rol: str,
                       memoria, limitador=None, hoy: datetime | None = None) -> list:
    """Crea las herramientas para un usuario y las filtra según su rol."""

    def _buscar(tipo: str, consulta: str):
        indice = indices.get(tipo)
        if indice is None:
            return (f"ÍNDICE_NO_DISPONIBLE: el índice '{tipo}' no está construido. "
                    "Ejecutar `python -m src.ingest`.",
                    {"tipo_busqueda": tipo, "consulta": consulta, "respaldo": False, "fuentes": []})
        if limitador is not None:
            limitador.acquire()  # el embedding también consume cuota de la API
        vector = embeddings.embed_query(consulta)
        resultados = indice.similarity_search_with_score_by_vector(
            vector, k=config.FRAGMENTOS_POR_BUSQUEDA)
        mejor = min((float(s) for _, s in resultados), default=None)
        con_respaldo = [(d, float(s)) for d, s in resultados if s <= config.SIMILARITY_THRESHOLD]

        if not con_respaldo:
            texto_mejor = f"{mejor:.3f}" if mejor is not None else "sin resultados"
            return (f"{SIN_RESPALDO}: ningún fragmento de las fuentes {tipo}s es lo bastante "
                    f"similar a «{consulta}» (mejor distancia: {texto_mejor}; umbral: "
                    f"{config.SIMILARITY_THRESHOLD}). No respondas con conocimiento general.",
                    {"tipo_busqueda": tipo, "consulta": consulta, "respaldo": False,
                     "mejor_distancia": mejor, "fuentes": []})

        bloques, fuentes = [], []
        for i, (doc, dist) in enumerate(con_respaldo, start=1):
            vig = estado_vigencia(doc.metadata["vigencia"], hoy)
            bloques.append(f"[{i}] Fuente: {doc.metadata['fuente']} "
                           f"(vigencia: {doc.metadata['vigencia']}) | estado: {vig['texto']}\n"
                           f"{doc.page_content}")
            fuentes.append({"archivo": doc.metadata["archivo"], "fuente": doc.metadata["fuente"],
                            "vigencia": doc.metadata["vigencia"], "tipo": doc.metadata["tipo"],
                            "estado_vigencia": vig["estado"], "distancia": round(dist, 4)})
        contenido = (f"Fragmentos con respaldo ({len(fuentes)}) para «{consulta}»:\n\n"
                     + "\n\n---\n\n".join(bloques))
        return contenido, {"tipo_busqueda": tipo, "consulta": consulta, "respaldo": True,
                           "mejor_distancia": mejor, "fuentes": fuentes}

    # ------------------------------------------------------------ CONSULTA
    @tool(response_format="content_and_artifact")
    def buscar_procedimientos_internos(consulta: str):
        """Busca en el Manual de Procedimientos Internos y en las Preguntas Frecuentes
        del personal del CESFAM Lo Franco. Úsala para temas operativos o administrativos:
        registro de pacientes, derivaciones e interconsultas, agenda, horarios, box,
        inducción de personal nuevo y confidencialidad.
        `consulta` debe ser una frase completa y autocontenida (si es una pregunta de
        seguimiento, incluye el tema del que se venía hablando)."""
        return _buscar("interna", consulta)

    @tool(response_format="content_and_artifact")
    def buscar_guias_minsal(consulta: str):
        """Busca en las fuentes externas oficiales de MINSAL: Guía GES de Hipertensión
        Arterial, Guía GES de Diabetes Mellitus tipo 2 y Calendario del Programa Nacional
        de Inmunizaciones (PNI) 2026. Úsala para metas clínicas, tratamiento farmacológico
        según guía, presión arterial, HbA1c y vacunas por edad o grupo.
        `consulta` debe ser una frase completa y autocontenida (si es una pregunta de
        seguimiento, incluye el tema del que se venía hablando)."""
        return _buscar("externa", consulta)

    # ------------------------------------------------------------ RAZONAMIENTO
    @tool
    def verificar_vigencia(documento: str) -> str:
        """Indica si un documento de la base de conocimiento está vigente o debe
        revisarse, calculando su antigüedad según la fecha declarada. Úsala cuando el
        usuario pregunte si una guía está actualizada o si conviene confiar en ella.
        `documento` puede ser parte del nombre (por ejemplo: 'hipertensión', 'PNI')."""
        catalogo = catalogo_documentos()
        buscadas = _palabras(documento)
        puntaje = [(len(buscadas & _palabras(d["fuente"] + " " + d["archivo"])), d) for d in catalogo]
        puntaje = [(p, d) for p, d in puntaje if p > 0]
        if not puntaje:
            nombres = "; ".join(d["fuente"] for d in catalogo)
            return f"No encontré un documento llamado «{documento}». Documentos cargados: {nombres}"
        mejor = max(p for p, _ in puntaje)
        lineas = []
        for _, d in [x for x in puntaje if x[0] == mejor]:
            vig = estado_vigencia(d["vigencia"], hoy)
            lineas.append(f"- {d['fuente']} (vigencia declarada: {d['vigencia']}) → {vig['texto']}")
        return "Resultado de la verificación de vigencia:\n" + "\n".join(lineas)

    # ------------------------------------------------------------ ESCRITURA
    @tool(response_format="content_and_artifact")
    def guardar_documento(titulo: str, contenido: str):
        """Guarda un documento en formato Markdown para que el usuario lo descargue:
        material de inducción, resumen de un protocolo, ficha de apoyo para un box.
        Úsala SOLO cuando el usuario pida dejar algo por escrito o generar un documento,
        y SOLO con información obtenida de las herramientas de búsqueda, incluyendo
        al final las líneas "Fuente: ... (vigencia: ...)" que correspondan."""
        aviso = ""
        if contiene_rut(titulo) or contiene_rut(contenido):
            aviso = " Se detectaron y omitieron RUT de pacientes."
        titulo, contenido = anonimizar(titulo).strip(), anonimizar(contenido).strip()
        os.makedirs(config.SALIDAS_DIR, exist_ok=True)
        ahora = datetime.now()
        slug = re.sub(r"[^a-z0-9]+", "-", titulo.lower()).strip("-")[:50] or "documento"
        nombre = f"{ahora:%Y%m%d-%H%M%S}_{slug}.md"
        ruta = os.path.join(config.SALIDAS_DIR, nombre)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(f"# {titulo}\n\n"
                    f"> Generado por el asistente interno del CESFAM Lo Franco para "
                    f"{usuario} ({rol}) el {ahora:%d-%m-%Y %H:%M}.\n"
                    f"> Apoyo informativo: validar con la fuente oficial antes de usarlo.\n\n"
                    f"{contenido}\n")
        return (f"Documento guardado como salidas/{nombre}.{aviso}",
                {"ruta": ruta, "nombre": nombre, "titulo": titulo})

    @tool
    def registrar_consulta_pendiente(consulta: str, motivo: str) -> str:
        """Registra una consulta que la base documental no pudo responder, para que el
        equipo a cargo de los documentos la revise y agregue la fuente que falta. Úsala
        cuando las búsquedas devuelvan SIN_RESPALDO o cuando la consulta quede fuera del
        alcance del agente. `motivo` explica brevemente por qué quedó pendiente."""
        os.makedirs(os.path.dirname(config.PENDIENTES_PATH), exist_ok=True)
        registro = {"timestamp": datetime.now().isoformat(timespec="seconds"),
                    "usuario": usuario, "rol": rol,
                    "consulta": anonimizar(consulta), "motivo": anonimizar(motivo)}
        with open(config.PENDIENTES_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        return "Consulta registrada como pendiente para el equipo de documentación."

    @tool
    def recordar_preferencia(preferencia: str) -> str:
        """Guarda en la memoria de largo plazo una preferencia estable del usuario sobre
        cómo quiere recibir las respuestas (por ejemplo: 'respuestas en viñetas breves',
        'siempre indicar el box'). Se recuerda en las próximas sesiones. No la uses
        para datos de pacientes ni para información puntual de una sola consulta."""
        return memoria.agregar_preferencia(preferencia)

    todas = [buscar_procedimientos_internos, buscar_guias_minsal, verificar_vigencia,
             guardar_documento, registrar_consulta_pendiente, recordar_preferencia]
    permitidas = config.PERMISOS_POR_ROL.get(rol, config.PERMISOS_POR_ROL["administrativo"])
    return [h for h in todas if h.name in permitidas]


HERRAMIENTAS_BUSQUEDA = {"buscar_procedimientos_internos", "buscar_guias_minsal"}
