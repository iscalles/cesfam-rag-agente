"""Memoria de largo plazo del agente, persistente por usuario.

La memoria de corto plazo (la conversación de la sesión) la maneja el
checkpointer de LangGraph dentro de `agente.py`. Esta clase guarda lo que
debe sobrevivir entre sesiones, en un JSON por usuario dentro de `memoria/`:

- preferencias: cómo quiere el usuario que le respondan (las guarda el
  propio agente con la herramienta `recordar_preferencia`).
- temas: cuántas veces se usó cada documento para responderle.
- ultima_sesion: las consultas de la sesión anterior, para retomar una tarea
  que quedó a medias.

Nunca se guardan datos de pacientes: todo pasa antes por `anonimizar`.
"""
import json
import os
import re
from datetime import datetime

from . import config
from .privacidad import anonimizar

MAX_PREFERENCIAS = 10
MAX_CONSULTAS_ULTIMA_SESION = 5


def _nombre_archivo(usuario: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", usuario) + ".json"


class MemoriaLargoPlazo:
    def __init__(self, usuario: str, carpeta: str | None = None):
        self.usuario = usuario
        self.carpeta = carpeta or config.MEMORIA_DIR
        self.ruta = os.path.join(self.carpeta, _nombre_archivo(usuario))
        self.datos = self._cargar()

    # ---------------------------------------------------------- persistencia
    def _cargar(self) -> dict:
        vacia = {"usuario": self.usuario, "preferencias": [], "temas": {}, "ultima_sesion": None}
        if not os.path.exists(self.ruta):
            return vacia
        try:
            with open(self.ruta, "r", encoding="utf-8") as f:
                datos = json.load(f)
            return {**vacia, **datos}
        except (OSError, json.JSONDecodeError):
            return vacia

    def _guardar(self) -> None:
        os.makedirs(self.carpeta, exist_ok=True)
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(self.datos, f, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------- escritura
    def agregar_preferencia(self, texto: str) -> str:
        texto = anonimizar(texto).strip()
        if not texto:
            return "No se guardó: la preferencia estaba vacía."
        prefs = self.datos["preferencias"]
        if texto.lower() in (p.lower() for p in prefs):
            return f"La preferencia ya estaba guardada: {texto}"
        prefs.append(texto)
        self.datos["preferencias"] = prefs[-MAX_PREFERENCIAS:]
        self._guardar()
        return f"Preferencia guardada para próximas sesiones: {texto}"

    def registrar_temas(self, fuentes: list[dict]) -> None:
        if not fuentes:
            return
        for nombre in {f["fuente"] for f in fuentes}:
            self.datos["temas"][nombre] = self.datos["temas"].get(nombre, 0) + 1
        self._guardar()

    def cerrar_sesion(self, consultas: list[str]) -> None:
        if not consultas:
            return
        self.datos["ultima_sesion"] = {
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "consultas": [anonimizar(c) for c in consultas][-MAX_CONSULTAS_ULTIMA_SESION:],
        }
        self._guardar()

    # ---------------------------------------------------------- lectura
    def como_contexto(self) -> str:
        """Texto que se agrega al prompt de sistema en cada llamada al modelo."""
        partes = []
        if self.datos["preferencias"]:
            partes.append("Preferencias guardadas del usuario (respétalas):\n"
                          + "\n".join(f"- {p}" for p in self.datos["preferencias"]))
        if self.datos["temas"]:
            top = sorted(self.datos["temas"].items(), key=lambda kv: kv[1], reverse=True)[:3]
            partes.append("Documentos que este usuario consulta con más frecuencia: "
                          + "; ".join(f"{t} ({n})" for t, n in top))
        sesion = self.datos.get("ultima_sesion")
        if sesion:
            partes.append(f"En su sesión anterior ({sesion['fecha']}) consultó:\n"
                          + "\n".join(f"- {c}" for c in sesion["consultas"])
                          + "\nSi el usuario dice que quiere retomar o continuar, usa esto como punto de partida.")
        if not partes:
            return ""
        return "\n\n## Memoria de largo plazo\n" + "\n\n".join(partes)
