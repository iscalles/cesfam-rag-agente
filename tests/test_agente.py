"""Pruebas del agente completo: orquestación, memoria y decisiones adaptativas.

Cada prueba fija lo que "dice" el modelo (ModeloGuionado) y verifica cómo
reacciona el agente: qué herramientas ejecuta, qué recuerda, qué decide
cuando cambian las condiciones (sin respaldo, otro rol, API caída, etc.).
"""
import json
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src import config
from src.agente import _MENSAJE_SERVICIO_NO_DISPONIBLE, _MENSAJE_SIN_RESPALDO, AgenteCESFAM
from tests.conftest import ModeloCaido, guion

HOY = datetime(2026, 11, 6)
FUENTE_DM2 = "Fuente: Guía de Práctica Clínica GES — Diabetes Mellitus Tipo 2 (vigencia: 2018)"


def _agente(modelo, indices, embeddings, rol="clinico", usuario="enfermera.jefa", respaldo=None):
    return AgenteCESFAM(usuario, rol, modelo=modelo, modelo_respaldo=respaldo or guion("respaldo"),
                        embeddings=embeddings, indices=indices, hoy=HOY)


def _log():
    with open(config.LOG_PATH, encoding="utf-8") as f:
        return [json.loads(linea) for linea in f]


def _sistema(llamada) -> str:
    sistema = next(m for m in llamada if isinstance(m, SystemMessage))
    assert isinstance(sistema.content, str)   # texto plano, compatible con Mistral
    return sistema.content


# 1. Consulta simple: elige la herramienta, cita y advierte vigencia antigua
def test_consulta_clinica_usa_guias_y_cita_fuente(indices, embeddings):
    modelo = guion([("buscar_guias_minsal", {"consulta": "meta de HbA1c adulto mayor frágil diabetes tipo 2"})],
                   f"La meta es HbA1c menor a 8%.\n{FUENTE_DM2}")
    r = _agente(modelo, indices, embeddings).preguntar("¿Meta de HbA1c en adulto mayor frágil?")

    assert [p["herramienta"] for p in r["pasos"]] == ["buscar_guias_minsal"]
    assert r["fuentes"][0]["archivo"] == "ges_diabetes_tipo2.md"
    assert "8%" in r["respuesta"]
    assert "⚠️ Vigencia" in r["respuesta"]            # guía de 2018 → aviso automático
    assert any("aviso de vigencia" in d for d in r["decisiones"])
    assert _log()[0]["fuentes"][0]["archivo"] == "ges_diabetes_tipo2.md"


# 2. Condición cambiante: no hay respaldo → el guardrail impide la alucinación
def test_sin_respaldo_reemplaza_respuesta_y_registra_pendiente(indices, embeddings):
    modelo = guion([("buscar_guias_minsal", {"consulta": "fractura expuesta de tobillo con yeso"})],
                   [("registrar_consulta_pendiente", {"consulta": "fractura de tobillo", "motivo": "sin respaldo"})],
                   "Se inmoviliza con yeso por 6 semanas.")   # respuesta inventada por el modelo
    r = _agente(modelo, indices, embeddings).preguntar("¿Cómo se maneja una fractura de tobillo?")

    assert r["respuesta"].startswith(_MENSAJE_SIN_RESPALDO)
    assert "yeso" not in r["respuesta"]
    assert "registrada como pendiente" in r["respuesta"]
    assert any(d.startswith("Guardrail") for d in r["decisiones"])
    with open(config.PENDIENTES_PATH, encoding="utf-8") as f:
        assert "fractura de tobillo" in f.read()


# 3. Tarea de varias etapas: planifica, consulta dos veces y escribe un documento
def test_tarea_multipaso_planifica_consulta_y_escribe(indices, embeddings):
    plan = [{"content": "Buscar cómo derivar a especialidad", "status": "in_progress"},
            {"content": "Buscar horario del box de vacunación", "status": "pending"},
            {"content": "Redactar y guardar el material de inducción", "status": "pending"}]
    hecho = [dict(p, status="completed") for p in plan]
    modelo = guion(
        [("write_todos", {"todos": plan})],
        [("buscar_procedimientos_internos", {"consulta": "dónde registro una derivación a especialidad"})],
        [("buscar_procedimientos_internos", {"consulta": "horario del box de vacunación"})],
        [("guardar_documento", {"titulo": "Inducción: derivaciones y box de vacunas",
                                "contenido": "1. Derivaciones en el módulo de interconsultas del SIS.\n"
                                             "2. Box de vacunas: lunes a viernes 08:30-16:00."})],
        [("write_todos", {"todos": hecho})],
        "Listo: dejé el material de inducción en salidas/.")
    r = _agente(modelo, indices, embeddings).preguntar(
        "Prepara un material de inducción sobre derivaciones y el box de vacunas y guárdalo")

    herramientas = [p["herramienta"] for p in r["pasos"]]
    assert herramientas[0] == "write_todos" and "guardar_documento" in herramientas
    assert herramientas.count("buscar_procedimientos_internos") == 2
    assert len(r["plan"]) == 3 and all(t["status"] == "completed" for t in r["plan"])
    assert r["documentos"] and r["documentos"][0]["nombre"].endswith(".md")
    assert any("Planificó" in d for d in r["decisiones"])


# 4. Memoria de corto plazo: la segunda pregunta llega con el historial
def test_memoria_corto_plazo_mantiene_el_hilo(indices, embeddings):
    modelo = guion([("buscar_guias_minsal", {"consulta": "vacunas a los 12 meses calendario PNI"})],
                   "A los 12 meses: SRP, Hepatitis A... Fuente: Calendario PNI (vigencia: 2026)",
                   [("buscar_guias_minsal", {"consulta": "vacunas de los 18 meses calendario PNI"})],
                   "A los 18 meses: refuerzo Hexavalente y SRP. Fuente: Calendario PNI (vigencia: 2026)")
    agente = _agente(modelo, indices, embeddings)
    agente.preguntar("¿Qué vacunas van a los 12 meses?")
    r = agente.preguntar("¿y a los 18?")

    segunda = modelo.llamadas[2]  # primera llamada del segundo turno
    textos = [m.content for m in segunda if isinstance(m, HumanMessage)]
    assert "¿Qué vacunas van a los 12 meses?" in textos   # recuerda la pregunta anterior
    assert r["pasos"][0]["argumentos"]["consulta"] == "vacunas de los 18 meses calendario PNI"


# 5. Memoria de corto plazo en conversaciones largas: se resume lo antiguo
def test_conversacion_larga_se_resume(indices, embeddings, monkeypatch):
    monkeypatch.setattr(config, "RESUMEN_DESDE_MENSAJES", 4)
    monkeypatch.setattr(config, "MENSAJES_A_CONSERVAR", 2)
    modelo = guion("respuesta 1", "respuesta 2",
                   "RESUMEN: el usuario preguntó por vacunas y derivaciones.",  # lo genera el resumen
                   "respuesta 3")
    agente = _agente(modelo, indices, embeddings)
    for i in (1, 2, 3):
        agente.preguntar(f"pregunta {i}")

    ultima = modelo.llamadas[-1]
    contenido = " ".join(str(m.content) for m in ultima)
    assert "RESUMEN: el usuario preguntó por vacunas" in contenido
    assert "pregunta 1" not in contenido   # lo antiguo quedó reemplazado por el resumen


# 6. Memoria de largo plazo: preferencias y sesión anterior sobreviven al cierre
def test_memoria_largo_plazo_entre_sesiones(indices, embeddings):
    modelo1 = guion([("recordar_preferencia", {"preferencia": "respuestas en viñetas breves"})],
                    "Anotado, te responderé en viñetas.")
    sesion1 = _agente(modelo1, indices, embeddings)
    sesion1.preguntar("De ahora en adelante respóndeme en viñetas breves")
    sesion1.cerrar_sesion()

    modelo2 = guion("- Hola de nuevo")
    _agente(modelo2, indices, embeddings).preguntar("Hola, quiero retomar lo de ayer")
    sistema = _sistema(modelo2.llamadas[0])
    assert "respuestas en viñetas breves" in sistema
    assert "sesión anterior" in sistema and "respóndeme en viñetas" in sistema


# 7. Cambio de rol: el administrativo no recibe herramientas clínicas
def test_rol_administrativo_cambia_herramientas_y_prompt(indices, embeddings):
    modelo = guion("Esa consulta corresponde a personal clínico; consulta a la enfermera del programa.")
    agente = _agente(modelo, indices, embeddings, rol="administrativo", usuario="admin.cesfam")
    r = agente.preguntar("¿Cuál es la meta de HbA1c?")

    assert "buscar_guias_minsal" not in agente.nombres_herramientas
    assert "NO tiene acceso a las guías clínicas" in _sistema(modelo.llamadas[0])
    assert r["pasos"] == [] and "personal clínico" in r["respuesta"]


# 8. Falla de la API: cambia solo al modelo de respaldo
def test_falla_del_modelo_principal_usa_respaldo(indices, embeddings):
    respaldo = guion(AIMessage(content="Respuesta desde el modelo de respaldo.",
                               response_metadata={"model_name": config.LLM_MODEL_RESPALDO}))
    agente = _agente(ModeloCaido(messages=iter([])), indices, embeddings, respaldo=respaldo)
    r = agente.preguntar("Hola")

    assert r["respuesta"] == "Respuesta desde el modelo de respaldo."
    assert any("modelo de respaldo" in d for d in r["decisiones"])


# 9. Falla total: no se cae ni inventa; el log guarda el error
def test_falla_total_informa_sin_caerse(indices, embeddings):
    agente = _agente(ModeloCaido(messages=iter([])), indices, embeddings,
                     respaldo=ModeloCaido(messages=iter([])))
    hilo_anterior = agente.thread_id
    r = agente.preguntar("Hola")

    assert r["respuesta"] == _MENSAJE_SERVICIO_NO_DISPONIBLE
    assert agente.thread_id != hilo_anterior    # abre un hilo limpio para seguir
    assert "429" in _log()[0]["error"]


# 10. Privacidad: un RUT nunca llega al modelo ni al log
def test_rut_no_llega_al_modelo_ni_al_log(indices, embeddings):
    modelo = guion("No ingreses datos de pacientes, por favor.")
    _agente(modelo, indices, embeddings).preguntar("¿Qué vacunas le tocan al paciente 12.345.678-9?")

    enviado = " ".join(str(m.content) for m in modelo.llamadas[0])
    assert "12.345.678-9" not in enviado
    registro = _log()[0]
    assert "12.345.678-9" not in json.dumps(registro, ensure_ascii=False)
    assert any("Privacidad" in d for d in registro["decisiones"])
