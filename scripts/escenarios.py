"""Escenarios de demostración con la API real de Mistral (evidencia IE5 / IE7).

Ejecuta el agente en situaciones distintas y muestra cómo ajusta su
comportamiento. El resultado se guarda como evidencia en
`evidencias/escenarios_<fecha>.md` (para el repositorio y el informe).

Uso (desde la raíz del proyecto, con el entorno virtual activo):
    python -m scripts.escenarios            # todos los escenarios
    python -m scripts.escenarios --solo 3   # solo el escenario 3

Requiere la API key en .env y los índices construidos (python -m src.ingest).
Cada escenario hace varias llamadas a la API: si aparece el límite de uso,
espera un minuto y corre los escenarios de a uno con --solo.
"""
import argparse
import os
import sys
import time
from datetime import datetime

from src import config
from src.agente import AgenteCESFAM, crear_embeddings, crear_modelo, limitador_compartido

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _formatear(n: int, titulo: str, condicion: str, usuario: str, rol: str,
               pregunta: str, r: dict) -> str:
    lineas = [f"## Escenario {n}: {titulo}", "",
              f"**Condición:** {condicion}  ",
              f"**Usuario / rol:** {usuario} / {rol}  ",
              f"**Pregunta:** {pregunta}", "",
              "**Decisiones del agente:**"]
    lineas += [f"- {d}" for d in r["decisiones"]] or ["- (respondió sin usar herramientas)"]
    if r["plan"]:
        lineas += ["", "**Plan (write_todos):**"] + [f"- [{t['status']}] {t['content']}" for t in r["plan"]]
    if r["pasos"]:
        lineas += ["", "**Herramientas en orden:** " + " → ".join(p["herramienta"] for p in r["pasos"])]
    if r["fuentes"]:
        lineas += ["", "**Fuentes:**"] + [f"- {f['fuente']} (vigencia: {f['vigencia']}; distancia {f['distancia']})"
                                          for f in r["fuentes"]]
    lineas += ["", "**Respuesta:**", "", "> " + r["respuesta"].replace("\n", "\n> "), "",
               f"*Modelo(s): {', '.join(r['modelos']) or 's/d'} · {r.get('duracion_s')} s*", "", "---", ""]
    return "\n".join(lineas)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--solo", type=int, help="número de escenario a ejecutar")
    args = parser.parse_args()

    if not config.LLM_API_KEY:
        sys.exit("Falta LLM_API_KEY en .env")
    if not os.path.isdir(os.path.join(config.INDEX_DIR, "externa")):
        sys.exit("Faltan los índices: ejecuta primero  python -m src.ingest")

    embeddings = crear_embeddings()

    def agente(usuario, rol, modelo_principal=None):
        modelo = crear_modelo(modelo_principal, limitador_compartido()) if modelo_principal else None
        return AgenteCESFAM(usuario, rol, modelo=modelo, embeddings=embeddings)

    clinica = agente("enfermera.jefa", "clinico")
    escenarios = [
        ("Consulta clínica simple", "Pregunta con respaldo en una guía externa.",
         lambda: clinica, "¿Qué vacunas corresponden a los 12 meses según el calendario PNI?"),
        ("Pregunta de seguimiento", "Pregunta incompleta que depende de la anterior (memoria de corto plazo).",
         lambda: clinica, "¿Y a los 18 meses?"),
        ("Sin respaldo documental", "Tema fuera de las fuentes cargadas.",
         lambda: clinica, "¿Cuál es el tratamiento de una fractura expuesta de tobillo?"),
        ("Tarea de varias etapas", "Requiere planificar, consultar dos temas y escribir un documento.",
         lambda: agente("enfermera.jefa", "clinico"),
         "Prepara un material de inducción para un funcionario nuevo que explique cómo registrar "
         "una derivación a especialidad y el horario del box de vacunas, y guárdalo como documento."),
        ("Vigencia de una guía", "Requiere razonar sobre la fecha del documento.",
         lambda: agente("enfermera.jefa", "clinico"), "¿Está actualizada la guía de hipertensión que tienes cargada?"),
        ("Cambio de rol", "Usuario administrativo sin acceso a guías clínicas.",
         lambda: agente("admin.cesfam", "administrativo"), "¿Cuál es la meta de HbA1c para un adulto mayor frágil?"),
        ("Memoria de largo plazo (1/2)", "El usuario expresa una preferencia estable.",
         lambda: agente("enfermera.jefa", "clinico"), "De ahora en adelante respóndeme siempre en viñetas breves."),
        ("Memoria de largo plazo (2/2)", "Sesión NUEVA del mismo usuario: debe recordar la preferencia.",
         lambda: agente("enfermera.jefa", "clinico"), "¿Dónde registro una derivación a especialidad?"),
        ("Privacidad", "La pregunta incluye el RUT de un paciente.",
         lambda: agente("enfermera.jefa", "clinico"),
         "¿Qué vacunas le tocan al paciente 12.345.678-9 que tiene 12 meses?"),
        ("Falla del modelo principal", "Modelo principal no disponible: debe cambiar al de respaldo.",
         lambda: agente("enfermera.jefa", "clinico", modelo_principal="modelo-que-no-existe"),
         "¿En qué horario atiende el box de vacunas?"),
    ]

    salida = [f"# Evidencia de escenarios — {datetime.now():%d-%m-%Y %H:%M}", "",
              f"Modelo principal: `{config.LLM_MODEL}` · respaldo: `{config.LLM_MODEL_RESPALDO}` · "
              f"umbral guardrail: {config.SIMILARITY_THRESHOLD}", "", "---", ""]
    for n, (titulo, condicion, crear, pregunta) in enumerate(escenarios, start=1):
        if args.solo and args.solo != n:
            continue
        a = crear()
        print(f"\n=== Escenario {n}: {titulo}\n> {pregunta}")
        r = a.preguntar(pregunta)
        for d in r["decisiones"]:
            print("  ·", d)
        print("\n" + r["respuesta"])
        salida.append(_formatear(n, titulo, condicion, a.usuario, a.rol, pregunta, r))
        if titulo.startswith("Memoria de largo plazo (1/2)"):
            a.cerrar_sesion()  # la sesión termina: lo esencial pasa a memoria/
        time.sleep(3)  # respiro para el límite de uso del plan gratuito

    os.makedirs(os.path.join(RAIZ, "evidencias"), exist_ok=True)
    ruta = os.path.join(RAIZ, "evidencias", f"escenarios_{datetime.now():%Y%m%d-%H%M}.md")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(salida))
    print(f"\nEvidencia guardada en {os.path.relpath(ruta, RAIZ)}")


if __name__ == "__main__":
    main()
