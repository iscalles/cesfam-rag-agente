"""Evaluación automática del agente con LangSmith (módulo IL1.4).

Qué hace:
  1. Define un dataset de preguntas de prueba (ampliando las 4 del README),
     cada una con el comportamiento esperado: si debería responder citando
     una fuente puntual, o si debería rehusar por falta de respaldo.
  2. Ejecuta el agente real (AgenteCESFAM) sobre cada pregunta.
  3. Aplica 3 evaluadores propios, mapeados al diseño del guardrail del
     proyecto (no son las métricas RAGAS genéricas, sino chequeos directos
     de las reglas que el agente debe cumplir):
       - rechazo_correcto: ¿el agente respondió o rehusó cuando correspondía?
       - fuente_correcta: cuando debía responder, ¿citó el archivo esperado?
       - cita_formato: cuando respondió, ¿la respuesta incluye la línea
         "Fuente: ..." que exige el SYSTEM_PROMPT (regla 3)?

Uso:
    python -m src.ingest                 # si no se ha construido el índice
    python scripts/evaluar_langsmith.py

Con LANGSMITH_API_KEY configurado en .env (y LANGSMITH_TRACING=true), los
resultados quedan además en https://smith.langchain.com/ como un
"experimento" (capacidad "Análisis Visual" / "Gestión de Datasets" del
módulo IL1.4) — ver docs/langsmith.md. Sin API key, el script igual corre
y deja evidencia en consola + scripts/resultados_evaluacion.json, útil
para el informe aunque no se use la plataforma en la nube.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config  # noqa: E402
from src.rag_chain import AgenteCESFAM  # noqa: E402

# ---------------------------------------------------------------------
# 1. Dataset de evaluación
#    tipo_esperado: "con_respaldo" | "sin_respaldo"
#    fuente_esperada: nombre de archivo que debería aparecer citado
#    (None si se espera rechazo)
# ---------------------------------------------------------------------
EJEMPLOS = [
    {
        "pregunta": "¿Cuál es la meta de HbA1c para un adulto mayor frágil con diabetes tipo 2?",
        "tipo_esperado": "con_respaldo",
        "fuente_esperada": "ges_diabetes_tipo2.md",
    },
    {
        "pregunta": "¿Qué vacunas corresponden a los 12 meses según el calendario PNI?",
        "tipo_esperado": "con_respaldo",
        "fuente_esperada": "pni_calendario_vacunacion.md",
    },
    {
        "pregunta": "¿Dónde registro una derivación a especialidad?",
        "tipo_esperado": "con_respaldo",
        "fuente_esperada": "manual_procedimientos.md",
    },
    {
        "pregunta": "¿Cómo se clasifica la presión arterial en un adulto según la guía GES de hipertensión?",
        "tipo_esperado": "con_respaldo",
        "fuente_esperada": "ges_hipertension.md",
    },
    {
        "pregunta": "¿Cuál es el procedimiento para solicitar horas extra administrativas?",
        "tipo_esperado": "con_respaldo",
        "fuente_esperada": "faq_personal.md",
    },
    {
        "pregunta": "¿Cuál es la dosis de metformina recomendada para un embarazo de alto riesgo?",
        "tipo_esperado": "sin_respaldo",
        "fuente_esperada": None,
    },
    {
        "pregunta": "¿Cuál es el mejor restaurante cerca del CESFAM para almorzar?",
        "tipo_esperado": "sin_respaldo",
        "fuente_esperada": None,
    },
]


# ---------------------------------------------------------------------
# 2. Función objetivo: ejecuta el agente real (sin pasar por el login
#    de Streamlit — auth.autenticar() solo valida credenciales, el
#    agente se puede instanciar directo con cualquier usuario/rol).
# ---------------------------------------------------------------------
def agente_objetivo(inputs: dict) -> dict:
    agente = AgenteCESFAM(usuario="eval.langsmith", rol="clinico")
    resultado = agente.preguntar(inputs["pregunta"])
    return {
        "respuesta": resultado["respuesta"],
        "fuentes": resultado["fuentes"],
    }


# ---------------------------------------------------------------------
# 3. Evaluadores propios (firma compatible con langsmith.evaluate():
#    reciben run/example o, en el modo local de abajo, outputs/reference)
# ---------------------------------------------------------------------
def rechazo_correcto(outputs: dict, reference: dict) -> bool:
    respondio = bool(outputs.get("fuentes"))
    debia_responder = reference["tipo_esperado"] == "con_respaldo"
    return respondio == debia_responder


def fuente_correcta(outputs: dict, reference: dict) -> bool:
    if reference["tipo_esperado"] != "con_respaldo":
        return True  # no aplica: se esperaba rechazo
    archivos = {f["archivo"] for f in outputs.get("fuentes", [])}
    return reference["fuente_esperada"] in archivos


def cita_formato(outputs: dict, reference: dict) -> bool:
    if reference["tipo_esperado"] != "con_respaldo":
        return True  # el mensaje de rechazo no necesita la línea "Fuente:"
    return "Fuente:" in outputs.get("respuesta", "")


EVALUADORES = [rechazo_correcto, fuente_correcta, cita_formato]


# ---------------------------------------------------------------------
# 4. Ejecución: con LangSmith si hay API key, local si no.
# ---------------------------------------------------------------------
def _correr_con_langsmith() -> None:
    from langsmith import Client
    from langsmith.evaluation import evaluate

    cliente = Client()
    nombre_dataset = f"{config.LANGSMITH_PROJECT}-dataset"

    if not cliente.has_dataset(dataset_name=nombre_dataset):
        dataset = cliente.create_dataset(dataset_name=nombre_dataset)
        cliente.create_examples(
            dataset_id=dataset.id,
            inputs=[{"pregunta": e["pregunta"]} for e in EJEMPLOS],
            outputs=[
                {"tipo_esperado": e["tipo_esperado"], "fuente_esperada": e["fuente_esperada"]}
                for e in EJEMPLOS
            ],
        )
        print(f"Dataset '{nombre_dataset}' creado en LangSmith con {len(EJEMPLOS)} ejemplos.")
    else:
        print(f"Usando dataset existente '{nombre_dataset}' en LangSmith.")

    def _wrap(evaluador):
        def _fn(run, example):
            return {
                "key": evaluador.__name__,
                "score": evaluador(run.outputs, example.outputs),
            }
        return _fn

    resultados = evaluate(
        agente_objetivo,
        data=nombre_dataset,
        evaluators=[_wrap(ev) for ev in EVALUADORES],
        experiment_prefix="cesfam-eval",
    )
    print("\nEvaluación enviada a LangSmith. Revisar el experimento en "
          "https://smith.langchain.com/ (proyecto: "
          f"{config.LANGSMITH_PROJECT}).")
    print(resultados)


def _correr_local() -> None:
    print("LANGSMITH_API_KEY no está configurado: corriendo evaluación en "
          "modo local (sin subir resultados a smith.langchain.com).\n")
    filas = []
    for ejemplo in EJEMPLOS:
        outputs = agente_objetivo({"pregunta": ejemplo["pregunta"]})
        fila = {
            "pregunta": ejemplo["pregunta"],
            "tipo_esperado": ejemplo["tipo_esperado"],
            "fuente_esperada": ejemplo["fuente_esperada"],
            "fuentes_obtenidas": [f["archivo"] for f in outputs["fuentes"]],
        }
        for evaluador in EVALUADORES:
            fila[evaluador.__name__] = evaluador(outputs, ejemplo)
        filas.append(fila)

        estado = "OK" if all(fila[ev.__name__] for ev in EVALUADORES) else "FALLA"
        print(f"[{estado}] {ejemplo['pregunta'][:70]}...")
        for evaluador in EVALUADORES:
            print(f"    {evaluador.__name__}: {fila[evaluador.__name__]}")

    ruta_salida = os.path.join(os.path.dirname(__file__), "resultados_evaluacion.json")
    with open(ruta_salida, "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False, indent=2)

    total = len(filas)
    aprobadas = sum(1 for fila in filas if all(fila[ev.__name__] for ev in EVALUADORES))
    print(f"\nResumen: {aprobadas}/{total} ejemplos pasaron los 3 evaluadores.")
    print(f"Detalle guardado en {ruta_salida}")


if __name__ == "__main__":
    if config.LANGSMITH_TRACING and os.getenv("LANGSMITH_API_KEY"):
        _correr_con_langsmith()
    else:
        _correr_local()
