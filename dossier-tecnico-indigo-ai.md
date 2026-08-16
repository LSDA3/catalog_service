# Dossier técnico de la plataforma indigo.ai

Base de referencia para diseñar la arquitectura del agente y de la base de datos.
Fuente: guide.indigo.ai (documentación pública completa, ~100 páginas). Fecha: 13 ago 2026.

---

## 1. Modelo mental: jerarquía de objetos

```
Workspace  (contenedor de nivel superior, 1 por proyecto/entorno)
├── Mother Agent          ← orquestador implícito, no editable directamente
├── Agents                ← especialistas basados en prompt + KB
│   └── General Agent     ← fallback obligatorio, no borrable
├── Workflows             ← flujos deterministas de bloques
│   └── Welcome Workflow  ← no borrable ni renombrable, arranca la sesión
├── Folders               ← organización por dominio (Orders, Returns…)
├── Knowledge Base        ← Docs & URLs (+ tags) e integración vía API
├── Variables             ← custom, de sistema ($…) y Secrets
├── Integrations          ← Tools (OpenAPI), MCP servers, API blocks
├── Evaluators & Guardrails
└── Settings              ← Agent Settings globales, canales, equipo, widget
```

**Regla estructural clave:** el workspace es la unidad de aislamiento. Los datos *nunca* se comparten entre agentes ni entre workspaces. Un entorno = un workspace.

---

## 2. Runtime: cómo se resuelve un turno

1. Llega un mensaje por un canal (widget, WhatsApp, voz, Chat API).
2. Se resuelve o crea la **sesión** (ver §9).
3. El **Mother Agent** hace un *ranking dinámico* de agentes y workflows de nivel superior según sus **triggers** (descripción + preguntas de ejemplo) y el contexto conversacional, y delega en el mejor candidato.
4. El agente/workflow elegido ejecuta:
   - Un **Agent** compone un prompt (sus secciones + tono + reglas + memoria corta + documentos recuperados de la KB por tag) y llama al LLM.
   - Un **Workflow** ejecuta bloques en secuencia determinista.
5. Guardrails de pre-check se aplican en vivo sobre cada mensaje; los evaluadores clásicos corren post-hoc al cerrar la sesión.
6. Si nada matchea → **General Agent** (fallback).

**Consecuencia de diseño:** el routing es semántico, no basado en intents entrenados. La calidad del enrutado depende casi por completo de cómo estén escritos los *triggers*. Triggers solapados = enrutado errático. Solo los objetos de nivel superior llevan trigger; los secundarios se invocan por **Reroute Block**.

---

## 3. Agentes: anatomía

### Secciones del prompt (bloque Agent)

| Sección | Obligatoria | Notas |
|---|---|---|
| General Description | Sí | tarea concreta del agente |
| Agent Goal | Sí | rol y alcance, qué preguntas atiende |
| Company Description | No | historia, misión, valores |
| Tone of Voice | No | estilo |
| Brand Rules | No | hasta 10 términos prohibidos con sustituto |
| Useful URLs | No | enlaces a citar explícitamente |
| General Rules | No | hasta 10 reglas duras |
| Additional Sections | No | libres, vía "Add Section" |
| Conversation Examples | No | few-shot; opción *Pick Examples Dynamically* |

Cada sección muestra su consumo de tokens con contador acumulado. **Recomendación oficial: mantener el total del agente entre 4.000 y 5.000 tokens.** Ese es el presupuesto real de diseño.

### Agent Settings (por agente, heredan de los globales)

- **Documents to Use – Tags**: qué tags de la KB puede leer. Alternativas: usar una variable, no usar documentos, o todos. Opción de *source citations*.
- **Short Memory**: por defecto **3 mensajes**. Opciones: none, 1–5, 10, 20, 50, whole context.
- **Hypercontrol**: validación extra de la salida. Aumenta latencia.
- **AI Settings**: Creativity (Low/Medium/High/Custom 0–1.00), Model, Max Answer Tokens, **Max Document Tokens (default 2048)**.
- **Error Handling**: conectar otro agente o mensaje custom (default: *"Something went wrong. Please try again."*).
- **Tools**: proveedor + acción (ver §7).
- Toggles: User Feedback (👍/👎), Typebar (permitir texto libre o forzar botones).

Los agentes heredan de **Agent Settings** globales del workspace salvo edición local → se marcan como **OVERWRITTEN**. "Reload agent" revierte a global sin perder título, triggers ni conexiones.

### Conexión de salida
`Connection` define el destino siguiente, con opciones **Save Output** (a variable) y **Write Output in Chat** (mostrar o no la respuesta). Esto permite usar un agente como paso interno silencioso.

---

## 4. Workflows y catálogo de bloques

Workflows = caminos conversacionales deterministas, construidos con drag & drop. Opcionales (los agentes no lo son). Trigger solo si son proceso primario.

### Message blocks

| Bloque | Límites duros |
|---|---|
| **Text** | **300 caracteres**/bloque; recomendado máx. 3 bloques (900 car.) |
| **Image** | PNG/JPEG/GIF, **máx. 5 MB**, alt text 100 car. |
| **Card** | título **55** car., descripción **85** car. (3 líneas), hasta **2 botones** de **20** car., imagen alt. máx 244 px; carrusel hasta **10 cards** (mismo nº de botones en todas) |
| **Video** | URL de YouTube, Wistia, Vimeo, Google Drive; alt 100 car. |

### Action blocks

| Bloque | Puntos clave |
|---|---|
| **API** | GET/POST/PUT/PATCH/DELETE/COPY/HEAD/OPTIONS. Headers + body (editor JSON). Credenciales vía **Secrets** (1 secreto por campo). *Capture Variables* mapea el JSON de respuesta a variables. Rutas **Success** y **Error** separadas. Botón "Send request" para probar. |
| **Mail** | Destinatarios múltiples (coma), CC, Reply-To. Remitente: nombre libre pero **dirección @indigo.ai** salvo configuración específica en Postmark. Cuerpo texto o HTML (sin JS). Sin adjuntos documentados. |
| **Quick Reply** | hasta **10 botones**; destinos: agente, workflow, teléfono, URL, `mailto://`, o variable de tipo Agent/Workflow. Puede fijar variables con `set_variable`. |
| **Handover** | Waiting message, franjas de disponibilidad de operador, mensaje "no operator available", mensaje "contact center closed". Operadores dentro de la plataforma (Human Takeover debe activarlo soporte). |
| **Upload** | **5 MB** por defecto, ampliable a **25 MB**. El archivo queda en una variable y accesible en Chats. |
| **Event** | Dispara un evento del catálogo, asíncrono con reintentos. Metadatos globales + locales (los locales ganan). Metadatos inválidos se guardan con flag de error. |
| **Hang up** / **Transfer call** / **Digit** | Solo canal voz telefónico; se ignoran en texto y web calls. Transfer admite número dinámico, música de espera, horarios y rutas de fallback por tipo de fallo. Digit = menús DTMF con "no match" obligatorio. |

### Logic blocks

| Bloque | Puntos clave |
|---|---|
| **Condition** | Operadores según tipo de variable. Combinación AND / NOT AND / OR / NOT OR. Evaluación **secuencial de arriba abajo**, gana la primera verdadera. **No se pueden anidar Condition blocks**. El "else" se emula con la variable `$true`. |
| **Capture** | Bloqueante. Tipos: Text, Number, Boolean, Date/Time. Reintenta hasta obtener formato válido. Modo obligatorio u opcional (con botón Cancel redirigible). |
| **Collect** | Extracción semántica de varios datos a la vez desde lenguaje natural. Por punto de dato: variable destino, descripción/regex, prompt automático si falta, lista de valores admisibles con sinónimos, y *shots* de ejemplo. **Este es el bloque correcto para formularios conversacionales**; Capture es para campos únicos y rígidos. |
| **Set Values** | Asigna fijo / null / empty / boolean; admite `REGEX(variable, 'patrón')`. **Evalúa todo simultáneamente**: si necesitas encadenar transformaciones, usa bloques separados. |
| **Reroute** | Redirige a agente, workflow o variable. Opción **"Resume from here"** = comportamiento tipo pila, vuelve al punto de origen al terminar. |

### Utility blocks

| Bloque | Puntos clave |
|---|---|
| **Prompt** | Composer system/user/assistant. Params: max tokens (**default 256**), max document tokens (default 2048), temperature, reasoning effort (gpt-5.x, gemini-2.5-pro), prompt language, modelo, short memory 0–50 o contexto completo. **JSON Output Mode** + *Variable Assignment* con formato `response.key`. Recomendaciones: prompt caching (estático arriba, dinámico abajo) y campo `reasoning` como primera clave del JSON para chain-of-thought. |
| **Metadata** | Inyecta JSON estructurado no visible al usuario (voz, CRM, analytics, estado de flujo). Editores Text/Tree/Table. JSON malformado se ignora. Solo se procesa si la condición que lo envuelve se cumple. |
| **Notes** | Documentación interna, no ejecutable. |

---

## 5. Variables, funciones y secretos

### Tipos de dato
Text, Number, Boolean, Date/Time, Agent/Workflow, **Map** (objeto clave-valor), **List** (array).

> Restricción importante: **Map y List se actualizan sobrescribiendo el objeto entero**; no se puede modificar un campo suelto.

### Sintaxis
`{{variable}}` · `{{mapa.clave}}` · `{{lista[0]}}` · `{{mapa.n1.n2}}` · secretos: `{{secrets.NOMBRE}}`

### Configuración
Nombre único + tipo obligatorios. Opcionales: **Test Value** (para probar API blocks) y **Fallback Value** (default `null`).

### Ámbito y ciclo de vida
Ámbito de **sesión**. Buena práctica oficial: **inicializar todas las variables en el Welcome Workflow con un Set Values**, y resetearlas a `null`/`empty` al cerrar flujos, para que no arrastren datos entre usuarios o entre sesiones.

### Variables de sistema (selección relevante)

| Variable | Contenido |
|---|---|
| `$project_id` | ID del workspace |
| `$env` | `TEST` o `PRODUCTION` |
| `$user_id` | ID numérico interno de plataforma |
| `$user_ref` | Identificador externo aportado por la integración |
| `$chat_id` | ID del chat actual |
| `$message_id` | ID del último mensaje entrante |
| `$total_interactions` | Turnos de usuario respondidos en la sesión |
| `$last_user_message` | Último mensaje del usuario |
| `$conversation` | Render de la sesión, **máx. 100 turnos** |
| `$context` | Render completo sin límite de turnos |
| `$context_1` … `$context_5` | Últimos 1–5 pares usuario/agente |
| `$documents` | Documentos recuperados de la KB |
| `$intent` | Última etiqueta de intención reconocida |
| `$lang`, `$detected_language`, `$detected_language_iso`, `$fallback_language` | Idioma |
| `$current_workflow`, `$previous_workflow`, `$handoff_source` | Estado de enrutado |
| `$timestamp`, `$date`, `$date_year/_month/_weekday/_hour` | Tiempo (zona horaria del workspace) |
| `$install_url`, `$platform_endpoint` | Contexto de instalación |
| `$true` | Constante verdadera (para el "else") |

### Funciones (pipeline tipo Liquid)
`{{ var | f1 | f2: arg1, arg2 }}`, encadenadas de izquierda a derecha, **el resultado siempre es texto**.

Familias disponibles: general (`default`), numéricas (`abs`, `round`, `plus`, `divided_by`, `random`…), texto (`append`, `slice`, `truncate`, `strip_html`, `translate`…), **regex** (`regex`, `regex_replace`), codificación (`base64_*`, `url_*`, `escape`), split/join/`format_list`/`to_integer`/`to_float`, **fechas** (`date`, `parse_date`, `add_time`, `shift_datetime_timezone`, `format_datetime`, `compare_with_date`), **JSON** (`get`, `json_decode/encode`, `json_to_markdown`, **`json_path`** con filtros JSONPath), **CSV** (`csv_to_json`, `csv_to_markdown`), **listas** (`where`, `unique_by`, `sort`, `sum`, `contains`, `push/pop`…), e imágenes (`merge_images`).

> `json_path`, `where` y `unique_by` son la capa práctica de "consulta" sobre datos traídos por API. Es lo más parecido a lógica de query que hay dentro del flujo.

### Secrets
Cifrados en el workspace, resueltos **server-side** (nunca llegan al LLM ni al usuario). Nombre inmutable. Visibilidad **Masked** o **Restricted**, elegida al crear y **no modificable después**. Admins/Owners crean y editan; Editors solo ven. Se usan en API blocks (uno por campo) y en headers de Tools/MCP.

---

## 6. Knowledge base y RAG

Dos vías, combinables:

**(a) Documentos y URLs (estático)**
- Formatos: `.pdf`, `.docx`, `.txt`, `.csv`, `.xlsx`. Sin archivos protegidos por contraseña. Sin límite documentado de nº de documentos.
- **Tags**: solo letras y números, sin caracteres especiales. El tag es el mecanismo de control de acceso: cada agente ve solo los tags asignados. **Evitar tags duplicados en contenidos no relacionados.**
- Procesamiento avanzado (pdf/doc/docx): OCR con detección de manuscrito (off por defecto), multilingüe (on), modo **Standard o Agentic**; extracción **OCR / Metadata / Hybrid**; **chunking LLM-based (default) o por tokens, rango 100–700**.
- **View as Sections**: cada chunk es una fila editable (contenido, título, metadatos, columnas personalizadas, operaciones en lote). Los cambios se sincronizan con la base y los metadatos quedan disponibles para el modelo.
- **La actualización es manual**: hay que pulsar *Refresh* / re-subir. No hay sincronización automática.

**(b) Integración por API (dinámico)**
Patrón documentado: `Prompt Block` (extrae parámetros de la consulta en JSON) → `API Block` (consulta el sistema real) → `Agent Block` que referencia esa variable en una sección custom. El agente responde con datos en vivo en lugar de con documentos estáticos.

**Criterio oficial de elección:** contenido estático y de bajo volumen (FAQs, políticas) → documentos. Datos que cambian, volumen alto o estructura compleja → API. Literal de la guía: *"Product catalogs should always be integrated via API"*.

**Calidad de KB** (esto condiciona el resultado más que el modelo): pares pregunta-respuesta en tabla de 2 columnas, un tema por documento, Markdown, UTF-8, URLs escritas completas (no hipervínculos), terminología consistente. Antipatrones: PDFs escaneados, scraping sin estructura, logs de tickets como única fuente, temas mezclados, celdas combinadas en xlsx.

**RAG:** la guía confirma embeddings vectoriales + recuperación + contexto aumentado + citaciones, pero **no publica** el vector store, la estrategia de reranking ni la de indexación. → zona gris a confirmar (§16).

---

## 7. Integraciones: tres mecanismos

| Mecanismo | Cuándo | Cómo |
|---|---|---|
| **API Block** | Llamada determinista dentro de un flujo | HTTP configurado a mano, secrets, mapeo a variables, ruta de error |
| **Tools** | El LLM decide cuándo llamar (function calling) | Modelo **proveedor → acción**, definido desde una **especificación OpenAPI** importada. Configurado en *Tools settings* y reutilizable. Soporta invocación **paralela** (acciones independientes) y **secuencial** (dependientes). En un API Block: solo una acción por integración |
| **MCP Servers** | Exponer un conjunto de herramientas externas sin construirlas una a una | Agent Settings → Integrations: nombre + **URL base** (sin `/mcp`) + headers HTTP opcionales (con secrets). Estados 🟢 conectado / 🔴 fallo / ⚪ sin conectar; reconexión automática al siguiente uso. Selección de herramientas por checkbox en el Agent Block |

Integraciones nativas citadas: **Zendesk**, **Shopify**, Google Sheets (consultas SQL sobre hoja, pública o vía service account), Gmail. Por API: Salesforce, HubSpot, SAP, Microsoft Dynamics, Magento, PrestaShop, Vivocha.

---

## 8. Canales

| Canal | Notas y limitaciones |
|---|---|
| **Web Chat** | Widget v3. Disponible en todos los planes. Persistencia de sesión 10 min. LaTeX y notación química renderizables |
| **WhatsApp** | Requiere número registrado en Meta + WhatsApp Business + Meta Business Account. **Máx. 3 botones de 20 caracteres**, sin hipervínculos (URLs completas), sin caption en botones/imágenes, sin indicador de escritura, typebar siempre activa. Carruseles sí |
| **Voice** | Telefonía (web pendiente). TTS/STT con **ElevenLabs** y **AudioCodes**, 100+ idiomas. Reutiliza los mismos agentes y workflows. Bloques exclusivos: Hang up, Transfer call, Digit, Metadata |
| **CRM (Zendesk, Salesforce, Vivocha)** | Widget embebido en el CRM. **La typebar no se puede desactivar con quick replies** → hay que asumir texto libre siempre |
| **Chat API (canal custom)** | `POST https://platform.indigo.ai/chat/:project_token/send`, auth `Bearer pat-…`. Body: `sender`, `source`, `data` (`text` \| `payload` \| `profile`). Toda conversación arranca con payload `init`. Respuesta en **streaming por chunks**: `processing.start` → text / generation.chunk / button / carousel.* / url → `processing.end` |

Licencias: Web Chat en todos los planes; **el resto de canales requieren licencia Super o Elite**; analytics por canal solo Elite.

### Widget: superficie programática
- Objeto global `IndigoAIChat`; evento `indigo-ai-widget-loaded`.
- Métodos: `setOpen(bool)`, `sendMessage({type:'text'|'postback', …})`, `setVariables({…})`, `showCsat()`.
- Eventos suscribibles (`on`/`once`/`off`): `$message-sent`, `$message-received`, `$answer`, `$user-data-sent`, `$set-open`.
- Paso de variables al cargar: atributos `data-*` (recomendado) o query-string `init=` URL-encoded.
- `uid` / `localStorage['indigo-ai-widget-uid']` fija el `$user_ref`. Debe establecerse **antes** de cargar el widget.
- Parámetros de script: `autostart=on`, `fullpage=on`, `fullpage_close=off`, `node=ID`. Nodos DOM: `iaw-container`, `iaw-trigger`, `iaw-bubble`, `iaw-chat`.

---

## 9. Sesiones e identidad (crítico para el modelo de datos)

- **Sesión** = intercambio ininterrumpido actual, ligado a un `chat_id`. **Conversación** = todo el historial de un mismo `$user_ref`.
- `$user_ref` externo (cookie del widget, número de WhatsApp, número llamante o valor propio) y `$user_id` interno numérico. **Ambos persisten entre sesiones y no rotan.**
- La sesión termina por: **timeout de inactividad (default 10 min)**, envío de CSAT, o reset explícito (botón "Start a new chat" / evento `init`).
- **Se resetean**: variables capturadas, analítica del chat, `$conversation`, `$context`.
  **Persisten**: `$user_id`, `$user_ref`, variables de perfil de usuario.
- `$conversation` y `$context` están **limitados a la sesión actual**: no hay memoria nativa entre sesiones.

> **Implicación directa para la BD:** si el caso de uso necesita memoria a largo plazo, historial de cliente o estado entre sesiones, hay que persistirlo fuera de indigo.ai y recuperarlo por API al inicio de cada sesión, con `$user_ref` como clave de unión. Esta es probablemente la decisión estructural más importante del diseño.

---

## 10. Calidad, observabilidad y operación

- **Debugging**: por mensaje, timeline vertical con saltos entre agentes/workflows, historial de variables con valores y bloque que los cambió, prompts completos (input/output) con modelo y latencia en ms, llamadas API con headers y body, y **desglose de tiempo de respuesta** por componente (prompt, LLM, API, retrieval de KB).
- **Evaluators** (post-hoc, sobre la conversación cerrada) y **Guardrails** (pre-check en vivo, por mensaje).
  Built-in: Chat Success (1-10), AI CSAT (1-5), User Sentiment, Tone Consistency, Repetition Presence, Escalation Appropriateness, Harmful Content, Language Coherence, **Hallucination** (guardrail), **Jailbreak Detection**, Response Formatting Check, Keyword Presence, **PII Detection** (guardrail), Insights Extractor.
  Custom: Label, 1-10, Boolean y Guardrail. Acciones del guardrail: fallback, redirección o sanitización.
- **Simulations**: role-play automatizado. Cada simulación = escenario + criterios de éxito + límite de mensajes + variables iniciales. Creación manual, asistida, importación CSV o desde conversaciones reales fallidas. Resultado Pass/Fail/Error con explicación. **Límites: 200 simulaciones por run y 500 por workspace cada 5 horas.** La suite **"No Regression Test"** bloquea la publicación si falla → puerta de calidad del pipeline.
- **Conversation Logs**: exportables a CSV, con resultados de evaluadores, CSAT, errores de API, assignee y estado de revisión.
- **Issue Tracker**: identificador autogenerado, prioridad, tags (Bug/Improvement/Feature), estados Backlog → In Progress → Resolved/Closed.
- **Events**: catálogo de acciones de negocio con *event key* única y **esquema de metadatos**. Estados activo/deshabilitado/archivado. Export CSV asíncrono por email. Pueden actualizar variables y enviarse por webhook a sistemas externos. → **es el mecanismo nativo para instrumentar KPIs de negocio.**
- **Analytics**: usuarios, mensajes, chats, handover (rate, directas, tras fallback, por botón), engagement/returning rate, feedback 👍👎, CSAT 1-5, tráfico por día/hora, clicks (tipo, título, destino), vistas por agente/workflow. **No se puede descargar desde la UI**: se accede por API.

---

## 11. APIs de plataforma

**Auth global:** Personal Access Token → `Authorization: Bearer pat-<valor>`. Acceso a API = **plan premium con coste adicional**.

| Superficie | Endpoint / detalle |
|---|---|
| **GraphQL** (principal) | `POST https://platform.indigo.ai/graphql`. Queries: `conversations`, `messages`, `userRoles`. Paginación cursor estilo Relay (`pageInfo.endCursor`, `hasNextPage`, `after`/`before`). Mensajes en orden cronológico inverso; `sender: null` = mensaje del asistente |
| **Non-Conversational Triggers** | `POST /rest/trigger/async/{project_token}` (responde `{"status":true}` al instante) y `/rest/trigger/sync/{project_token}` (espera y devuelve `messages`). Motor nuevo: `https://clair.platform.indigo.ai/trigger/{sync\|async}/`. Body: `{"target": "<label del agente>", "data": {…}, "sender": "…"}`. `data` admite List y Map y **no requiere predefinir las variables**. Es la vía para disparos proactivos/outbound desde sistemas externos |
| **Analytics REST** | `GET /rest/analytics/{project_id}/messages/daily`, `/rest/dashboard/{project_id}/chats/count`, `/users/unique-daily` (HyperLogLog, ~2% error), `/csat/daily`, `/ai_quality/daily` (score 0-1), `/errors/daily`. Params `from` (incl.) / `to` (excl.), `limit` 1–365 (default 30), cursor base64. **Rate limit: 100 req / 60 s por PAT** (429 con `retry_after`) |
| **Evaluator Outcomes** | `POST /rest/evaluator_outcome/{project_id}` con `{name, value, chat_id, reasoning?, force?}`. Solo evaluadores Label / 1-10 / Boolean **activos**; guardrails no. Errores 403/404/409/422 |
| **Chat API** | Ver §8 |

---

## 12. Entornos y ciclo de vida (Enterprise Architecture)

Cinco entornos, cada uno un workspace clonado: **DEV → TEST → UAT → STAGING → PRODUCTION**, con aislamiento de red entre ellos.

- La edición ocurre **solo en DEV**; los demás son de solo lectura.
- **Sync** = mecanismo estándar de promoción entre niveles. **Publish** = activación en PRODUCTION (go-live y hotfix excepcional).
- **Se propaga**: agentes, workflows, knowledge base, APIs, configuración de widget.
- **NO se propaga**: historial de conversaciones, analytics, evaluadores, guardrails y **secrets**. → hay que reconfigurarlos por entorno.
- Las **variables** permiten cambiar valores por defecto en entornos superiores manteniendo la estructura gobernada por Sync → así se evita duplicar endpoints por entorno. **Este es el patrón correcto para endpoints y credenciales por entorno.**
- Los **hotfixes en producción no retropropagan**: hay que recrearlos en DEV y volver a promocionar.

En un workspace simple (sin Enterprise) el ciclo es **Draft → Preview/Test token → Publish → Live**. El entorno de test se actualiza automáticamente con cada cambio; el token *live* solo refleja lo publicado.

---

## 13. Seguridad y cumplimiento

- **ISO 27001**, **GDPR**, **EU AI Act** (indigo.ai no se clasifica como proveedor de IA de alto riesgo).
- Art. 50 AI Act: obligación de informar de que se interactúa con IA → *AI Disclosure Badge* en el widget y aviso al inicio en llamadas de voz.
- Cifrado en tránsito y en reposo, TLS 1.3 (mín. 1.2), gestión de secretos sobre HashiCorp Vault (IBM Secrets Manager).
- **Anti-jailbreak en dos capas**: PromptShield de Azure (clasifica safe/unsafe) + agente especializado en el mother prompt. Detecta ofuscación lingüística/base64, mundos imaginarios, retórica y manipulación de tokens. Dispara fallback; **no hay parámetros configurables documentados**.
- **Audit logs**: usuario + fecha/hora + acción, sobre publish, chats (takeover), documentos, utilities, agent settings, variables & secrets, settings y builder. Filtrado y export CSV. Externamente centralizados en Splunk. **Retención no documentada.**
- **SSO**: Google Workspace, SAML 2.0 (Entra ID, Okta, Auth0, OneLogin, Ping) y OIDC. **MFA** por código a teléfono/email. Chat SSO autentica al usuario dentro del chat. Activación vía soporte.
- **Roles**: Owner, Admin, Editor, Manager, Operator, Viewer + roles personalizados (parten de Viewer). Solo Owner/Admin gestionan roles.
- Privacidad del widget: URL de política obligatoria si se activa, checkbox de consentimiento previo al primer mensaje, almacenamiento por sesión.

---

## 14. Infraestructura y persistencia de la plataforma

- SaaS en **IBM Cloud, región Frankfurt**. Frontend React, backend **Elixir** con API GraphQL.
- **PostgreSQL 16 gestionado por Neon (serverless)** para datos de aplicación; almacenamiento de objetos S3-compatible en IBM Cloud para documentos.
- **Redis** para caché y procesamiento en tiempo real.
- LLMs servidos desde centros de datos de la UE (ver tabla §15).
- Conversaciones cifradas en tránsito y reposo, sesiones identificadas por UUID. **Retención por defecto de datos conversacionales: 30 días**, luego borrado automático.
- Los datos no se comparten entre agentes ni se almacenan en servicios de terceros.

---

## 15. Modelos LLM disponibles

| Modelo | Backend | Proveedor | Región | Perfil |
|---|---|---|---|---|
| **gpt-4.1-mini (EU)** ← *default* | gpt-4.1-mini-2025-04-14 | Azure | Suecia | Velocidad |
| gpt-4.1 (EU) | gpt-4.1-2025-04-14 | Azure | Suecia | Potencia |
| gpt-4.1-nano (EU) | gpt-4.1-nano-2025-04-14 | Azure | Suecia | Velocidad |
| gpt-5.1 (EU) | azure-se-gpt-5.1 | Azure | Suecia | Razonamiento |
| gpt-5-mini / gpt-5-nano (EU) | azure-se-gpt-5-* | Azure | Suecia | Razonamiento |
| gemini-2.5-pro (EU) | gemini-2.5-pro | Google | Bélgica | Razonamiento |
| gemini-2.5-flash (EU) | gemini-2.5-flash | Google | Bélgica | Potencia |
| gemini-2.5-flash-lite (EU) | gemini-2.5-flash-lite | Google | Bélgica | Velocidad |
| claude-4.5-sonnet (EU) | claude-sonnet-4-5@20250929 | Google Vertex | Bélgica | Potencia |
| claude-4.5-haiku (EU) | claude-haiku-4-5@20251001 | Google Vertex | Bélgica | Velocidad |
| mistral-small-3.2 (EU) | mistral-small-2506 | Mistral | Suecia | Velocidad |
| gpt-oss-120b / gpt-oss-20b (EU) | openai/gpt-oss-* | Groq | UE | Potencia / Velocidad |
| maestrale-chat | mii-llm/maestrale-chat-v0.4 | indigo.ai | Alemania | Personalizado |

Criterio: *Velocidad* para tiempo real (voz, alto tráfico — los de Groq destacan en latencia), *Potencia* para tareas complejas, *Razonamiento* cuando hace falta deliberación previa (a costa de latencia).

**Multilingüe:** detección automática de idioma y respuesta en el mismo, 120+ idiomas, sin configuración. **Pero el contenido estático no se traduce solo**: mensajes de bienvenida, textos del widget y Text blocks requieren el add-on multilingüe (activación por soporte, coste por idioma adicional).

---

## 16. Tabla resumen de límites duros

| Elemento | Límite |
|---|---|
| Prompt de agente | 4.000–5.000 tokens recomendado |
| Short memory agente | default 3 mensajes; máx. 50 o contexto completo |
| Max Document Tokens | 2048 por defecto |
| Prompt Block max tokens | 256 por defecto |
| Brand Rules / General Rules | 10 cada una |
| Text block | 300 caracteres |
| Card | título 55 / descripción 85 / botón 20 / 2 botones / 10 cards por carrusel |
| Quick Reply | 10 botones (3 de 20 car. en WhatsApp) |
| Imagen | 5 MB, alt 100 car. |
| Upload de usuario | 5 MB (ampliable a 25 MB) |
| Chunking de KB | 100–700 tokens |
| Tags de KB | solo letras y números |
| `$conversation` | 100 turnos |
| Sesión | timeout 10 min por defecto |
| Simulaciones | 200 por run · 500 por workspace / 5 h |
| Analytics API | 100 req / 60 s por PAT; rango 1–365 días |
| Retención conversacional | 30 días por defecto |
| Widget | nombre 35 car. · home título 35 / subtítulo 85 · 5 preguntas de 25 car. · avatar 5 MB |

---

## 17. Implicaciones para nuestro diseño

1. **La BD vive fuera.** indigo.ai no es una base de datos: guarda conversaciones (30 días), variables de sesión y documentos de KB. Cualquier estado de negocio, memoria entre sesiones o histórico de cliente hay que modelarlo en una BD propia y exponerlo por API. La clave de unión natural es `$user_ref`.
2. **Dos caminos de acceso a datos y hay que elegir bien**: KB documental (semántico, tolerante, lento de actualizar) vs API (exacto, en vivo, requiere parámetros bien extraídos). El patrón robusto es Prompt Block → API Block → sección custom del agente.
3. **El presupuesto de tokens por agente (~5k) fuerza la granularidad.** Un agente que necesite mucho contexto es señal de que hay que partirlo en varios especialistas con tags de KB distintos.
4. **Los triggers son el contrato de routing.** Merece la pena diseñarlos como un espacio de intenciones mutuamente excluyente y documentarlo, antes de escribir prompts.
5. **Determinismo donde importa.** Todo lo que tenga consecuencias (crear ticket, modificar pedido, cobrar) debería ir por workflow con Collect + Condition + API, no delegado al criterio del LLM vía Tools.
6. **Instrumentación desde el día uno**: catálogo de Events con esquema de metadatos + evaluadores custom + suite "No Regression Test". Es barato al diseñar y carísimo de retrofitear.
7. **Secrets y variables por entorno** son la pieza que Sync no propaga: hay que planificar el modelo de configuración por entorno desde el principio.

---

## 18. Zonas grises (a confirmar con indigo.ai)

- **RAG**: vector store, modelo de embeddings, top-k, reranking y umbral de similitud no están documentados. Afecta a cómo dimensionamos y troceamos la KB.
- **Retención**: los 30 días de conversaciones ¿son configurables? Retención de audit logs y conversation logs no publicada.
- **Rate limits** de Chat API, triggers no conversacionales, Tools y MCP (solo está publicado el de Analytics API).
- **Timeouts** del API Block y política de reintentos.
- **Límites de la KB**: número máximo de documentos, tamaño máximo por archivo y de secciones por tag.
- **Handover**: qué datos exactos se transfieren al operador y qué proveedores externos de contact center están soportados.
- **Coste/licencia**: acceso a API, canales no-web, multilingüe, Enterprise Architecture y algunas funciones de seguridad requieren Elite o add-on.
- Dos páginas de la guía (*Practical Use Cases & Templates* y *Troubleshooting Common Issues*) están publicadas pero **vacías de contenido**.
