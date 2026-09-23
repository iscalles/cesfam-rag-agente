# Formulación de prompts (IE2)

Este documento justifica el diseño de los prompts del agente, para el
apartado "Formulación de prompts" del informe.

## 1. Prompt de sistema (`SYSTEM_PROMPT` en `src/rag_chain.py`)

```
Eres el asistente interno del CESFAM Lo Franco. Apoyas únicamente al
personal clínico y administrativo del centro; NO interactúas con
pacientes, no entregas diagnósticos ni reemplazas el criterio clínico.

Reglas obligatorias:
1. Responde EXCLUSIVAMENTE con la información contenida en el CONTEXTO
   entregado en el mensaje del usuario. No completes vacíos con
   conocimiento general del modelo.
2. Si el CONTEXTO no permite responder con certeza, dilo explícitamente
   en vez de arriesgar una respuesta parcial o inventada.
3. Toda afirmación debe cerrar con una línea "Fuente: <nombre del
   documento> (vigencia: <fecha>)" por cada documento citado.
4. Nunca proceses, solicites ni registres datos identificables de
   pacientes (nombre, RUT, domicilio, etc.).
```

**Justificación de diseño:**

- **Rol y alcance explícitos** (primer párrafo): fija el público (personal
  del CESFAM) y excluye explícitamente el uso con pacientes o como
  herramienta diagnóstica, en línea con la restricción declarada en la
  propuesta ("el agente está dirigido exclusivamente al personal... no
  diagnostica ni reemplaza el criterio clínico").
- **Regla 1 (contexto cerrado):** es la instrucción central para
  minimizar alucinaciones: obliga al modelo a fundamentar cada respuesta
  únicamente en los chunks recuperados, no en su conocimiento
  paramétrico. Esto es indispensable en un dominio clínico donde una
  respuesta genérica del modelo puede estar desactualizada o ser
  incorrecta para el protocolo local.
- **Regla 2 (declarar incertidumbre):** operacionaliza el objetivo de la
  propuesta de no completar respuestas cuando no hay respaldo suficiente.
  Se refuerza además con un resguardo programático (umbral de similitud
  en `rag_chain.py`) para no depender solo del criterio del LLM.
- **Regla 3 (citación obligatoria):** traduce directamente el objetivo
  "Asegurar que el 100% de las respuestas del agente citen su fuente y
  fecha de vigencia del documento consultado" en una instrucción
  verificable; además, el código adjunta las fuentes recuperadas de forma
  independiente del texto generado por el LLM (ver `fuentes_usadas` en
  `rag_chain.py`), como resguardo ante el caso de que el modelo omita la
  cita.
- **Regla 4 (protección de datos):** traduce la restricción de la
  propuesta sobre la Ley N.º 20.584 y la Ley N.º 19.628 en una
  instrucción explícita del sistema.

## 2. Plantilla de consulta por turno (`_PLANTILLA_USUARIO`)

```
Pregunta del personal: {pregunta}

CONTEXTO RECUPERADO:
{contexto}
```

**Justificación:** separa claramente la pregunta original del usuario del
contexto recuperado automáticamente, evitando que el modelo confunda
ambas fuentes de información. Cada bloque de contexto se etiqueta con
tipo de fuente (interna/externa), nombre del documento y fecha de
vigencia, para que el modelo tenga directamente el material que debe
citar (ver `rag_chain.py`, construcción de `contexto`).

## 3. Prompt de enrutamiento (`_enrutar`)

En vez de usar una llamada adicional al LLM para clasificar cada consulta
(lo que agrega latencia y costo), el enrutamiento usa un diccionario de
palabras clave por tipo de fuente (clínicas/vacunación → externa;
administrativas/procedimientos → interna). Ante ambigüedad, consulta
ambas colecciones. Se documenta como una decisión de diseño deliberada:
prioriza recall (no perder una fuente relevante) sobre precisión de
enrutamiento, razonable en un contexto donde omitir una fuente clínica
pertinente es más riesgoso que hacer una búsqueda de más.

## 4. Mensaje de respaldo insuficiente

```
No cuento con información suficiente en las fuentes documentales
disponibles para responder esta consulta. Se recomienda consultar
directamente la guía correspondiente o a un/a profesional con mayor
experiencia.
```

**Justificación:** mensaje fijo (no generado por el LLM) para garantizar
que, ante la ausencia de respaldo documental, la respuesta sea siempre
predecible, no alucinada, y redirija al personal hacia una fuente humana
o documental confiable — coherente con la restricción de la propuesta de
"declarar explícitamente esa limitación en lugar de completar la
respuesta con conocimiento general del modelo".

## Declaración de uso de IA

Los prompts anteriores fueron redactados con apoyo de Claude (Anthropic)
para estructurar el texto y las reglas; la definición de qué reglas
incluir (alcance, no alucinación, citación obligatoria, protección de
datos) y su justificación técnica fueron definidas por el equipo en base
a los requerimientos de la propuesta aprobada. [Completar por el equipo
según lo efectivamente usado antes de entregar el informe.]
