"""Pruebas de las herramientas por separado (consulta, razonamiento, escritura)."""
import json
import os
from datetime import datetime

from src import config
from src.herramientas import crear_herramientas, estado_vigencia
from src.memoria import MemoriaLargoPlazo
from src.privacidad import anonimizar, contiene_rut

HOY = datetime(2026, 11, 6)


def _herramientas(indices, embeddings, rol="clinico", usuario="enfermera.jefa"):
    memoria = MemoriaLargoPlazo(usuario)
    lista = crear_herramientas(indices=indices, embeddings=embeddings, usuario=usuario,
                               rol=rol, memoria=memoria, hoy=HOY)
    return {h.name: h for h in lista}, memoria


def _invocar_con_artefacto(herramienta, args):
    mensaje = herramienta.invoke({"type": "tool_call", "id": "t1", "name": herramienta.name, "args": args})
    return mensaje.content, mensaje.artifact


# ---------------------------------------------------------------- privacidad
def test_anonimiza_rut_en_distintos_formatos():
    texto = "Paciente 12.345.678-9 y 9876543-k consultan"
    assert contiene_rut(texto)
    limpio = anonimizar(texto)
    assert "12.345.678-9" not in limpio and "9876543-k" not in limpio
    assert limpio.count("[RUT OMITIDO]") == 2
    assert anonimizar("vacunas de los 12 meses") == "vacunas de los 12 meses"


# ---------------------------------------------------------------- razonamiento
def test_vigencia_marca_documentos_antiguos():
    assert estado_vigencia("Publicación 2018 — verificar", HOY)["estado"] == "revisar"
    assert estado_vigencia("Calendario oficial 2026", HOY)["estado"] == "vigente"
    assert estado_vigencia("sin fecha", HOY)["estado"] == "sin_fecha"


def test_verificar_vigencia_por_nombre_parcial(indices, embeddings):
    h, _ = _herramientas(indices, embeddings)
    texto = h["verificar_vigencia"].invoke({"documento": "hipertensión"})
    assert "Hipertensión" in texto and "REVISAR" in texto
    assert "vigente (2026)" in h["verificar_vigencia"].invoke({"documento": "calendario PNI"})


# ---------------------------------------------------------------- consulta + guardrail
def test_busqueda_con_respaldo_devuelve_fuente_y_vigencia(indices, embeddings):
    h, _ = _herramientas(indices, embeddings)
    contenido, art = _invocar_con_artefacto(
        h["buscar_guias_minsal"], {"consulta": "meta de HbA1c en adulto mayor frágil con diabetes tipo 2"})
    assert art["respaldo"] is True
    assert art["fuentes"][0]["archivo"] == "ges_diabetes_tipo2.md"
    assert "vigencia:" in contenido and "REVISAR" in contenido


def test_busqueda_sin_respaldo_no_entrega_texto_al_modelo(indices, embeddings):
    h, _ = _herramientas(indices, embeddings)
    contenido, art = _invocar_con_artefacto(
        h["buscar_guias_minsal"], {"consulta": "fractura expuesta de tobillo inmovilización con yeso"})
    assert art["respaldo"] is False and art["fuentes"] == []
    assert contenido.startswith("SIN_RESPALDO")
    assert "Metas de control" not in contenido  # ningún fragmento se filtra al modelo


# ---------------------------------------------------------------- permisos por rol
def test_rol_administrativo_no_tiene_guias_clinicas(indices, embeddings):
    h, _ = _herramientas(indices, embeddings, rol="administrativo")
    assert "buscar_guias_minsal" not in h
    assert "buscar_procedimientos_internos" in h


# ---------------------------------------------------------------- escritura
def test_guardar_documento_escribe_markdown_sin_rut(indices, embeddings):
    h, _ = _herramientas(indices, embeddings)
    contenido, art = _invocar_con_artefacto(h["guardar_documento"], {
        "titulo": "Ficha box vacunas",
        "contenido": "Atención sin hora de 08:30 a 16:00. Ejemplo paciente 11.111.111-1",
    })
    assert os.path.exists(art["ruta"])
    texto = open(art["ruta"], encoding="utf-8").read()
    assert "11.111.111-1" not in texto and "[RUT OMITIDO]" in texto
    assert "omitieron RUT" in contenido


def test_registrar_consulta_pendiente(indices, embeddings):
    h, _ = _herramientas(indices, embeddings)
    h["registrar_consulta_pendiente"].invoke({"consulta": "manejo de fractura de tobillo",
                                              "motivo": "sin respaldo en las fuentes"})
    linea = json.loads(open(config.PENDIENTES_PATH, encoding="utf-8").readline())
    assert linea["consulta"] == "manejo de fractura de tobillo" and linea["usuario"] == "enfermera.jefa"


def test_recordar_preferencia_persiste_en_disco(indices, embeddings):
    h, memoria = _herramientas(indices, embeddings)
    h["recordar_preferencia"].invoke({"preferencia": "respuestas en viñetas breves"})
    otra = MemoriaLargoPlazo("enfermera.jefa")  # simula una sesión nueva
    assert "respuestas en viñetas breves" in otra.como_contexto()
