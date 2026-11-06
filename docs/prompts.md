# Prompts e instrucciones del agente (EP2)

En un agente, las instrucciones no están solo en el prompt de sistema: también
lo están las descripciones de cada herramienta, porque el LLM las lee para
decidir cuál usar. Este documento reúne todos los textos que guían al agente.
El prompt de la EP1 se conserva en [`ep1/prompts_ep1.md`](ep1/prompts_ep1.md).

## 1. Prompt de sistema (`SYSTEM_PROMPT`, `src/agente.py`)

Se arma en **cada llamada al modelo** con el middleware `dynamic_prompt`:

```
SYSTEM_PROMPT  +  sección del rol  +  memoria de largo plazo  +  instrucciones de planificación
```

| Parte | Contenido | Cambia según |
| --- | --- | --- |
| Rol y alcance | Asistente interno; no atiende pacientes, no diagnostica | — |
| Uso de herramientas | Cuándo buscar, verificar vigencia, guardar, registrar pendientes, recordar preferencias | — |
| Regla 1 | Buscar antes de responder; usar solo lo que devuelven las herramientas | — |
| Regla 2 | Ante `SIN_RESPALDO`: reformular una vez; si persiste, declararlo y registrar la consulta | — |
| Regla 3 | Citar `Fuente: <documento> (vigencia: <fecha>)` y advertir vigencias a revisar | — |
| Regla 4 | En preguntas de seguimiento, escribir consultas de búsqueda completas | — |
| Regla 5 | No procesar datos identificables de pacientes; avisar si aparece `[RUT OMITIDO]` | — |
| Regla 6 | Español, breve y directo | — |
| Sección del rol (`_seccion_rol`) | Si el rol no tiene guías clínicas, no responder temas clínicos y derivar | Rol del usuario |
| Memoria (`MemoriaLargoPlazo.como_contexto`) | Preferencias, documentos más consultados, consultas de la sesión anterior | Usuario y lo que el agente guardó |
| Planificación (`PROMPT_PLANIFICACION`) | Usar `write_todos` solo en tareas de 3 o más pasos | — |

## 2. Descripciones de herramientas (`src/herramientas.py`)

Cada docstring es la "instrucción" que el LLM ve para esa herramienta.

| Herramienta | Le dice al LLM |
| --- | --- |
| `buscar_procedimientos_internos` | Para temas operativos (registro, derivación, agenda, box, inducción); escribir la consulta completa |
| `buscar_guias_minsal` | Para metas clínicas, fármacos según guía y vacunas por edad; escribir la consulta completa |
| `verificar_vigencia` | Para saber si una guía está actualizada; acepta parte del nombre |
| `guardar_documento` | Solo si el usuario pide un documento, solo con información de las búsquedas y con sus fuentes |
| `registrar_consulta_pendiente` | Cuando no hay respaldo o la consulta queda fuera del alcance |
| `recordar_preferencia` | Solo para preferencias estables de formato; nunca datos de pacientes |
| `write_todos` (LangChain) | Plan de pasos para tareas complejas |

## 3. Respuestas de las herramientas como señal para el agente

- `SIN_RESPALDO: ...` — la búsqueda no superó el umbral; el texto incluye la
  mejor distancia y la instrucción de no responder con conocimiento general.
- `estado: REVISAR: documento de 2018 (8 años)...` — cada fragmento trae el
  estado de vigencia calculado, para que el agente lo advierta.
- `ÍNDICE_NO_DISPONIBLE` — falta correr `python -m src.ingest`.

## 4. Mensajes fijos (no los genera el modelo)

| Mensaje | Cuándo |
| --- | --- |
| `_MENSAJE_SIN_RESPALDO` | Ninguna búsqueda del turno tuvo respaldo (garantía determinista) |
| `_MENSAJE_SERVICIO_NO_DISPONIBLE` | Fallaron el modelo principal, los reintentos y el modelo de respaldo |
| Aviso `⚠️ Vigencia` | Se citó un documento con más de 5 años y la respuesta no lo advertía |

## Justificación de la redacción

[Sección para el equipo: explicar con sus palabras por qué redactaron así las
reglas y las descripciones de herramientas. La pauta no permite redactar
justificaciones técnicas con IA.]
