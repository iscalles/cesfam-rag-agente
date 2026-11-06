# Evidencias de pruebas

| Archivo | Cómo se genera | Qué demuestra |
| --- | --- | --- |
| `pruebas_automatizadas.txt` | `python -m pytest -v` | 21 pruebas del agente, sus herramientas y la integración HTTP, sin consumir la API |
| `escenarios_<fecha>.md` | `python -m scripts.escenarios` | 10 escenarios con la API real de Mistral: decisiones, herramientas, fuentes y respuesta de cada uno |
| `capturas/` | Capturas de la app en uso | Panel de razonamiento, fuentes, rechazo del guardrail y documento descargado |

## Escenarios con la API real

| # | Escenario | Condición que cambia | Qué se espera ver |
| --- | --- | --- | --- |
| 1 | Consulta clínica simple | — | `buscar_guias_minsal` y cita del calendario PNI |
| 2 | Pregunta de seguimiento ("¿Y a los 18 meses?") | Pregunta incompleta | Consulta reescrita con el contexto anterior |
| 3 | Fractura de tobillo | Sin respaldo documental | Mensaje fijo y consulta registrada como pendiente |
| 4 | Material de inducción | Tarea de varias etapas | `write_todos`, dos búsquedas y `guardar_documento` |
| 5 | ¿Está actualizada la guía de hipertensión? | Documento antiguo | `verificar_vigencia` → REVISAR |
| 6 | Meta de HbA1c como administrativo | Otro rol | Sin herramienta clínica; deriva a personal clínico |
| 7 | "Respóndeme en viñetas breves" | Preferencia del usuario | `recordar_preferencia` |
| 8 | Nueva sesión del mismo usuario | Sesión distinta | Respuesta en viñetas (memoria de largo plazo) |
| 9 | Pregunta con RUT | Dato de paciente | RUT omitido antes del modelo y del log |
| 10 | Modelo principal inexistente | Falla de la API | Cambio al modelo de respaldo |

Si aparece el límite de uso de Mistral, espera un minuto y corre de a uno:
`python -m scripts.escenarios --solo 4`.
