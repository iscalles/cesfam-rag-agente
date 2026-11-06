"""Agente conversacional del CESFAM Lo Franco (EP2).

Reemplaza a `rag_chain.py` de la EP1. En la EP1 el flujo era fijo
(enrutar por palabras clave -> recuperar -> guardrail -> responder). Ahora
es un agente de LangChain (`create_agent`, construido sobre LangGraph) que
en cada paso DECIDE qué herramienta usar, cuántas veces y en qué orden,
según la pregunta, el rol del usuario y lo que van devolviendo las
herramientas.

Orquestación (middleware, en el orden en que se aplican):
  1. PIIMiddleware          -> borra RUT de la entrada antes del modelo
  2. prompt dinámico        -> reglas + permisos del rol + memoria de largo plazo
  3. TodoListMiddleware     -> herramienta `write_todos` para planificar tareas largas
     (+ un middleware propio que deja el prompt de sistema como texto plano)
  4. SummarizationMiddleware-> resume la conversación cuando crece (memoria corto plazo)
  5. ModelCallLimitMiddleware -> tope de llamadas por pregunta (evita ciclos)
  6. ModelRetryMiddleware   -> reintenta con espera creciente si la API falla
  7. ModelFallbackMiddleware-> cambia al modelo de respaldo si el principal falla

Fuera del grafo, `preguntar()` aplica dos garantías deterministas que no
dependen del modelo: el guardrail final (si ninguna búsqueda tuvo respaldo,
la respuesta se reemplaza por el mensaje fijo) y el aviso de vigencia (si
se citó un documento antiguo, se advierte siempre). Todo queda en el log.
"""
import json
import os
import time
import uuid
from datetime import datetime, timezone

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRequest,
    ModelRetryMiddleware,
    PIIMiddleware,
    SummarizationMiddleware,
    TodoListMiddleware,
    dynamic_prompt,
    wrap_model_call,
)
from langchain_community.vectorstores import FAISS
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.checkpoint.memory import InMemorySaver

from . import config
from .herramientas import HERRAMIENTAS_BUSQUEDA, crear_herramientas
from .memoria import MemoriaLargoPlazo
from .privacidad import RUT_REGEX, anonimizar

# ---------------------------------------------------------------- prompts
SYSTEM_PROMPT = """Eres el asistente interno del CESFAM Lo Franco. Apoyas \
únicamente al personal clínico y administrativo del centro; NO interactúas \
con pacientes, no entregas diagnósticos ni reemplazas el criterio clínico.

Trabajas con herramientas. Decide en cada paso cuál usar:
- Búsqueda en procedimientos internos o en guías MINSAL según el tema. Si la \
pregunta mezcla ambos temas, usa ambas.
- verificar_vigencia si preguntan si una guía está actualizada.
- guardar_documento solo si el usuario pide dejar algo por escrito.
- registrar_consulta_pendiente si no encuentras respaldo.
- recordar_preferencia si el usuario expresa cómo quiere que le respondas \
de ahora en adelante.

Reglas obligatorias:
1. Antes de responder sobre protocolos, guías, vacunas o procedimientos, \
busca con la herramienta correspondiente. Responde EXCLUSIVAMENTE con lo que \
devuelvan las herramientas; no completes vacíos con conocimiento general.
2. Si una búsqueda devuelve SIN_RESPALDO, puedes reformularla una vez con \
otras palabras. Si sigue sin respaldo, di explícitamente que no cuentas con \
información suficiente y registra la consulta con registrar_consulta_pendiente.
3. Cierra cada afirmación con una línea "Fuente: <documento> (vigencia: \
<fecha>)". Si la herramienta marca la vigencia como REVISAR, adviértelo.
4. En preguntas de seguimiento usa la conversación para escribir consultas \
de búsqueda completas (por ejemplo "vacunas de los 18 meses calendario PNI" \
en vez de "¿y a los 18?").
5. Nunca proceses, solicites ni registres datos identificables de pacientes \
(nombre, RUT, domicilio). Si ves un dato omitido como [RUT OMITIDO] o \
[REDACTED_RUT], recuerda al usuario que no debe ingresarlos.
6. Responde en español, de forma breve y directa.
"""

PROMPT_PLANIFICACION = """## Planificación con `write_todos`
Si la tarea tiene 3 o más pasos (por ejemplo: buscar en dos fuentes y luego \
redactar y guardar un documento), escribe primero un plan con `write_todos`, \
marca cada paso como completado apenas lo termines y ajusta el plan si una \
búsqueda no tiene respaldo. Para preguntas simples NO uses esta herramienta. \
Nunca llames `write_todos` en paralelo. La respuesta final va en un mensaje \
aparte, después de la última actualización del plan."""

_MENSAJE_SIN_RESPALDO = (
    "No cuento con información suficiente en las fuentes documentales "
    "disponibles para responder esta consulta. Se recomienda consultar "
    "directamente la guía correspondiente o a un/a profesional con mayor "
    "experiencia."
)
_MENSAJE_SERVICIO_NO_DISPONIBLE = (
    "El servicio de IA no está disponible en este momento (límite de uso o "
    "problema de conexión), tampoco con el modelo de respaldo. Intenta "
    "nuevamente en unos segundos."
)
_NOMBRE_INDICE = {"buscar_procedimientos_internos": "índice interno",
                  "buscar_guias_minsal": "índice externo (MINSAL)"}


def _seccion_rol(rol: str, herramientas: list[str]) -> str:
    texto = f"\n## Usuario actual\nRol: {rol}."
    if "buscar_guias_minsal" not in herramientas:
        texto += (" Este rol NO tiene acceso a las guías clínicas MINSAL ni al "
                  "calendario de vacunación. Si te consultan algo clínico (metas, "
                  "fármacos, vacunas por edad), no lo respondas: explica que esa "
                  "consulta corresponde a personal clínico y sugiere a quién acudir. "
                  "Sí puedes responder procedimientos internos.")
    return texto


@wrap_model_call
def prompt_sistema_como_texto(request, handler):
    """TodoListMiddleware deja el prompt de sistema como lista de bloques;
    se convierte a texto plano para máxima compatibilidad con la API de Mistral."""
    sistema = request.system_message
    if sistema is not None and not isinstance(sistema.content, str):
        request = request.override(system_message=SystemMessage(content=sistema.text))
    return handler(request)


# ---------------------------------------------------------------- fábricas
_limitador = None


def limitador_compartido() -> InMemoryRateLimiter:
    """Un solo limitador para chat y embeddings: toda la cuota es la misma."""
    global _limitador
    if _limitador is None:
        _limitador = InMemoryRateLimiter(requests_per_second=config.SOLICITUDES_POR_SEGUNDO,
                                         check_every_n_seconds=0.1, max_bucket_size=1)
    return _limitador


def crear_modelo(nombre: str, limitador=None) -> ChatOpenAI:
    return ChatOpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY,
                      model=nombre, temperature=0.1, max_retries=1, timeout=60,
                      rate_limiter=limitador)


def crear_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY,
                            model=config.EMBEDDING_MODEL, check_embedding_ctx_length=False)


def cargar_indices(embeddings) -> dict:
    indices = {}
    for tipo in ("interna", "externa"):
        ruta = os.path.join(config.INDEX_DIR, tipo)
        indices[tipo] = (FAISS.load_local(ruta, embeddings, allow_dangerous_deserialization=True)
                         if os.path.isdir(ruta) else None)
    return indices


# ---------------------------------------------------------------- agente
class AgenteCESFAM:
    """Un agente por sesión de usuario. Los parámetros opcionales permiten
    inyectar modelos, embeddings e índices de prueba (ver tests/)."""

    def __init__(self, usuario: str, rol: str, *, modelo=None, modelo_respaldo=None,
                 embeddings=None, indices=None, memoria=None, hoy=None):
        self.usuario, self.rol = usuario, rol
        self.thread_id = self._nuevo_hilo()
        self.consultas_sesion: list[str] = []
        self.memoria = memoria or MemoriaLargoPlazo(usuario)

        limitador = limitador_compartido() if modelo is None else None
        self.embeddings = embeddings or crear_embeddings()
        modelo = modelo or crear_modelo(config.LLM_MODEL, limitador)
        modelo_respaldo = modelo_respaldo or crear_modelo(config.LLM_MODEL_RESPALDO, limitador)
        self.indices = indices if indices is not None else cargar_indices(self.embeddings)

        self.herramientas = crear_herramientas(
            indices=self.indices, embeddings=self.embeddings, usuario=usuario, rol=rol,
            memoria=self.memoria, limitador=limitador, hoy=hoy)
        self.nombres_herramientas = [h.name for h in self.herramientas]

        memoria_sesion, nombres = self.memoria, self.nombres_herramientas

        @dynamic_prompt
        def prompt_con_contexto(request: ModelRequest) -> str:
            # Se recalcula en CADA llamada al modelo: si el agente guarda una
            # preferencia a mitad de la conversación, rige desde la siguiente.
            return SYSTEM_PROMPT + _seccion_rol(rol, nombres) + memoria_sesion.como_contexto()

        self.grafo = create_agent(
            model=modelo,
            tools=self.herramientas,
            middleware=[
                PIIMiddleware("rut", detector=RUT_REGEX, strategy="redact", apply_to_input=True),
                prompt_con_contexto,
                TodoListMiddleware(system_prompt=PROMPT_PLANIFICACION),
                prompt_sistema_como_texto,
                SummarizationMiddleware(model=modelo,
                                        trigger=("messages", config.RESUMEN_DESDE_MENSAJES),
                                        keep=("messages", config.MENSAJES_A_CONSERVAR)),
                ModelCallLimitMiddleware(run_limit=config.MAX_LLAMADAS_MODELO_POR_PREGUNTA,
                                         exit_behavior="end"),
                ModelRetryMiddleware(max_retries=config.REINTENTOS_MODELO,
                                     initial_delay=config.ESPERA_INICIAL_REINTENTO_S,
                                     on_failure="error"),
                ModelFallbackMiddleware(modelo_respaldo),
            ],
            # Memoria de corto plazo por hilo de sesión. (Sin `name=`: ese campo
            # viajaría en cada mensaje del asistente y la API de Mistral no lo usa.)
            checkpointer=InMemorySaver(),
        )

    def _nuevo_hilo(self) -> str:
        return f"{self.usuario}-{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------ consulta
    def preguntar(self, pregunta: str) -> dict:
        inicio = time.time()
        texto = anonimizar(pregunta)
        decisiones = []
        if texto != pregunta:
            decisiones.append("Privacidad: se detectó un RUT y se omitió antes de enviarlo al modelo.")

        mensaje = HumanMessage(content=texto, id=str(uuid.uuid4()))
        configuracion = {"configurable": {"thread_id": self.thread_id}, "recursion_limit": 50}
        error = None
        try:
            estado = self.grafo.invoke({"messages": [mensaje]}, config=configuracion)
            resultado = self._analizar_turno(estado, mensaje.id, decisiones)
        except Exception as exc:  # ambos modelos fallaron o error inesperado
            error = f"{type(exc).__name__}: {exc}"[:300]
            # El hilo pudo quedar a medias (por ejemplo, con una herramienta sin
            # respuesta del modelo); se abre uno nuevo para no arrastrar el error.
            self.thread_id = self._nuevo_hilo()
            resultado = {"respuesta": _MENSAJE_SERVICIO_NO_DISPONIBLE, "fuentes": [], "pasos": [],
                         "plan": [], "documentos": [], "modelos": [],
                         "decisiones": decisiones + [
                             "Servicio de IA no disponible (modelo principal y de respaldo "
                             "fallaron): se informó al usuario sin inventar una respuesta."]}

        self.consultas_sesion.append(texto)
        self.memoria.registrar_temas(resultado["fuentes"])
        resultado["duracion_s"] = round(time.time() - inicio, 2)
        self._registrar_trazabilidad(texto, resultado, error)
        return resultado

    def _analizar_turno(self, estado: dict, id_mensaje: str, decisiones: list[str]) -> dict:
        mensajes = estado["messages"]
        inicio = next((i for i, m in enumerate(mensajes) if m.id == id_mensaje), None)
        if inicio is None:  # respaldo: desde el último mensaje humano
            inicio = max(i for i, m in enumerate(mensajes) if isinstance(m, HumanMessage))
        turno = mensajes[inicio + 1:]

        pasos, artefactos, modelos, uso_plan = [], [], [], False
        resultados = {m.tool_call_id: m for m in turno if isinstance(m, ToolMessage)}
        for m in turno:
            if not isinstance(m, AIMessage):
                continue
            nombre_modelo = (m.response_metadata or {}).get("model_name")
            if nombre_modelo and nombre_modelo not in modelos:
                modelos.append(nombre_modelo)
            for llamada in m.tool_calls:
                tm = resultados.get(llamada["id"])
                artefacto = getattr(tm, "artifact", None) if tm else None
                if llamada["name"] == "write_todos":
                    uso_plan = True
                pasos.append({"herramienta": llamada["name"], "argumentos": llamada["args"],
                              "resultado": (tm.content[:300] if tm and isinstance(tm.content, str) else "")})
                artefactos.append((llamada["name"], llamada["args"], artefacto))

        # Respuesta final = último mensaje del modelo sin llamadas a herramientas.
        finales = [m for m in turno if isinstance(m, AIMessage) and not m.tool_calls]
        respuesta = finales[-1].text.strip() if finales else ""

        busquedas = [a for n, _, a in artefactos if n in HERRAMIENTAS_BUSQUEDA and a]
        fuentes: dict[str, dict] = {}
        for b in busquedas:
            for f in b["fuentes"]:
                previa = fuentes.get(f["archivo"])
                if previa is None or f["distancia"] < previa["distancia"]:
                    fuentes[f["archivo"]] = f
        fuentes_lista = sorted(fuentes.values(), key=lambda f: f["distancia"])
        documentos = [a for n, _, a in artefactos if n == "guardar_documento" and a]

        # ---- decisiones legibles, para la interfaz y el log
        for nombre, args, art in artefactos:
            if nombre in HERRAMIENTAS_BUSQUEDA and art:
                dist = art.get("mejor_distancia")
                dist_txt = f"{dist:.3f}" if isinstance(dist, (int, float)) else "s/d"
                if art["respaldo"]:
                    decisiones.append(f"Consultó el {_NOMBRE_INDICE[nombre]} con «{art['consulta']}» → "
                                      f"{len(art['fuentes'])} fragmento(s) con respaldo (mejor distancia {dist_txt}).")
                else:
                    decisiones.append(f"Consultó el {_NOMBRE_INDICE[nombre]} con «{art['consulta']}» → "
                                      f"SIN RESPALDO (mejor distancia {dist_txt} > umbral {config.SIMILARITY_THRESHOLD}).")
            elif nombre == "write_todos":
                n = len(args.get("todos", []))
                decisiones.append(f"Planificó la tarea en {n} paso(s) con write_todos.")
            elif nombre == "guardar_documento" and art:
                decisiones.append(f"Escribió el documento salidas/{art['nombre']}.")
            elif nombre == "registrar_consulta_pendiente":
                decisiones.append("Registró la consulta como pendiente para el equipo de documentación.")
            elif nombre == "recordar_preferencia":
                decisiones.append(f"Guardó en memoria de largo plazo: «{args.get('preferencia', '')}».")
            elif nombre == "verificar_vigencia":
                decisiones.append(f"Verificó la vigencia de «{args.get('documento', '')}».")

        respaldo_modelo = config.LLM_MODEL_RESPALDO.split("-latest")[0]
        if any(m.startswith(respaldo_modelo) for m in modelos):
            decisiones.append(f"Usó el modelo de respaldo ({config.LLM_MODEL_RESPALDO}) porque el principal falló.")

        # ---- garantía 1: guardrail final (no depende del modelo)
        if busquedas and not any(b["respaldo"] for b in busquedas):
            respuesta = _MENSAJE_SIN_RESPALDO
            if any(n == "registrar_consulta_pendiente" for n, _, _ in artefactos):
                respuesta += " La consulta quedó registrada como pendiente para el equipo de documentación."
            decisiones.append("Guardrail: ninguna búsqueda tuvo respaldo → se entregó el mensaje fijo, "
                              "sin contenido generado por el modelo.")

        # ---- garantía 2: aviso de vigencia (no depende del modelo)
        antiguas = [f for f in fuentes_lista if f.get("estado_vigencia") == "revisar"]
        if antiguas and "revis" not in respuesta.lower():
            nombres = "; ".join(sorted({f["fuente"] for f in antiguas}))
            respuesta += (f"\n\n⚠️ Vigencia: {nombres} tiene más de {config.ANIOS_MAX_VIGENCIA} años. "
                          "Confirma si MINSAL publicó una versión más reciente.")
            decisiones.append("Agregó un aviso de vigencia porque citó un documento antiguo.")

        if not respuesta:
            respuesta = "No pude completar la respuesta dentro del límite de pasos. Intenta con una consulta más acotada."
            decisiones.append(f"Se alcanzó el límite de {config.MAX_LLAMADAS_MODELO_POR_PREGUNTA} llamadas al modelo.")

        return {"respuesta": respuesta, "fuentes": fuentes_lista, "pasos": pasos,
                "plan": estado.get("todos", []) if uso_plan else [],
                "documentos": documentos, "modelos": modelos, "decisiones": decisiones}

    # ------------------------------------------------------------ cierre y log
    def cerrar_sesion(self) -> None:
        """Pasa lo esencial de la sesión a la memoria de largo plazo."""
        self.memoria.cerrar_sesion(self.consultas_sesion)

    def _registrar_trazabilidad(self, pregunta: str, resultado: dict, error: str | None) -> None:
        os.makedirs(os.path.dirname(config.LOG_PATH), exist_ok=True)
        registro = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "usuario": self.usuario,
            "rol": self.rol,
            "sesion": self.thread_id,
            "pregunta": pregunta,
            "respuesta": anonimizar(resultado["respuesta"]),
            "fuentes": resultado["fuentes"],
            "herramientas": [{"herramienta": p["herramienta"],
                              "argumentos": json.loads(anonimizar(json.dumps(p["argumentos"], ensure_ascii=False)))}
                             for p in resultado["pasos"]],
            "decisiones": resultado["decisiones"],
            "modelos": resultado["modelos"],
            "duracion_s": resultado.get("duracion_s"),
            "error": error,
        }
        with open(config.LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
