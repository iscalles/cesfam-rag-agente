# Agente inteligente basado en LLM y RAG — CESFAM Lo Franco

Proyecto EP1, asignatura ISY0101 - Ingeniería de Soluciones con IA
(Duoc UC). Integrantes: Jose Salvatierra, Isabel Calles. Docente:
Cristián Cárcamo.

Agente conversacional de apoyo interno para el personal del CESFAM Lo
Franco: responde consultas sobre protocolos internos y guías clínicas
MINSAL (hipertensión, diabetes tipo 2, calendario de vacunación PNI),
citando siempre la fuente y fecha de vigencia del documento consultado, y
declarando explícitamente cuando no hay respaldo documental suficiente
para responder.

Ver `docs/arquitectura.md` para el diagrama y la justificación de
componentes, y `docs/prompts.md` para la justificación de los prompts.

## Requisitos

- Python 3.10 a 3.13
- Una API key gratuita de Mistral: https://console.mistral.ai/

## Instalación

```bash
# 1. Clonar el repo y entrar a la carpeta
git clone <url-del-repo>
cd cesfam-rag-agent

# 2. (Recomendado) crear un entorno virtual
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar credenciales
cp .env.example .env
# Editar .env y pegar tu LLM_API_KEY de Mistral
```

## Ejecución

```bash
# 1. Construir los índices vectoriales (una vez, o cada vez que
#    cambien los documentos en data/internal o data/external)
python -m src.ingest

# 2. Levantar la demo
streamlit run app.py
```

Se abre en `http://localhost:8501`. Usuarios de demo (ver
`data/usuarios.json`):

| Usuario           | Contraseña  | Rol            |
| ------------------ | ----------- | -------------- |
| `enfermera.jefa`   | `cesfam2026`| clinico        |
| `admin.cesfam`     | `cesfam2026`| administrativo |

## Interfaz

La app usa la misma paleta del informe y el PPT (tema definido en
`.streamlit/config.toml`). Al iniciar sesión, el panel lateral muestra el
usuario/rol conectado y botones de "preguntas de ejemplo" para no tener
que escribirlas a mano durante la demo. Cada respuesta muestra sus
fuentes como tarjetas de color (verde azulado = fuente externa MINSAL,
teal oscuro = fuente interna); si el guardrail rechaza responder, se
muestra en un recuadro naranja en vez de una tarjeta de fuente.

## Preguntas de ejemplo para la demo

- "¿Cuál es la meta de HbA1c para un adulto mayor frágil con diabetes
  tipo 2?" → debería responder citando `ges_diabetes_tipo2.md`.
- "¿Qué vacunas corresponden a los 12 meses según el calendario PNI?" →
  cita `pni_calendario_vacunacion.md`.
- "¿Dónde registro una derivación a especialidad?" → cita
  `manual_procedimientos.md` (fuente interna).
- "¿Cuál es la dosis de metformina recomendada para un embarazo de alto
  riesgo?" → pregunta fuera de las fuentes cargadas: el agente debe
  declarar que no cuenta con información suficiente, en vez de responder.

## Estructura del proyecto

```
app.py                      # Interfaz Streamlit (login + chat)
src/
  config.py                 # Configuración (rutas, credenciales, umbral)
  auth.py                   # Autenticación usuario/rol
  ingest.py                 # Chunking + embeddings + construcción FAISS
  rag_chain.py               # Agente: enrutamiento, memoria, guardrail,
                             # generación, trazabilidad
data/
  internal/                 # Manual de procedimientos y FAQ (simulados)
  external/                 # Guías GES MINSAL y calendario PNI (reales)
  usuarios.json             # Usuarios de demo
docs/
  arquitectura.md / .mmd / .png   # Diagrama y justificación (IE4/IE7)
  prompts.md                      # Justificación de prompts (IE2)
logs/
  trazabilidad.jsonl         # Se genera al usar la app: evidencia de
                              # pruebas (cada pregunta, fuente y respuesta)
```

## Notas importantes antes de la entrega final (semana 7)

- Los documentos en `data/external/` son **resúmenes** elaborados por el
  equipo a partir de las páginas y PDF oficiales de MINSAL/DIPRECE (las
  referencias completas están al final de cada archivo). Para el informe
  final, evaluar reemplazarlos por extractos más extensos o el PDF
  completo, y que sean validados por el equipo antes de citarlos como
  definitivos.
- El umbral `SIMILARITY_THRESHOLD` en `src/config.py` es un punto de
  partida: conviene afinarlo con pruebas reales y documentar en el
  informe cómo se calibró (parte de la "coherencia entre datos
  recuperados y respuestas" que pide IE6).
- Este es un proyecto académico simulado: no debe usarse con datos reales
  de pacientes ni conectarse a sistemas de producción del CESFAM.

## Declaración de uso de IA

Se utilizó IA (Claude, Anthropic) como apoyo para: estructurar el código
base del pipeline RAG, redactar la documentación técnica y generar
resúmenes iniciales de las guías MINSAL a partir de las fuentes oficiales
citadas en cada documento. Las decisiones de diseño, la validación del
contenido y las conclusiones/reflexiones del informe son del equipo.
[Completar/ajustar esta declaración según lo efectivamente usado por
ambos integrantes antes de entregar.]
