"""Demo del agente CESFAM Lo Franco — interfaz de chat con autenticación
básica por usuario/rol.

El look and feel (colores, tarjetas de fuentes) usa la misma paleta que
el informe y el PPT del proyecto: ver .streamlit/config.toml para el
tema base y las constantes de color más abajo.

Antes de correr esto, construir los índices una vez:
    python -m src.ingest

Ejecutar la demo:
    streamlit run app.py
"""
import streamlit as st

from src import auth
from src.rag_chain import AgenteCESFAM

# ---------- Paleta (misma que docs/arquitectura.png y el PPT) ----------
PRIMARY = "#04555C"
PRIMARY_DK = "#022F33"
SECONDARY = "#00A896"
LIGHT = "#EAF6F6"
ACCENT = "#E4572E"

EJEMPLOS = [
    "¿Qué vacunas corresponden a los 12 meses según el calendario PNI?",
    "¿Cuál es la meta de HbA1c para un adulto mayor frágil con diabetes tipo 2?",
    "¿Dónde registro una derivación a especialidad?",
    "¿Cuál es la dosis de metformina en un embarazo de alto riesgo?",
]

st.set_page_config(page_title="Asistente CESFAM Lo Franco", page_icon=":hospital:", layout="centered")

if "agente" not in st.session_state:
    st.session_state.agente = None
    st.session_state.mensajes = []
if "pregunta_pendiente" not in st.session_state:
    st.session_state.pregunta_pendiente = None

# ---------- Estilos ----------
st.markdown(f"""
<style>
.cesfam-header {{
    background: linear-gradient(135deg, {PRIMARY_DK} 0%, {PRIMARY} 100%);
    padding: 1.4rem 1.6rem;
    border-radius: 14px;
    margin-bottom: 1.2rem;
}}
.cesfam-header h1 {{
    color: #FFFFFF; font-size: 1.5rem; margin: 0 0 0.2rem 0; font-weight: 700;
}}
.cesfam-header p {{
    color: {LIGHT}; font-size: 0.92rem; margin: 0; opacity: 0.9;
}}
.cesfam-badge {{
    display: inline-block; background: {SECONDARY}; color: #FFFFFF;
    font-size: 0.72rem; font-weight: 700; letter-spacing: 0.04em;
    padding: 0.15rem 0.55rem; border-radius: 999px; margin-bottom: 0.5rem;
    text-transform: uppercase;
}}
.fuente-card {{
    border-left: 4px solid var(--tag-color, {PRIMARY});
    background: {LIGHT}; border-radius: 8px; padding: 0.55rem 0.8rem;
    margin-bottom: 0.5rem;
}}
.fuente-card .tag {{
    display: inline-block; font-size: 0.68rem; font-weight: 700;
    color: #FFFFFF; background: var(--tag-color, {PRIMARY});
    padding: 0.05rem 0.5rem; border-radius: 999px; margin-bottom: 0.25rem;
    text-transform: uppercase; letter-spacing: 0.03em;
}}
.fuente-card .nombre {{ font-weight: 700; color: {PRIMARY_DK}; font-size: 0.9rem; }}
.fuente-card .meta {{ color: #4B6668; font-size: 0.78rem; margin-top: 0.1rem; }}
.rechazo-box {{
    border-left: 4px solid {ACCENT}; background: #FCEEE9; border-radius: 8px;
    padding: 0.6rem 0.9rem; font-size: 0.85rem; color: {PRIMARY_DK};
}}
</style>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="cesfam-header">
  <h1>🩺 Asistente interno — CESFAM Lo Franco</h1>
  <p>Apoyo informativo para personal autorizado. No diagnostica, no reemplaza el
  criterio clínico y no interactúa con pacientes.</p>
</div>
""", unsafe_allow_html=True)


def _tarjeta_fuente(f: dict) -> str:
    es_interna = f.get("tipo") == "interna"
    color = PRIMARY if es_interna else SECONDARY
    etiqueta = "Fuente interna" if es_interna else "Fuente externa · MINSAL"
    return f"""
    <div class="fuente-card" style="--tag-color: {color};">
        <span class="tag">{etiqueta}</span>
        <div class="nombre">{f['fuente']}</div>
        <div class="meta">Vigencia: {f['vigencia']} &nbsp;·&nbsp; similitud (distancia): {f['distancia']}</div>
    </div>
    """


def _procesar_pregunta(pregunta: str) -> None:
    st.session_state.mensajes.append({"role": "user", "content": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)

    with st.chat_message("assistant"):
        with st.spinner("Consultando fuentes documentales..."):
            resultado = st.session_state.agente.preguntar(pregunta)
        st.markdown(resultado["respuesta"])
        if resultado["fuentes"]:
            st.markdown("**Fuentes consultadas**")
            for f in resultado["fuentes"]:
                st.markdown(_tarjeta_fuente(f), unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="rechazo-box">Sin fuente citada: el guardrail determinó '
                "que ningún documento respalda esta consulta con suficiente similitud."
                "</div>",
                unsafe_allow_html=True,
            )

    st.session_state.mensajes.append({"role": "assistant", "content": resultado["respuesta"]})


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

    st.caption("Demo: usuario `enfermera.jefa` o `admin.cesfam`, contraseña `cesfam2026`.")

else:
    with st.sidebar:
        st.markdown(f"""
        <div style="background:{LIGHT}; border-radius:10px; padding:0.8rem 1rem; margin-bottom:1rem;">
            <div style="font-size:0.75rem; color:{PRIMARY}; font-weight:700; text-transform:uppercase;">Sesión activa</div>
            <div style="font-size:1rem; font-weight:700; color:{PRIMARY_DK};">{st.session_state.agente.usuario}</div>
            <div style="font-size:0.8rem; color:#4B6668;">Rol: {st.session_state.agente.rol}</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("**Preguntas de ejemplo**")
        for ej in EJEMPLOS:
            if st.button(ej, key=f"ej_{ej}", use_container_width=True):
                st.session_state.pregunta_pendiente = ej

        st.divider()
        if st.button("Cerrar sesión", use_container_width=True):
            st.session_state.agente = None
            st.session_state.mensajes = []
            st.rerun()

    for m in st.session_state.mensajes:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    pregunta = st.chat_input("Escribe tu consulta sobre protocolos, guías o vacunación...")
    if st.session_state.pregunta_pendiente:
        pregunta = st.session_state.pregunta_pendiente
        st.session_state.pregunta_pendiente = None

    if pregunta:
        _procesar_pregunta(pregunta)
