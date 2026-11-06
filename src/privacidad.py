"""Protección de datos de pacientes (Ley N.º 20.584 y Ley N.º 19.628).

El agente no debe procesar ni almacenar datos identificables. Este módulo
detecta RUT chilenos en cualquier texto y los reemplaza antes de que lleguen
al modelo, al log de trazabilidad, a la memoria o a un documento escrito.
"""
import re

# RUT con o sin puntos y con guion: 12.345.678-9, 12345678-K, 9.876.543-2
RUT_REGEX = r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b"
_RUT = re.compile(RUT_REGEX)

MARCA_RUT = "[RUT OMITIDO]"


def contiene_rut(texto: str) -> bool:
    return bool(_RUT.search(texto or ""))


def anonimizar(texto: str) -> str:
    """Devuelve el texto con todos los RUT reemplazados por una marca."""
    return _RUT.sub(MARCA_RUT, texto or "")
