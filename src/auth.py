"""Autenticación básica por usuario/rol.

Cumple el requerimiento de la propuesta: "el acceso al agente debe estar
restringido exclusivamente a personal autorizado del CESFAM mediante un
mecanismo de autenticación (usuario/rol)". No es un sistema de
credenciales de nivel productivo (para eso se usaría OAuth/SSO contra el
sistema institucional); para el piloto académico basta con validar contra
una lista de personal autorizado con contraseña hasheada.
"""
import hashlib
import json

from . import config


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def cargar_usuarios() -> dict:
    with open(config.USUARIOS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def autenticar(usuario: str, password: str) -> str | None:
    """Devuelve el rol del usuario si las credenciales son válidas y
    pertenece al personal autorizado; None en caso contrario.
    """
    usuarios = cargar_usuarios()
    registro = usuarios.get(usuario)
    if not isinstance(registro, dict):
        return None
    if registro.get("password_hash") != _hash(password):
        return None
    return registro.get("rol")
