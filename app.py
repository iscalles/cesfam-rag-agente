"""Demo del agente CESFAM Lo Franco (EP2) — chat con login por usuario/rol.

Novedades respecto de la EP1:
- Panel "Cómo lo resolvió el agente": plan, herramientas usadas y decisiones,
  para mostrar en vivo la autonomía y el razonamiento (IE7).
- Descarga de los documentos que el agente escribe en salidas/.
- Barra lateral con la memoria de largo plazo y las herramientas del rol.
- Al cerrar sesión, lo esencial pasa a la memoria de largo plazo.

Antes de correr esto, construir los índices una vez:
    python -m src.ingest
Ejecutar la demo:
    streamlit run app.py
"""
import os

import streamlit as st

from src import auth
from src.agente import AgenteCESFAM
from src.privacidad import anonimizar

# ---------- Paleta (misma que docs/ y el PPT) ----------
PRIMARY = "#04555C"
PRIMARY_DK = "#022F33"
SECONDARY = "#00A896"
LIGHT = "#EAF6F6"
ACCENT = "#E4572E"

EJEMPLOS = {
    "clinico": [
        "¿Qué vacunas corresponden a los 12 meses según el calendario PNI?",
        "¿Y a los 18 meses?",
        "¿Está actualizada la guía de hipertensión que tienes cargada?",
        "Prepara un material de inducción sobre cómo registrar una derivación a "
        "especialidad y el horario del box de vacunas, y guárdalo como documento.",
        "¿Cuál es el tratamiento de una fractura expuesta de tobillo?",
        "De ahora en adelante respóndeme siempre en viñetas breves.",
    ],
    "administrativo": [
        "¿En qué horario atiende el box de vacunas?",
        "¿Dónde registro una derivación a especialidad?",
        "¿Cuál es la meta de HbA1c para un adulto mayor frágil?",
    ],
}
NOMBRES_HERRAMIENTAS = {
    "buscar_procedimientos_internos": "🔎 Buscar procedimientos internos",
    "buscar_guias_minsal": "🔎 Buscar guías MINSAL / PNI",
    "verificar_vigencia": "🧠 Verificar vigencia",
    "guardar_documento": "✍️ Guardar documento",
    "registrar_consulta_pendiente": "✍️ Registrar consulta pendiente",
    "recordar_preferencia": "✍️ Recordar preferencia",
    "write_todos": "🧠 Planificar tarea",
}

st.set_page_config(page_title="Asistente CESFAM Lo Franco", page_icon=":hospital:", layout="centered")

for clave, valor in (("agente", None), ("mensajes", []), ("pregunta_pendiente", None)):
    if clave not in st.session_state:
        st.session_state[clave] = valor

# ---------- Estilos ----------
st.markdown(f"""
<style>
.cesfam-header {{ background: linear-gradient(135deg, {PRIMARY_DK} 0%, {PRIMARY} 100%);
    padding: 1.4rem 1.6rem; border-radius: 14px; margin-bottom: 1.2rem; }}
.cesfam-header h1 {{ color: #FFFFFF; font-size: 1.5rem; margin: 0 0 0.2rem 0; font-weight: 700; }}
.cesfam-header p {{ color: {LIGHT}; font-size: 0.92rem; margin: 0; opacity: 0.9; }}
.cesfam-badge {{ display: inline-block; background: {SECONDARY}; color: #FFFFFF; font-size: 0.72rem;
    font-weight: 700; letter-spacing: 0.04em; padding: 0.15rem 0.55rem; border-radius: 999px;
    margin-bottom: 0.5rem; text-transform: uppercase; }}
.fuente-card {{ border-left: 4px solid var(--tag-color, {PRIMARY}); background: {LIGHT};
    border-radius: 8px; padding: 0.55rem 0.8rem; margin-bottom: 0.5rem; }}
.fuente-card .tag {{ display: inline-block; font-size: 0.68rem; font-weight: 700; color: #FFFFFF;
    background: var(--tag-color, {PRIMARY}); padding: 0.05rem 0.5rem; border-radius: 999px;
    margin-bottom: 0.25rem; text-transform: uppercase; letter-spacing: 0.03em; }}
.fuente-card .nombre {{ font-weight: 700; color: {PRIMARY_DK}; font-size: 0.9rem; }}
.fuente-card .meta {{ color: #4B6668; font-size: 0.78rem; margin-top: 0.1rem; }}
.rechazo-box {{ border-left: 4px solid {ACCENT}; background: #FCEEE9; border-radius: 8px;
    padding: 0.6rem 0.9rem; font-size: 0.85rem; color: {PRIMARY_DK}; }}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="cesfam-header">
  <h1>🩺 Asistente interno — CESFAM Lo Franco</h1>
  <p>Agente de apoyo informativo para personal autorizado. No diagnostica, no reemplaza el
  criterio clínico y no interactúa con pacientes.</p>
</div>
""", unsafe_allow_html=True)


def _tarjeta_fuente(f: dict) -> str:
    es_interna = f.get("tipo") == "interna"
    color = PRIMARY if es_interna else SECONDARY
    etiqueta = "Fuente interna" if es_interna else "Fuente externa · MINSAL"
    alerta = " · ⚠️ revisar vigencia" if f.get("estado_vigencia") == "revisar" else ""
    return f"""
    <div class="fuente-card" style="--tag-color: {color};">
        <span class="tag">{etiqueta}</span>
        <div class="nombre">{f['fuente']}</div>
        <div class="meta">Vigencia: {f['vigencia']}{alerta} &nbsp;·&nbsp; distancia: {f['distancia']}</div>
    </div>"""


def _mostrar_resultado(r: dict, indice: int) -> None:
    """Dibuja una respuesta del agente con sus fuentes, documentos y razonamiento."""
    st.markdown(r["respuesta"])
    if r["fuentes"]:
        st.markdown("**Fuentes consultadas**")
        for f in r["fuentes"]:
            st.markdown(_tarjeta_fuente(f), unsafe_allow_html=True)
    elif any(d.startswith("Guardrail") for d in r["decisiones"]):
        st.markdown('<div class="rechazo-box">Sin fuente citada: ninguna búsqueda encontró un '
                    "documento con similitud suficiente, así que el agente no generó contenido.</div>",
                    unsafe_allow_html=True)

    for i, doc in enumerate(r["documentos"]):
        if os.path.exists(doc["ruta"]):
            with open(doc["ruta"], "rb") as archivo:
                st.download_button(f"⬇️ Descargar «{doc['titulo']}»", archivo.read(),
                                   file_name=doc["nombre"], mime="text/markdown",
                                   key=f"descarga_{indice}_{i}")

    if r["decisiones"] or r["plan"]:
        with st.expander("🧠 Cómo lo resolvió el agente"):
            if r["plan"]:
                st.markdown("**Plan**")
                for t in r["plan"]:
                    marca = "✅" if t["status"] == "completed" else "⏳"
                    st.markdown(f"{marca} {t['content']}")
            if r["pasos"]:
                st.markdown("**Herramientas, en orden**")
                st.markdown(" → ".join(NOMBRES_HERRAMIENTAS.get(p["herramienta"], p["herramienta"])
                                       for p in r["pasos"]))
            st.markdown("**Decisiones**")
            for d in r["decisiones"]:
                st.markdown(f"- {d}")
            modelos = ", ".join(r["modelos"]) or "s/d"
            st.caption(f"Modelo(s): {modelos} · {r.get('duracion_s', '?')} s")


def _procesar_pregunta(pregunta: str) -> None:
    pregunta = anonimizar(pregunta)  # un RUT nunca se muestra ni se guarda en el historial
    with st.chat_message("user"):
        st.markdown(pregunta)
    with st.chat_message("assistant"):
        with st.spinner("El agente está consultando y razonando..."):
            resultado = st.session_state.agente.preguntar(pregunta)
        indice = len(st.session_state.mensajes)
        _mostrar_resultado(resultado, indice)
    st.session_state.mensajes.append({"role": "user", "content": pregunta, "resultado": None})
    st.session_state.mensajes.append({"role": "assistant", "content": resultado["respuesta"],
                                      "resultado": resultado})


# ======================= MODO 1: LOGIN =======================
if st.session_state.agente is None:
    st.markdown('<span class="cesfam-badge">Ingreso de personal autorizado</span>', unsafe_allow_html=True)
    with st.form("login"):
        usuario = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        enviar = st.form_submit_button("Ingresar", use_container_width=True)

    if enviar:
        rol = auth.autenticar(usuario, password)
        if rol is None:
            st.error("Usuario o contraseña incorrectos, o cuenta no autorizada.")
        else:
            st.session_state.agente = AgenteCESFAM(usuario=usuario, rol=rol)
            st.rerun()

    st.caption("Demo: usuario `enfermera.jefa` (clínico) o `admin.cesfam` (administrativo), "
               "contraseña `cesfam2026`.")

# ======================= MODO 2: CHAT =======================
else:
    agente = st.session_state.agente
    with st.sidebar:
        st.markdown(f"""
        <div style="background:{LIGHT}; border-radius:10px; padding:0.8rem 1rem; margin-bottom:1rem;">
            <div style="font-size:0.75rem; color:{PRIMARY}; font-weight:700; text-transform:uppercase;">Sesión activa</div>
            <div style="font-size:1rem; font-weight:700; color:{PRIMARY_DK};">{agente.usuario}</div>
            <div style="font-size:0.8rem; color:#4B6668;">Rol: {agente.rol}</div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("🧰 Herramientas de este rol"):
            for nombre in agente.nombres_herramientas + ["write_todos"]:
                st.markdown(f"- {NOMBRES_HERRAMIENTAS.get(nombre, nombre)}")

        with st.expander("💾 Memoria de largo plazo"):
            datos = agente.memoria.datos
            if datos["preferencias"]:
                st.markdown("**Preferencias**")
                for p in datos["preferencias"]:
                    st.markdown(f"- {p}")
            if datos.get("ultima_sesion"):
                st.markdown(f"**Sesión anterior** ({datos['ultima_sesion']['fecha']})")
                for c in datos["ultima_sesion"]["consultas"]:
                    st.markdown(f"- {c}")
            if not datos["preferencias"] and not datos.get("ultima_sesion"):
                st.caption("Aún no hay nada guardado para este usuario.")

        st.markdown("**Preguntas de ejemplo**")
        for ej in EJEMPLOS.get(agente.rol, []):
            if st.button(ej, key=f"ej_{ej}", use_container_width=True):
                st.session_state.pregunta_pendiente = ej

        st.divider()
        if st.button("Cerrar sesión", use_container_width=True):
            agente.cerrar_sesion()   # lo esencial de la sesión pasa a la memoria de largo plazo
            st.session_state.agente = None
            st.session_state.mensajes = []
            st.rerun()

    for i, m in enumerate(st.session_state.mensajes):
        with st.chat_message(m["role"]):
            if m["resultado"] is not None:
                _mostrar_resultado(m["resultado"], i)
            else:
                st.markdown(m["content"])

    pregunta = st.chat_input("Escribe tu consulta o pide una tarea (por ejemplo, preparar un documento)...")
    if st.session_state.pregunta_pendiente:
        pregunta = st.session_state.pregunta_pendiente
        st.session_state.pregunta_pendiente = None

    if pregunta:
        _procesar_pregunta(pregunta)
