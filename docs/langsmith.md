# LangSmith — observabilidad y evaluación del agente (módulo IL1.4)

Este documento complementa `docs/arquitectura.md` y `docs/prompts.md`: explica
qué es LangSmith, por qué se incorporó al proyecto y cómo se usó, como
evidencia para el informe (aporta a IE5/IE6: coherencia entre datos
recuperados y respuestas, y evaluación del agente).

## Qué es

LangSmith es la plataforma de observabilidad y evaluación del ecosistema
LangChain. No es un componente del pipeline de respuesta (el agente funciona
igual si está desactivado): es una capa externa que registra y analiza lo que
el agente hace, para poder depurarlo y medir su calidad de forma sistemática
en vez de solo probarlo "a mano". Ofrece cuatro capacidades:

| Capacidad | Para qué sirve en este proyecto |
|---|---|
| **Trazabilidad completa** | Ver, de cada pregunta, el árbol completo de pasos: enrutamiento → recuperación (qué chunks y con qué distancia) → llamada al LLM (prompt exacto enviado) → respuesta. Útil para depurar por qué el agente citó (o no) determinada fuente. |
| **Gestión de datasets** | Mantener un conjunto de preguntas de prueba con la respuesta/comportamiento esperado, reutilizable cada vez que cambie el prompt, el umbral o los documentos. |
| **Evaluadores automáticos** | Correr ese dataset contra el agente y puntuar automáticamente cada resultado (en vez de releer todas las respuestas a mano). |
| **Análisis visual** | Comparar experimentos (p. ej. antes/después de cambiar `SIMILARITY_THRESHOLD`) en un dashboard. |

## Relación con las métricas RAGAS vistas en el módulo

El material del curso (IL1.4) presenta 4 métricas estándar para evaluar un
RAG, usables tanto con LangSmith como con la librería RAGAS:

| Métrica | Evalúa | Qué mide |
|---|---|---|
| Context Precision | Recuperación | De los chunks recuperados, cuántos son relevantes |
| Context Recall | Recuperación | De todos los chunks relevantes que existen, cuántos se recuperaron |
| Faithfulness | Generación | Si la respuesta se sostiene estrictamente en el contexto (clave contra alucinaciones) |
| Answer Relevancy | Generación | Si la respuesta aborda realmente la pregunta original |

Estas métricas genéricas sirven para un RAG abierto cualquiera. **Este
proyecto, en cambio, ya tiene un guardrail propio y más estricto que
Faithfulness/Answer Relevancy**: el agente rehúsa responder por completo si
ningún chunk cumple `SIMILARITY_THRESHOLD` (ver `src/config.py` y
`docs/prompts.md`), en vez de generar una respuesta parcial y luego medir
cuán "fiel" fue. Por eso, en `scripts/evaluar_langsmith.py` se optó por 3
evaluadores propios que verifican directamente ese diseño, en vez de
reimplementar Context Precision/Recall/Faithfulness con RAGAS:

- **`rechazo_correcto`**: ¿el agente respondió cuando debía, y rehusó cuando
  no había respaldo? (equivalente, para este agente, a lo que Faithfulness
  intenta prevenir — una respuesta sin base documental).
- **`fuente_correcta`**: cuando respondió, ¿citó el archivo fuente esperado?
  (equivalente a Context Precision para este caso: el chunk correcto fue el
  que efectivamente se usó).
- **`cita_formato`**: ¿la respuesta incluye la línea `Fuente: ...` exigida
  por la regla 3 del `SYSTEM_PROMPT`? (verifica el objetivo del proyecto de
  que el 100% de las respuestas citen su fuente).

Si en el informe se quiere mostrar además las métricas estándar, RAGAS se
puede agregar como librería adicional (`pip install ragas`) y correr sobre el
mismo dataset de `scripts/evaluar_langsmith.py` — no se incluyó por defecto
para no agregar una dependencia más sin necesidad, dado que el guardrail
propio ya cubre el mismo riesgo (alucinación) de forma más directa.

## Cómo se implementó

1. **Tracing automático** (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`,
   `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` en `.env`): `ChatOpenAI` y
   `OpenAIEmbeddings` (de `langchain-openai`) se auto-instrumentan al
   detectar estas variables de entorno — no requieren cambios de código.
2. **Decoradores `@traceable`** en `src/rag_chain.py` sobre `_enrutar`,
   `_recuperar` y `preguntar`: son lógica propia del equipo (no componentes
   nativos de LangChain), así que sin el decorador no aparecerían en las
   trazas aunque el tracing esté activo. Con esto, una traza en LangSmith
   muestra el árbol completo: enrutamiento → recuperación → generación.
3. **`scripts/evaluar_langsmith.py`**: dataset de 7 preguntas (las 4 del
   README más 3 adicionales) con el comportamiento esperado, evaluado con
   las 3 funciones descritas arriba. Si hay `LANGSMITH_API_KEY` configurada,
   sube el dataset y los resultados a smith.langchain.com (quedan ahí como
   un "experimento" navegable); si no, corre igual en modo local y deja la
   evidencia en `scripts/resultados_evaluacion.json` — para no obligar a
   nadie del equipo a crear una cuenta solo para ejecutar el agente.

## Cómo usarlo

```bash
# 1. (Opcional) crear cuenta gratuita en https://smith.langchain.com/ y
#    pegar la API key en .env (LANGSMITH_API_KEY, LANGSMITH_TRACING=true)

# 2. Construir los índices si no existen
python -m src.ingest

# 3. Correr la evaluación
python scripts/evaluar_langsmith.py
```

Sin configurar nada, el agente sigue funcionando exactamente igual: LangSmith
es una capa de observabilidad opcional, no una dependencia del pipeline de
respuesta.

## Nota de alcance

Este material corresponde al módulo IL1.4 del curso ("Evaluación y
Optimización de LLMs y RAG"), visto más adelante que la propuesta EP1
original. La pauta de EP1 revisada no lo exige explícitamente como entregable
obligatorio, pero se incorporó porque aporta evidencia directa a IE5/IE6
(coherencia datos-recuperados/respuesta y evaluación del agente) y porque
`langsmith` ya estaba contemplado como dependencia del proyecto. Conviene
confirmar con el profesor si esto cuenta como parte de la evaluación de EP1 o
si se documenta como trabajo adicional del equipo.
