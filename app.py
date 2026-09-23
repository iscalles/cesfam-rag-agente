"""Demo del agente CESFAM Lo Franco — interfaz de chat con autenticación
básica por usuario/rol.

Antes de correr esto, construir los índices una vez:
    python -m src.ingest

Ejecutar la demo:
    streamlit run app.py
"""
import streamlit as st

from src import auth
from src.rag_chain import AgenteCESFAM

st.set_page_config(page_title="Asistente CESFAM Lo Franco", page_icon=":hospital:")

if "agente" not in st.session_state:
    st.session_state.agente = None
    st.session_state.mensajes = []

st.title("Asistente interno — CESFAM Lo Franco")
st.caption(
    "Apoyo informativo para personal autorizado. No diagnostica, no "
    "reemplaza el criterio clínico y no interactúa con pacientes."
)

if st.session_state.agente is None:
    st.subheader("Ingreso de personal autorizado")
    with st.form("login"):
        usuario = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        enviar = st.form_submit_button("Ingresar")

    if enviar:
        rol = auth.autenticar(usuario, password)
        if rol is None:
            st.error("Usuario o contraseña incorrectos, o cuenta no autorizada.")
        else:
            st.session_state.agente = AgenteCESFAM(usuario=usuario, rol=rol)
            st.success(f"Bienvenido/a, {usuario} ({rol}).")
            st.rerun()

    st.caption("Demo: usuario `enfermera.jefa` o `admin.cesfam`, contraseña `cesfam2026`.")
else:
    for m in st.session_state.mensajes:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    pregunta = st.chat_input("Escribe tu consulta sobre protocolos, guías o vacunación...")
    if pregunta:
        st.session_state.mensajes.append({"role": "user", "content": pregunta})
        with st.chat_message("user"):
            st.markdown(pregunta)

        with st.chat_message("assistant"):
            with st.spinner("Consultando fuentes documentales..."):
                resultado = st.session_state.agente.preguntar(pregunta)
            st.markdown(resultado["respuesta"])
            if resultado["fuentes"]:
                with st.expander("Fuentes consultadas"):
                    for f in resultado["fuentes"]:
                        st.write(
                            f"- {f['fuente']} — vigencia: {f['vigencia']} "
                            f"(distancia: {f['distancia']})"
                        )

        st.session_state.mensajes.append({"role": "assistant", "content": resultado["respuesta"]})

    st.divider()
    if st.button("Cerrar sesión"):
        st.session_state.agente = None
        st.session_state.mensajes = []
        st.rerun()
