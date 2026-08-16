# Plan de construcción · Catalog Service + agente en indigo.ai

Documento de trabajo. **No forma parte del documento técnico** (`bloque-A-datos.md`), que recoge decisiones de diseño. Aquí está el orden de ejecución.

**Este plan no decide nada.** Cuando una instrucción y el documento técnico digan cosas distintas, manda el documento técnico y este plan está mal.

---

## Reparto

| Quién | Qué | Fases |
|---|---|---|
| **Claude** | El Catalog Service completo: repositorio, código, tests, Docker, despliegue y pipeline | **1 a 6** |
| **Laura** | La solución en indigo.ai: workspace, workflows, agentes, Tools y Cards | **7** |
| **Las dos** | Pruebas de aceptación, README y vídeo | **8 a 10** |

## El camino crítico

```
Fase 0   cuentas, claves y nombres
   ↓
Fases 1-5   el servicio, hasta desplegarlo en Fly
   ↓
/openapi.json publicado y autenticado
   ↓
Fase 7   indigo.ai puede importar la integración
```

**Nada que toque el catálogo se puede configurar en indigo.ai hasta que la fase 5 esté cerrada.** Lo que sí se puede adelantar en paralelo está marcado dentro de la fase 7.

**Cómo se lee este plan.** Las fases van en orden estricto: no se empieza una sin cerrar la anterior. Cada fase tiene **qué se hace**, **dónde va** y **cómo se sabe que está terminada** — y esa última es la condición para pasar a la siguiente.

---

## Fase 0 · Cuentas, claves y nombres

Nada de lo demás existe sin esto. Es enteramente manual.

### 0.1 · GitHub

Crear el repositorio **`catalog-service`**. Es la raíz de todo: código, datos y pipeline.

### 0.2 · Anthropic

Obtener una clave de API en la consola de Anthropic.

**Dónde va:** en los **secretos del repositorio** de GitHub, como `ANTHROPIC_API_KEY`.

> Se inyecta solo durante el workflow, se censura en los registros y **no entra nunca en el contenedor** (A3.7). El contenedor no puede llamar a un modelo aunque quisiera.

### 0.3 · Fly.io

Crear la cuenta, instalar `flyctl`, autenticarse, y **reservar el nombre de la aplicación**, porque de él sale el host:

```
https://indigo-catalog-service.fly.dev
```

Restricciones de Fly: **minúsculas, sin acentos, solo letras, números y guiones, y único en toda la plataforma.**

Ese host se escribe después en indigo.ai. Fijarlo ahora evita rehacer la integración.

### 0.4 · Las dos credenciales del servicio

Generar dos cadenas aleatorias largas, una por capacidad (B6.3):

| Credencial | Para qué |
|---|---|
| **Catalog key** | Las cinco operaciones. Es la que usa indigo.ai |
| **Diagnostics key** | `/_diagnostics/load-report`. Solo la operadora del servicio |

Todavía no van a ningún sitio: la de catálogo irá a Fly Secrets en la fase 5, y a los Secrets de indigo.ai en la fase 7.

### 0.5 · indigo.ai

Crear el workspace. Todavía no se configura nada dentro.

### 0.6 · Fijar los nombres, de una vez

Se escriben aquí y **se usan literales en todas partes**: el código, la especificación, indigo.ai y el README.

```
App en Fly              indigo-catalog-service
Secrets en Fly          CATALOG_API_KEY  ·  DIAGNOSTICS_API_KEY
Cabecera                X-Api-Key
Esquema en OpenAPI      CatalogApiKey
Secret en indigo.ai     CATALOG_API_KEY
Variables de sesión     criteria_map · search_count · catalog_response · technical_error
                        limit
Workflows               Welcome Workflow · Product Discovery Workflow
                        Find Products by Criteria Workflow
Agentes                 Product Discovery Agent · General Agent
```

**Las cinco variables de sesión son las cinco de C2, y no hay una sexta.** Las inicializa el Welcome Workflow y ninguna otra pieza crea variables por su cuenta.

**Cerrada cuando:** existe el repo, la clave de Anthropic está en los secretos de GitHub, `flyctl` responde autenticado, el nombre de la app está reservado, las dos credenciales están generadas y el workspace de indigo.ai existe.

---

## Fase 1 · El repositorio y los datos

**Dónde:** `catalog-service/`

### 1.1 · Crear el árbol completo de A3.9

```
catalog-service/
├── data/
│   ├── catalog.csv              el fichero de la tienda, INTACTO
│   ├── vocabularies.yaml        el diccionario del dominio
│   └── semantic_layer.json      la clasificación de los productos
├── prompts/
│   ├── enrich.md                criterio de los campos propios
│   └── relate.md                criterio de las relaciones
├── scripts/
│   ├── enrich.py                campos propios de los productos nuevos, o de los
│   │                            150 si el commit cambia vocabularies.yaml o enrich.md
│   ├── relate.py                relaciones, recálculo completo
│   └── validate_semantic.py     la puerta de cobertura
├── src/
│   ├── normalization.py         canonicalización determinista · CI y runtime
│   ├── models.py                la clase Product
│   ├── loader.py                normalization + semantic_layer → memoria
│   ├── repository.py            CatalogRepository
│   ├── selection.py             selección por cortes + orden por precedencia
│   └── api.py                   FastAPI · endpoints · OpenAPI
├── tests/
├── .github/workflows/deploy.yml el pipeline
├── requirements.txt             dependencias del servicio y de las pruebas
├── .gitignore                   lo que no sube al repositorio
├── Dockerfile
└── README.md
```

**`src/normalization.py` es una sola implementación con cuatro consumidores:** `loader.py` en tiempo de ejecución, y `enrich.py`, `relate.py` y `validate_semantic.py` en CI. Nadie vuelve a escribir la canonicalización en su propio módulo: dos implementaciones que se separen producen dos conjuntos de productos distintos y la puerta de cobertura dejaría de significar nada.

### 1.2 · Colocar los tres ficheros de datos

| Fichero | Estado |
|---|---|
| `catalog.csv` | El original de la tienda. **No se toca ni una celda** |
| `vocabularies.yaml` | **Ya está hecho.** Versión 4, 214 definiciones, 263 alias |
| `semantic_layer.json` | **Ya está hecho.** 150 productos, 9 campos, `vocabulary_version: 4` |

> Los dos últimos son el arranque de A3.8: se generaron en sesión de trabajo con el catálogo en contexto. **Eso es el arranque, no el diseño** — si se borraran, el pipeline de la fase 6 los reconstruye enteros.

**Cerrada cuando:** el árbol existe, los tres ficheros están en `data/`, y `semantic_layer.json` declara `vocabulary_version: 4` con 150 entradas.

---

## Fase 2 · El servicio

**Dónde:** `src/`. En este orden, porque cada módulo depende del anterior.

### 2.1 · `models.py`

| Forma | Qué es |
|---|---|
| **`Product`** | **26 campos.** La forma única de las cuatro operaciones que devuelven mercancía |
| **Cómo se escriben** | **Modelos de pydantic, no `dataclass`.** Es la única manera de que la descripción de un campo llegue al JSON Schema, y B7.8 exige quince de ellas |
| **Los vocabularios cerrados** | `use_case`, `functional_family`, `gift_risk` y `suitable_relationships` se construyen **como `enum` desde `vocabularies.yaml`** y se publican como esquemas propios, con su `definicion`. `product_type` es la excepción: texto libre (B7.10) |
| **`ExcludedProduct`** | Forma reducida: `product_id` · `name` · `price` · `exclusion_reason` · `actual` · `required` |
| **`CategorySummary`** | Lo que devuelve `get_categories`: estado de la categoría, no solo el nombre |

**Los envelopes no son una forma común, y no se construye un esquema universal.** Cada operación lleva **sus** metadatos y ningún otro. Esta es la tabla normativa de B4.8, copiada aquí para que no haya que ir a buscarla:

| Operación | Contenido | Cantidad | Metadatos propios |
|---|---|---|---|
| `get_categories` | CategorySummary | Una por categoría; 11 hoy | — |
| `get_products_by_category` | Product | `limit` **1 a 8**, por defecto 8 | `total` · `offset` |
| `find_products_by_criteria` | Product | `limit` **1 a 8**, por defecto 8 · **5** cuando la conversación está acotada | `query_understood` · `excluded` · `not_applied` |
| `get_related_products` | Product | `limit` **1 a 5**, por defecto 3 | `relation_type` · `query_understood` · `excluded` |
| `get_product_details` | Product | 1 | — |

**Y quince campos llevan descripción, las de B7.8 literales**, porque su mala lectura cambia lo que el agente afirma: `results`, `excluded`, `exclusion_reason`, `actual`, `required`, `not_applied`, `query_understood`, `total`, `offset`, `relation_type`, `gift_risk`, `is_standalone_gift`, `in_stock`, `stocking_filler`, `pairs_with` y `alternative_to`. Los evidentes —`name`, `price`, `brand`, `color`, `material`— no la llevan.

Lo que se deduce de esa tabla, y es lo que evita el megaesquema:

- **`currency`** es lo único que llevan todas: se declara una vez por respuesta y no se repite en cada producto.
- **`not_applied`** existe **solo** en `find_products_by_criteria`.
- **`excluded`** existe **solo** en `find_products_by_criteria` y `get_related_products`.
- **`total` y `offset`** existen **solo** en `get_products_by_category`, que es la única operación paginada.
- **`relation_type`** existe **solo** en los elementos de `get_related_products` con `relation=alternative_to`, y describe la relación con el punto de partida, no el producto: por eso no forma parte de `Product`.
- **`get_product_details`** devuelve `result`, un único `Product`, no una lista.
- **`excluded` y `not_applied` se omiten cuando están vacíos.**

> **Ocho es el máximo absoluto del servicio.** Ninguna operación devuelve nunca más de ocho productos, en ninguna circunstancia.

### 2.2 · `normalization.py`

**Toda la transformación determinista del catálogo vive aquí, y solo aquí.** Es la lista de A2.2, entera:

- **normalizar el precio** —divisa y separador decimal—; si está ausente queda `None`, y ese producto queda fuera de las búsquedas con filtro de precio;
- **fusionar los duplicados** y **elegir el `product_id` canónico**: **152 filas → 150 productos**;
- **resolver un `alt_product_id`** al producto canónico;
- **abrir `recipient` añadiendo `anyone`**;
- **marcar `description_quality: "poor"`** donde la descripción no permite construir una razón;
- **emitir los avisos de calidad** documentados.

**Y no inventa valores ausentes.** `rating` y `reviews_count` sin dato se quedan sin dato: **`null` no es cero.**

Se escribe antes que `loader.py` porque lo consume, y antes que los scripts de la fase 6 porque también lo consumen.

### 2.3 · `loader.py`

Hace tres cosas, y ninguna más:

1. **Llama a `normalization.py`** sobre `catalog.csv` y recibe los 150 productos canónicos. **No transforma nada por su cuenta**: ni precios, ni duplicados, ni `recipient`, ni `description_quality`.
2. **Lee `semantic_layer.json`** y une cada entrada con su producto canónico.
3. **Construye el modelo en memoria** que consume el resto del servicio.

> Si en `loader.py` aparece una regla de transformación del CSV, está duplicada: su sitio es `normalization.py`. Dos implementaciones que se separen producen dos catálogos distintos, y la puerta de cobertura de la fase 6 dejaría de significar nada.

### 2.4 · `repository.py`

El protocolo `CatalogRepository`. Es lo que permite que el resto del código no sepa que detrás hay un CSV.

### 2.5 · `selection.py`

**El módulo que más importa acertar.** Dos pasos, y solo dos:

```
1 · SELECCIÓN
    Se cogen los productos que cumplen las doce fronteras de B2.7.
    Si el cliente ha pedido un objeto concreto y se ha resuelto,
    product_type restringe ANTES por coincidencia exacta: solo esos
    productos entran, y las fronteras se aplican sobre ese conjunto.

2 · ORDEN POR PRECEDENCIA
    Comparación lexicográfica sobre ocho niveles, en este orden fijo.
    Se compara el nivel 1; si separa, ya está. Si no, se baja al 2.
    Y así hasta que un nivel separe o se acaben los niveles.

    1 · functional_family + use_case
    2 · occasion
    3 · category + subcategory
    4 · recipient
    5 · suitable_relationships
    6 · rating + reviews_count
    7 · gift_risk
    8 · description_quality
```

**Dentro de un nivel.** Los niveles 1 y 3 llevan dos dimensiones: el nivel se resuelve por **cuántas de sus dimensiones satisface el producto** —las dos por delante de una, y una por delante de ninguna—, y no se suman coincidencias de niveles distintos. Dentro de una misma dimensión, la dimensión está satisfecha **cuando hay intersección** entre lo pedido y lo que el producto lleva: coincidir con dos valores en vez de con uno no adelanta a nadie.

**Los tres casos que se implementan mal si no se dicen:**

| Caso | Qué hace el módulo |
|---|---|
| `use_case: universal` | No cuenta como coincidencia con ningún valor concreto. Con `use_case` en la consulta: **coincidencia exacta, luego `universal`, luego sin coincidencia**. Sin `use_case` en la consulta: **`universal` delante**. Desempata **dentro de un mismo recuento de dimensiones**, nunca por encima de él |
| `rating` + `reviews_count` | **Valor conocido delante de valor desconocido**, y entre conocidos el descendente ya definido. Los dos campos van juntos, no por separado. **`null` nunca se compara como cero** |
| `gift_risk` | **Es el único nivel que puede no participar**: lo decide `buyer_knows_recipient` |

**Y el empate final.** Si al terminar los ocho niveles varios productos siguen empatados, el empate es irrelevante para la recomendación. La salida se estabiliza con `product_id` para que sea reproducible, y eso es **todo** lo que significa. Nunca con el precio.

> **No existe `product_score`, ni `weighted_sum`, ni `similarity`, ni suma de porcentajes, ni pesos, ni categorías ponderadas.** La precedencia es un orden lexicográfico: un nivel separa o no separa, y si no separa se baja al siguiente. **Un producto no acumula nada.**

**La prohibición, dicha con precisión:** no se calcula **ningún valor numérico derivado** para ordenar o comparar productos. Los números que existen —`price`, `rating`, `reviews_count`, `shipping_days`, `stock`— son datos del catálogo, no resultados de un cálculo de orden.

**El vocabulario del código sigue la misma regla que el documento:** la selección se describe por lo que los productos cumplen, y no se nombra ninguna puntuación, peso ni ranking. **Este plan no bautiza funciones**: los nombres concretos los elige quien programa, dentro de esa regla.

### 2.6 · La lógica de relacionados, dentro de `selection.py`

`get_related_products` **no tiene módulo propio ni lógica de orden propia**: reutiliza las dos piezas que ya existen, las fronteras y la precedencia. Lo que sí tiene es una cascada de niveles, y sin ella no se puede programar.

**Con `relation=alternative_to`, en este orden:**

```
1 · Se aplican las fronteras activas de la llamada
    (precio, plazo, envoltorio, marca, color, material, stock…)

2 · Nivel 1 · relación explícita, la persistida en alternative_to
      se ordena con la precedencia de B2.8 → se toma hasta agotar el limit

3 · Si quedan plazas → Nivel 2 · mismo product_type
      se ordena con la precedencia de B2.8 → se toma hasta agotar el limit

4 · Si quedan plazas → Nivel 3 · misma functional_family
      se ordena con la precedencia de B2.8 → se toma hasta agotar el limit

5 · Empate que persiste → product_id
```

**Un candidato de un nivel inferior nunca adelanta a uno de un nivel superior**, por bien que encaje en cualquier criterio: primero se agota el nivel de arriba y solo después se completa el `limit` con el siguiente.

**Dentro de cada nivel se aplica la misma cadena de B2.8**, usando **únicamente los criterios presentes en la llamada**. No se crea un criterio de proximidad, ni una puntuación, ni un orden propio para los relacionados.

**La restricción exacta de `product_type` NO se propaga aquí** (v34 → v35). En la búsqueda identifica el objeto pedido y restringe; en relacionados **describe el objeto que se sustituye**, así que define el nivel de partida y **no limita la respuesta**: un sustituto es muy a menudo otro tipo de objeto. Ante *"algo como un cuchillo de chef"* sin producto de origen, primero salen los cuchillos de chef, después lo de su misma familia funcional, y después el resto.

**Con `relation=pairs_with` no hay tres niveles**: se parte de los `pairs_with` explícitos del producto ancla, se aplican las fronteras activas y se ordena a los supervivientes con esa misma precedencia.

**`is_standalone_gift` lo exigen la búsqueda y `alternative_to`; no lo exigen `pairs_with`, `get_products_by_category` y `get_product_details`** (B2.7, registros B2ag y B2ah). Y en `pairs_with` no se exige (B2.7, registro B2ag): el complemento no tiene que sostenerse solo como regalo — la piedra de afilar, el muestrario de tintas o la funda son justo lo que se busca. `in_stock` sí corta también aquí, sin excepción.

**La etiqueta la decide el vínculo, no el nivel.** `equivalent` solo con evidencia suficiente del catálogo; **todo lo demás es `same_function`**, incluida una relación explícita entre dos objetos distintos. Estar en el nivel 1 **no** implica `equivalent`.

**Los parámetros son 20, y no son los de la búsqueda.** Comparte dieciocho con `find_products_by_criteria` —diecisiete criterios de negocio y `limit`—, y además:

| | |
|---|---|
| Solo aquí | `relation` · `product_id` |
| Solo en `find_products_by_criteria` | `stocking_filler` |
| Dentro, y conviene no perderlos | `gift_wrap_required` · `buyer_knows_recipient` |
| `limit` | **1 a 5, por defecto 3** |

**Y de qué se puede partir:**

| Situación | Qué devuelve |
|---|---|
| Falta `relation` | `invalid_parameter` — es el único obligatorio |
| `pairs_with` sin `product_id` | `missing_anchor` |
| `alternative_to` sin `product_id` y sin ningún criterio semántico | `missing_anchor` |
| `alternative_to` con `product_id` inexistente | `product_not_found` |

`product_id` **no es una entrada privilegiada**: es un criterio más. Con `alternative_to` basta con que llegue un producto **o** intención semántica suficiente.

### 2.7 · `api.py`

FastAPI con las cinco operaciones. **Las descripciones de B7 se escriben literales en el código**, porque son exactamente lo que el modelo lee al decidir qué capacidad usar.

| | |
|---|---|
| Operaciones | `get_categories` · `get_products_by_category` · `find_products_by_criteria` · `get_related_products` · `get_product_details` |
| Seguridad | `CatalogApiKey` · `apiKey` · `in: header` · `X-Api-Key` |
| Sin credencial | **401**. Credencial de catálogo contra diagnóstico: **403** |
| Límite de tasa | **60 por minuto** con la Catalog key, **10 por minuto** con la Diagnostics key. Superarlo: **429** con `error_code: "rate_limited"` (B6.8) |
| Cómo se cuenta | **Ventana deslizante de 60 segundos en memoria del proceso**, por credencial. Sin almacén externo. Vuelve a cero en cada despliegue (B6.8, registro B6q) |
| Recuperables | **200** con `error_type`: `invalid_parameter` · `conflicting_parameters` · `missing_anchor` · `product_not_found` |
| Fallo técnico | **5xx**, con `error_code`, que es un vocabulario **separado** del de `error_type` |
| Abierto sin credencial | `/openapi.json` y `/docs` |
| Fuera del contrato | `/_diagnostics/load-report`, con `include_in_schema=False` |
| Rutas | La ruta **es** el `operation_id`: `/get_categories`, `/get_products_by_category`, `/find_products_by_criteria`, `/get_related_products`, `/get_product_details` |
| Criterios compartidos | Se declaran **una sola vez** y las dos operaciones los reutilizan, con el mismo esquema y la misma descripción (B7.10) |
| Validación | Los errores previsibles de FastAPI y pydantic **se interceptan** y salen como **200 con `error_type`** (B5.3). **El 422 no existe en este contrato** |
| Declarado en la spec | **200, 401, 403, 429 y 5xx**, cada una con su forma |

**Los dos booleanos sin valor por defecto.** `gift_wrap_required` y `stocking_filler` **no se rellenan con `false`** cuando el cliente no los ha dicho: ausente, `false` y `true` son tres estados distintos, y la ausencia **no viaja en `query_understood`**. Es una regla de la petición, no de la carga del catálogo.

**Cerrada cuando:** el servicio arranca en local, responde a las cinco operaciones, y `GET /openapi.json` devuelve la especificación con los `enum` llevando las `definicion` de `vocabularies.yaml`.

---

## Fase 3 · Tests

**Dónde:** `tests/`. **Es una puerta: sin verde aquí no se despliega.**

| # | Test | Valor esperado |
|---|---|---|
| 1 | Carga | 150 productos · 139 disponibles · 145 `product_type` · 30 `use_case` · 31 `functional_family` |
| 2 | Canonicalización | Las **152 filas** dan **150 productos**, y un `alt_product_id` resuelve al canónico **sin** ser `product_not_found` |
| 3 | Una sola canonicalización | `loader.py` y `validate_semantic.py` producen **el mismo conjunto de identificadores**, porque llaman al mismo `normalization.py` |
| 4 | Las doce fronteras, una a una | `gift_wrap` → 137, y 127 contando solo disponibles |
| 5 | Precedencia | El orden cambia al activar niveles, y **ningún campo numérico de puntuación viaja en la respuesta** |
| 6 | Ausentes | Un producto sin `rating` queda **detrás** de uno con `rating`, y **nunca** se compara como si tuviera 0 |
| 7 | `universal` | Sin `use_case` en la consulta, un producto `universal` va delante; con `use_case: cooking`, va **detrás** de lo que cocina y **delante** de lo que no |
| 8 | Booleanos sin defecto | `gift_wrap_required` y `stocking_filler` ausentes **no** viajan como `false` en `query_understood` |
| 9 | **Los seis escenarios de A8.7** | **34 · 0 · 97 · 132** candidatos |
| 10 | Escenario 1 · la hermana | Encabeza la **tarjeta regalo de 50 €**, porque la consulta no lleva `use_case` |
| 11 | Escenario 2 · el cuchillo | `results: []`, y el gyuto de 149 € en `excluded` con `over_budget`, `actual: 149`, `required: 100` |
| 12 | Escenario 4 · la botella de vino | `not_applied` con `unresolved`. El conservador **no** se presenta como vino |
| 13 | Escenario 5 · lo retro | La consola agotada **no aparece** |
| 14 | Alias | `gyuto` → `chef_knife`. `santoku` no resuelve, y va a `not_applied` |
| 15 | `anyone` | Con `recipient=her`, los 6 de `kids` no entran en los ocho |
| 16 | Envelopes por operación | `not_applied` solo en `find_products_by_criteria`; `excluded` solo en esa y en `get_related_products`; `total`/`offset` solo en `get_products_by_category`; `relation_type` solo en relacionados |
| 17 | Tamaño | Ninguna operación devuelve más de 8 productos; `get_related_products` por defecto 3 |
| 18 | Relacionados · los tres niveles | Con `alternative_to`, un candidato de misma `functional_family` **nunca** adelanta a uno de mismo `product_type`, ni este a una relación explícita |
| 19 | Relacionados · dentro del nivel | Con más candidatos que plazas, dentro de un nivel ordena B2.8 con **solo** los criterios de la llamada, y el empate final es `product_id` |
| 20 | Relacionados · `pairs_with` | Parte de los `pairs_with` explícitos del ancla, aplica las fronteras y ordena con esa precedencia. **Sin los tres niveles** |
| 21 | Relacionados · anclaje | Sin `relation` → `invalid_parameter`. `pairs_with` sin `product_id` → `missing_anchor`. `alternative_to` solo con precio → `missing_anchor`. Ancla inexistente → `product_not_found` |
| 22 | Relacionados · los 20 parámetros | `gift_wrap_required` y `buyer_knows_recipient` **dentro**; `stocking_filler` **fuera**; `limit` 1 a 5, por defecto 3 |
| 23 | Relacionados · la etiqueta | Una relación explícita entre dos objetos distintos sale como **`same_function`**, no como `equivalent`. El nivel 1 no implica `equivalent` |
| 24 | Nadie amplía lo que ya llegó completo | `get_product_details` **no** se usa para enriquecer un producto que ya vino en una lista: las tres operaciones devuelven `Product` entero |
| 25 | Vocabularios como `enum` | `UseCase` 30, `FunctionalFamily` 31, `GiftRisk` 3, `SuitableRelationship` 5, publicados como esquemas y referenciados por sus parámetros. `product_type` **sin `enum`** |
| 26 | Criterios compartidos | La descripción de `product_type`, `use_case`, `functional_family`, `category`, `subcategory` y los tres de precio es **idéntica** en la búsqueda y en relacionados |
| 27 | Validación recuperable | Un valor fuera de un `enum` o un número mal formado → **200** con `invalid_parameter`. **Ninguna operación declara 422** |
| 28 | Códigos declarados | Las cinco declaran **401, 403, 429 y 5xx** |
| 29 | Descripciones de B7.8 | Los quince campos las llevan **en el JSON Schema**, no solo en el código |
| 30 | Relacionados y el tipo | Con `alternative_to` y `product_type`, la respuesta **incluye otros tipos**; la búsqueda con el mismo `product_type` **no** |
| 31 | Autenticación (B6.12) | Sin `X-Api-Key` → **401**. Clave desconocida → **401**. Catalog key contra diagnóstico → **403**. `/openapi.json` y `/docs` sin credencial → **200** |
| 32 | Límite de tasa | Con la Catalog key, **60 por minuto**; la 61 en el mismo minuto → **429** con `error_code: "rate_limited"` |

**Cerrada cuando:** los treinta y dos pasan, y el 9 da exactamente 34 / 0 / 97 / 132.

> Hoy son **159 pruebas** en el conjunto, porque varias de esas treinta y dos se comprueban con más de un caso.

---

## Fase 4 · Docker

**Dónde:** `Dockerfile`.

**Imagen base: Python 3.12.** Es la versión con la que FastAPI y pydantic traen todas sus ruedas compiladas; ir por delante obliga a compilar dependencias dentro de la imagen, y eso falla el día del despliegue y no antes.

**Qué entra en la imagen y qué no. Es una decisión de seguridad, no de tamaño:**

| Carpeta | ¿Entra? |
|---|---|
| `data/` · `src/` · `requirements.txt` | **Sí** |
| `prompts/` · `scripts/` · `tests/` | **No** |

> El contenedor no lleva la clave, no lleva el prompt y no lleva el script que haría la llamada.

`normalization.py` vive en `src/`, así que **entra**: lo necesita `loader.py` en cada arranque.

**Cerrada cuando:** la imagen se construye, arranca en local, responde a las cinco operaciones con la credencial, y **no contiene ninguna credencial**.

---

## Fase 5 · Fly · el hito que desbloquea la fase 7

1. **`fly.toml` se escribe a mano, como parte del proyecto.** **Nunca con `fly launch`**, ni siquiera con opciones que apunten a una app existente: `fly launch` está pensado para crear una aplicación nueva, y aquí la aplicación **ya existe y ya está reservada**. Lo que haría es intentar crear una segunda y reescribir lo que se haya decidido en el fichero.
2. **Las dos credenciales a Fly Secrets** — cifradas, inyectadas como variables de entorno en ejecución, **fuera de la imagen y fuera de `fly.toml`**. Mientras no exista ninguna Machine quedan en estado `Staged`: es lo normal, y se aplican solas en el primer despliegue.
3. **Desplegar solo por el pipeline.** No hay `fly deploy` a mano, y no es una preferencia de estilo: un despliegue manual **se salta la puerta de cobertura y las pruebas**, que son lo único que impide publicar un artefacto que clasifica mal. El primer despliegue tiene que ser el que sale de un `Action` en verde.
4. Comprobar que Fly Proxy termina TLS antes de la aplicación.

**Las tres decisiones de disponibilidad, y por qué.**

| Ajuste | Valor | Por qué |
|---|---|---|
| `primary_region` | `fra` | Quien llama no es un navegador, es indigo.ai, cuya arquitectura está alojada en Frankfurt. La región se elige por quien llama |
| `min_machines_running` · `auto_stop_machines` | `1` · `"stop"` | Una máquina siempre despierta. **El mínimo solo tiene efecto con `stop` o `suspend`**, así que no se pone `auto_stop_machines = "off"`: sería desactivar el mecanismo del que depende el mínimo |
| Health check HTTP | `GET /openapi.json` | Un puerto abierto solo prueba que algo escucha. Esta ruta prueba que **FastAPI responde**, y es la única que contesta 200 sin credencial. Cualquier otra devolvería 401 al sondeo y Fly daría por muerta una máquina viva |

**Cerrada cuando, contra el host real:**

```
GET /openapi.json                          → 200, la especificación completa
GET /docs                                  → 200
GET /... sin X-Api-Key                     → 401
GET /... con X-Api-Key desconocida         → 401
GET /... con Catalog key                   → 200
GET /_diagnostics/... con Catalog key      → 403
GET /_diagnostics/... con Diagnostics key  → 200
Ráfaga por encima del límite               → 429, error_code rate_limited
GET /_diagnostics/load-report              → no figura en la especificación
fly.toml y logs                            → no contienen ninguna credencial
```

---

## Fase 6 · El pipeline en CI

**Dónde:** `prompts/`, `scripts/`, `.github/workflows/deploy.yml`.

| Pieza | Qué hace |
|---|---|
| `prompts/enrich.md` | El criterio de los campos propios |
| `prompts/relate.md` | El criterio de las relaciones |
| `scripts/enrich.py` | Campos propios de **los productos nuevos**, o de **los 150** si el commit ha **cambiado el criterio**. También **da de alta los `product_type` nuevos** en el vocabulario, sin subir `vocabulary_version` |
| `scripts/relate.py` | Relaciones, **recálculo completo**, siempre |
| `scripts/validate_semantic.py` | **La puerta de cobertura** |
| `deploy.yml` | Encadena: enrich → relate → puerta → tests → **commit de vuelta** → deploy |

**Cuatro cosas del workflow que no son decorativas:**

| | |
|---|---|
| `permissions: contents: write` | El pipeline **escribe en el repositorio** —la capa semántica y, cuando hay un tipo nuevo, el vocabulario—. El token por defecto es de solo lectura en un repositorio restringido, y el YAML lo eleva **para ese job y para nada más** |
| `fetch-depth: 2` | `enrich.py` compara el vocabulario con el de la revisión anterior para distinguir un alta de un cambio de criterio, y la puerta lo necesita para el invariante de crecimiento. Sin historia, los dos caen del lado seguro |
| `concurrency` sin cancelar | Dos ejecuciones sobre la misma rama se pisarían al empujar. Se encolan en vez de cancelarse: una ejecución a medias no puede dejar el artefacto escrito y el vocabulario sin escribir |
| `file_pattern` con **los dos ficheros** | `data/semantic_layer.json` **y** `data/vocabularies.yaml`. Un artefacto que referencia un tipo que el vocabulario no lleva cierra la puerta en la ejecución siguiente |

> Un push hecho con `GITHUB_TOKEN` **no vuelve a disparar el workflow**, así que el commit de vuelta no puede entrar en bucle.

**Los tres scripts importan `src/normalization.py`.** Ninguno canonicaliza por su cuenta: la puerta de cobertura compara conjuntos de identificadores, y solo significa algo si los dos lados se han construido con la misma implementación.

**Qué recalcula cada commit:**

| Qué ha cambiado en el commit | `enrich.py` | `relate.py` |
|---|---|---|
| Solo `data/catalog.csv` | Incremental: los productos sin entrada | Completo |
| `data/vocabularies.yaml` · **alta de un `product_type` nuevo** | **Incremental.** Crece el inventario, no el criterio | Completo |
| `data/vocabularies.yaml` · **cambio de criterio** | **Completo: los 150** | Completo |
| `prompts/enrich.md` | **Completo: los 150** | Completo |
| `prompts/relate.md` | Incremental | Completo |

> Un vocabulario nuevo deja productos clasificados con el significado antiguo dentro del mismo fichero. **El artefacto sigue siendo válido y pasa la puerta**: por eso la reclasificación no puede depender de que alguien se acuerde.

> **El invariante de A3.4:** si la cobertura no está completa, **el despliegue no sale**. Es una puerta, no un respaldo.

**Cerrada cuando las dos ramas se han visto funcionar:**

1. Un commit que **añade una fila al CSV** dispara el pipeline, clasifica solo esa, pasa la puerta y despliega solo, sin intervención.
2. Un commit que **cambia `vocabularies.yaml` sin tocar el CSV** dispara el pipeline y reclasifica **los 150**.

---

## Fase 7 · indigo.ai · Laura

### Vía paralela · se puede hacer desde la fase 0

**7.1 · Variables.** En el **Welcome Workflow**, un `Set Values`:

```
criteria_map      = {}      Map
search_count      = 0       número
catalog_response  = null    Map
technical_error   = null    texto
limit             = 8       número
```

**`limit` es una variable, no un campo fijo del API Block.** La escribe la `Condition` de tamaño de 7.7 antes de cada llamada. Su inicial es `8` porque es lo que vale una primera búsqueda: si una llamada se saltara esa `Condition`, sale con el tamaño correcto y no vacía.

**No hay variable de paginación**: `get_products_by_category` pagina con el `offset` y el `total` de su propia respuesta.

**7.2 · General Agent.** Sin Catalog Tools. Saludos, agradecimientos, small talk mínimo, reconocer lo que está fuera de alcance, **no inventar información de la tienda**, reconducir.

**7.3 · Triggers del Mother.** El del **Product Discovery Workflow** tiene que cubrir todo el dominio del regalo, **incluidos los turnos breves de continuación** — *"cincuenta euros"*, *"el viernes"*, *"no sé"*, *"la segunda"*. Es donde vive la calidad del enrutado.

### Vía dependiente · necesita la fase 5 cerrada

**7.4 · La integración.** Importar `https://indigo-catalog-service.fly.dev/openapi.json` **una sola vez**. Salen las cinco acciones. La Catalog key se guarda como Secret y viaja en `X-Api-Key: {{secrets.CATALOG_API_KEY}}`.

**7.5 · Product Discovery Workflow.** Un `Prompt Block` "Update State", en **JSON Output Mode** con **Variable Assignment**: recibe el `criteria_map` anterior más el último mensaje, devuelve el `criteria_map` **entero reescrito**. Después, `Reroute` al agente.

**7.6 · Product Discovery Agent.** **Cuatro** de las cinco acciones se asignan como Tools directas: `get_categories`, `get_products_by_category`, `get_related_products` y `get_product_details`. **`find_products_by_criteria` no se asigna como Tool**: se alcanza solo por el workflow de 7.7. El prompt lleva el comportamiento de D, y **aquí se mide el tamaño** contra los 4.000–5.000 tokens del prompt configurado.

**7.7 · Find Products by Criteria Workflow.** En este orden exacto:

```
1 · CONDITION   red de seguridad
                ¿existe criterio de precio Y max_shipping_days?

                NO ──► SET VALUES  catalog_response = null
                                   technical_error  = null
                       search_count NO se toca
                       REROUTE → Product Discovery Agent
                       find_products_by_criteria NO se ejecuta

                SÍ ──► sigue

2 · CONDITION   tamaño de la entrega
                search_count == 0  ──► SET VALUES  limit = 8
                $true              ──► SET VALUES  limit = 5

3 · API BLOCK   acción find_products_by_criteria
                criteria_map.<campo> → <parámetro>  ·  limit
                X-Api-Key: {{secrets.CATALOG_API_KEY}}

4 · SUCCESS ──► SET VALUES  catalog_response = envelope de ESTA llamada
                            technical_error  = null

                CONDITION  ¿catalog_response.error_type?

                SÍ    ──► petición recuperable, no ejecutada
                          search_count NO se toca

                $true ──► búsqueda ejecutada correctamente
                          SET VALUES  search_count = 1

   ERROR    ──► SET VALUES  technical_error   = …
                            catalog_response  = null
                            search_count NO se toca

5 · REROUTE → Product Discovery Agent
```

**Las tres reglas que este workflow existe para garantizar:**

- **No hay `status`.** No existe `missing_required` ni ninguna otra variable de estado: la rama bloqueante no marca nada, limpia las dos variables y devuelve el turno al agente.
- **`search_count` solo pasa de `0` a `1` en la rama Success cuyo envelope *no* lleva `error_type`.** Un recuperable con HTTP 200, un fallo técnico y una llamada que la `Condition` impidió **no consumen la primera búsqueda**. Ahí se queda: `1` para siempre.
- **`catalog_response` y `technical_error` nunca son válidos a la vez.** Cada rama pone a `null` la de la otra, y la rama bloqueante las limpia las dos. Sin eso, el agente puede leer un resultado viejo junto a un error nuevo.

**7.8 · Cards.** `Save Output` con **`Write Output in Chat = NO`** → Card Blocks → carrusel. Micro-razón **dentro de 85 caracteres**.

**Cerrada cuando:** una conversación completa funciona de punta a punta, con productos reales del catálogo.

---

## Fase 8 · Pruebas de aceptación

| Prueba | Qué se observa | Si falla |
|---|---|---|
| **Enrutado del Mother** | Que *"cincuenta euros"* vuelva a Product Discovery | Se reescribe el trigger |
| **Elección de capacidad** | Que el agente distinga las cinco capacidades de C3 — cuatro Tools y el workflow | Se corrige su prompt |
| **Tamaño del prompt** | Que quepa en 4.000–5.000 tokens | Se retira duplicación, no se crean agentes |
| **No-2xx de una Tool** | Cómo lo presenta indigo.ai, en las cuatro Tools | El diseño aguanta los dos resultados (B5.3) |
| **La primera búsqueda** | Que la primera entrega ocho y la segunda cinco, y que un recuperable **no** consuma la primera | Se revisa la `Condition` 4 de 7.7 |
| **Los seis escenarios del brief** | En conversación real, no en test | — |

---

## Fase 9 · README y vídeo

**README:** qué es · cómo se levanta en local · cómo se despliega · cómo funciona el pipeline · y **por qué existe la capa semántica**, que es lo que separa este trabajo de un buscador con chat alrededor.

**Vídeo, 5–10 minutos.** Guion propuesto: el problema del CSV → la capa semántica → los seis escenarios en vivo → la arquitectura → el pipeline.

## Fase 10 · Bonus, solo si sobra tiempo

Landing con el widget embebido y personalizado · servidor MCP con transporte streamable HTTP.

---

## Estado

| Fase | Estado |
|---|---|
| 0 · Cuentas, claves y nombres | ⬜ pendiente |
| 1 · Repositorio y datos | ⬜ pendiente |
| 2 · El servicio | ⬜ pendiente |
| 3 · Tests | ⬜ pendiente |
| 4 · Docker | ⬜ pendiente |
| 5 · Fly | ⬜ pendiente |
| 6 · Pipeline en CI | ⬜ pendiente |
| 7 · indigo.ai | ⬜ pendiente |
| 8 · Pruebas de aceptación | ⬜ pendiente |
| 9 · README y vídeo | ⬜ pendiente |
| 10 · Bonus | ⬜ opcional |

---

## Qué ha cambiado en esta redacción

| Dónde | Qué decía | Qué dice ahora |
|---|---|---|
| **1.1** | El árbol no tenía `src/normalization.py`, y `enrich.py` era *"incremental"* sin condición | El árbol es el de A3.9 entero, con `normalization.py` y con la condición de `enrich.py` |
| **2.2** | `loader.py` parecía canonicalizar por su cuenta | `normalization.py` es una sección propia y `loader.py` lo importa |
| **2.1** | Los envelopes se enumeraban como forma común | Se copia la tabla normativa **B4.8** y se dice qué metadato es de qué operación |
| **2.5** | *"Categoría de más peso"*, *"porcentajes de B2.8"*, categorías *"ponderadas"* | Orden lexicográfico sobre los **ocho niveles**, con los tres casos que se implementan mal: `universal`, ausentes en `rating`, y `gift_risk` |
| **2.6** | No mencionaba códigos de acceso ni límite de tasa | 401 · 403 · 429 con `error_code: "rate_limited"`, y `/openapi.json` y `/docs` abiertos |
| **3** | Diez tests | Diecinueve: se añaden canonicalización, implementación única, ausentes, `universal`, booleanos sin defecto, el encabezado del escenario 1, envelopes por operación, tamaño y límite de tasa; la autenticación pasa de *"rechazo"* a los códigos de B6.12 |
| **6** | `enrich.py` *"incremental"*, y solo se cerraba probando un CSV nuevo | Tabla de qué recalcula cada commit, y se cierra probando **las dos ramas** |
| **0.3 · 0.6 · 7.4** | El nombre de la app de Fly era un hueco | Se escribe literal: `indigo-catalog-service`. El repositorio sigue siendo `catalog-service`, que es como lo llama A3.9 |
| **0.6 · 7.1** | `limit` no estaba declarada, y el workflow escribía en ella | `limit` es la quinta variable de sesión, número, inicial `8` (C2 y registro C0p) |
| **2.2 · 2.3** | `loader.py` listaba transformaciones que son de `normalization.py` | 2.2 recoge la lista entera de A2.2; 2.3 llama, lee la capa semántica y construye el modelo, y nada más |
| **2.5** | Prohibía *"un número por producto"*, lo que dejaba fuera a `shipping_days`, y bautizaba dos funciones que la memoria no nombra | Se prohíbe el **valor numérico derivado para ordenar**, y se retiran los nombres de función |
| **2.6** | `get_related_products` no tenía lógica escrita | Sección nueva dentro de `selection.py`: los tres niveles, `pairs_with`, la etiqueta, los 20 parámetros y las condiciones de anclaje |
| **2.7 · 3 · 5** | El límite de tasa no tenía cifra | **60 por minuto** con la Catalog key, **10** con la Diagnostics key (B6.8, registro B6p) |
| **3** | Diecinueve tests | Veintiséis: siete nuevos sobre relacionados y sobre no ampliar lo que ya llegó completo |
| **2.7** | El límite de tasa no decía cómo se cuenta | Ventana deslizante de 60 segundos en memoria, por credencial (B6.8, registro B6q) |
| **4** | La imagen no tenía versión de Python | **Python 3.12** |
| **1.1** | No había `.gitignore` | Creado, y añadido a A3.9: entorno virtual, temporales de Python, `.env` y `*.key` |
| **1.1 · 4** | No había fichero de dependencias | `requirements.txt` en la raíz, y entra en la imagen. Añadido también a A3.9 |
| **2.1** | Las formas eran `dataclass` y sin descripciones de campo | Modelos de pydantic, con las quince descripciones de B7.8 en el JSON Schema, y los cuatro vocabularios cerrados como `enum` desde `vocabularies.yaml` |
| **2.6** | Los relacionados restringían por `product_type` | La restricción exacta **no se propaga** (v34 → v35): ahí `product_type` es el ancla, no el universo |
| **2.7** | Los criterios se declaraban dos veces y el 422 escapaba | Criterios compartidos definidos una vez; validación previsible transformada a **200 con `error_type`**; 401, 403, 429 y 5xx declarados; rutas iguales al `operation_id` |
| **6** | El alta de un `product_type` nuevo no estaba resuelta | La escribe `enrich.py` en la misma ejecución, sin subir la versión; la puerta valida definición, alias, tipos desaparecidos y que no haya más tipos nuevos que productos nuevos (A4.11) |
| **3** | Veintiséis pruebas | Treinta y dos, con las seis nuevas del contrato |
| **2.6 · 2.7** | No decía dónde corta `is_standalone_gift` | Tabla de las cinco operaciones: lo exigen la búsqueda y `alternative_to`; no lo exigen `pairs_with`, la navegación y el detalle (B2.7, registros B2ag y B2ah) |
| **7.6** | No decía cuántas acciones se asignan como Tools | Cuatro Tools; `find_products_by_criteria` **no** se asigna |
| **7.7** | Reaparecía `status = missing_required`, `search_count = 1` en cualquier Success, y las ramas no se limpiaban | La lógica vigente de C, con las tres reglas escritas debajo |
| **8** | — | Se añade la prueba de la primera búsqueda: ocho, luego cinco, y un recuperable no la consume |
