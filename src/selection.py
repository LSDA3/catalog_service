"""Qué productos entran y en qué orden salen.

Tres mecánicas separadas, y no se mezclan nunca:

1. **La restricción de coincidencia exacta** (B2.6). Cuando el cliente ha pedido
   un objeto concreto y se ha resuelto, `product_type` define qué productos
   satisfacen literalmente la petición. Actúa **antes** de las fronteras. No es
   un corte y no ordena: identifica.
2. **Las doce fronteras** (B2.7). Se cogen los productos que las cumplen.
3. **El orden por precedencia** (B2.8). Ocho niveles, comparados uno tras otro:
   si un nivel separa, ya está; si no, se baja al siguiente.

**Aquí no se calcula ningún valor numérico derivado.** La precedencia se asigna
al criterio, no al producto: ningún producto acumula nada, no hay puntuación, ni
pesos, ni porcentajes. La clave de orden que construye este módulo es una
comparación lexicográfica —se leen sus posiciones en secuencia, la primera que
difiere decide— y no una cantidad que se sume ni se compare como magnitud.
"""

from __future__ import annotations

from models import ExcludedProduct, Product

# --------------------------------------------------------------------------
# 1 · Restricción de coincidencia exacta (B2.6)
# --------------------------------------------------------------------------


def restringir_por_coincidencia_exacta(
    productos: list[Product], product_type: str | None
) -> list[Product]:
    """El conjunto de los productos que son el objeto pedido.

    Un `paring_knife` no es un cuchillo de chef peor: es otro objeto. Por eso no
    entra en el conjunto ni aparece después en `excluded`.
    """
    if not product_type:
        return list(productos)
    return [producto for producto in productos if producto.product_type == product_type]


# --------------------------------------------------------------------------
# 2 · Las doce fronteras (B2.7)
# --------------------------------------------------------------------------

BANDA_DE_TARGET_PRICE = 0.20


def _cumple_el_precio(producto: Product, criterios: dict) -> bool:
    if producto.price is None:
        return not any(
            clave in criterios for clave in ("max_price", "min_price", "target_price")
        )
    if "max_price" in criterios and producto.price > criterios["max_price"]:
        return False
    if "min_price" in criterios and producto.price < criterios["min_price"]:
        return False
    if "target_price" in criterios:
        centro = criterios["target_price"]
        if not (
            centro * (1 - BANDA_DE_TARGET_PRICE)
            <= producto.price
            <= centro * (1 + BANDA_DE_TARGET_PRICE)
        ):
            return False
    return True


def coger_lo_que_cumple(
    productos: list[Product],
    criterios: dict,
    exclusivos_de_genero: set[str] | None = None,
    exigir_regalo_autonomo: bool = True,
) -> list[Product]:
    """Los productos que cumplen las doce fronteras.

    Dos son invariantes del servicio y no dependen de lo que diga el cliente:
    `in_stock` y `is_standalone_gift`. Las otras diez solo actúan si el cliente
    las ha declarado.

    `is_standalone_gift` corta **al recomendar**, y por eso lo hace por defecto.
    **No corta con `relation=pairs_with`**, que es la vía por la que un accesorio
    o un recambio llegan legítimamente como complemento: ahí un producto que no
    se sostiene solo como regalo es exactamente lo que se está buscando.
    `in_stock` no tiene excepción y corta en todo el servicio.
    """
    exclusivos_de_genero = exclusivos_de_genero or set()
    dentro: list[Product] = []

    for producto in productos:
        if not producto.in_stock:
            continue
        if exigir_regalo_autonomo and not producto.is_standalone_gift:
            continue
        if not _cumple_el_precio(producto, criterios):
            continue
        if "max_shipping_days" in criterios:
            if producto.shipping_days is None:
                continue
            if producto.shipping_days > criterios["max_shipping_days"]:
                continue
        if criterios.get("gift_wrap_required") is True and producto.gift_wrap is not True:
            continue
        for campo in ("brand", "color", "material"):
            if campo in criterios and getattr(producto, campo) != criterios[campo]:
                break
        else:
            if criterios.get("recipient") == "kids":
                if "kids" not in producto.recipient:
                    continue
            if "gender_specific" in criterios:
                pedido = criterios["gender_specific"]
                if producto.product_type in exclusivos_de_genero:
                    if pedido not in producto.recipient:
                        continue
            dentro.append(producto)

    return dentro


# --------------------------------------------------------------------------
# 3 · El orden por precedencia (B2.8)
# --------------------------------------------------------------------------

ORDEN_DE_GIFT_RISK = {"low": 0, "taste_dependent": 1, "high_commitment": 2}
ORDEN_DE_DESCRIPTION_QUALITY = {"ok": 0, "poor": 1}


def _coincide(valores_del_producto: list[str], pedido) -> bool:
    """Hay coincidencia cuando hay intersección.

    Coincidir con dos valores en vez de con uno no adelanta a nadie: los valores
    de la consulta son alternativas pertinentes, no puntos acumulables.
    """
    if pedido is None:
        return False
    pedidos = pedido if isinstance(pedido, (list, tuple, set)) else [pedido]
    return bool(set(valores_del_producto) & set(pedidos))


def _nivel_uno(producto: Product, criterios: dict) -> tuple[int, int]:
    """`functional_family` + `use_case`, con la precedencia propia de `universal`.

    El nivel se resuelve primero por cuántas de sus dos dimensiones satisface el
    producto. `universal` desempata **dentro de un mismo recuento**, nunca por
    encima de él.
    """
    familia_pedida = criterios.get("functional_family")
    situacion_pedida = criterios.get("use_case")

    dimensiones_satisfechas = 0
    if familia_pedida and _coincide(producto.functional_family, familia_pedida):
        dimensiones_satisfechas += 1
    if situacion_pedida and _coincide(producto.use_case, situacion_pedida):
        dimensiones_satisfechas += 1

    if situacion_pedida:
        if _coincide(producto.use_case, situacion_pedida):
            lugar_de_universal = 0
        elif "universal" in producto.use_case:
            lugar_de_universal = 1
        else:
            lugar_de_universal = 2
    else:
        lugar_de_universal = 0 if "universal" in producto.use_case else 1

    return (-dimensiones_satisfechas, lugar_de_universal)


def _nivel_seis(producto: Product) -> tuple[int, float, int, int]:
    """`rating` + `reviews_count`, en cascada y nunca combinados en una fórmula.

    Conocido antes que desconocido; entre conocidos, descendente. `null` no se
    sustituye por cero, no se compara como cero y no se escribe como cero: lo que
    se compara es si el dato existe.
    """
    nota_conocida = 0 if producto.rating is not None else 1
    nota = -producto.rating if producto.rating is not None else 0.0
    resenas_conocidas = 0 if producto.reviews_count is not None else 1
    resenas = -producto.reviews_count if producto.reviews_count is not None else 0
    return (nota_conocida, nota, resenas_conocidas, resenas)


def clave_de_precedencia(
    producto: Product, criterios: dict, description_quality: str = "ok"
) -> tuple:
    """La posición del producto en la cadena, nivel a nivel.

    Es una clave de comparación, no una nota: cada posición corresponde a un
    nivel de B2.8 y se lee en secuencia. La primera que difiere decide, y las
    siguientes no la pueden compensar.
    """
    nivel_1 = _nivel_uno(producto, criterios)

    nivel_2 = 0 if _coincide(producto.occasion, criterios.get("occasion")) else 1

    coincidencias_de_estanteria = 0
    if criterios.get("category") and producto.category == criterios["category"]:
        coincidencias_de_estanteria += 1
    if criterios.get("subcategory") and producto.subcategory == criterios["subcategory"]:
        coincidencias_de_estanteria += 1
    nivel_3 = -coincidencias_de_estanteria

    pedido = criterios.get("recipient")
    nivel_4 = 0 if (pedido and pedido in producto.recipient) else 1

    relacion = criterios.get("relationship")
    nivel_5 = 0 if (relacion and relacion in producto.suitable_relationships) else 1

    nivel_6 = _nivel_seis(producto)

    if criterios.get("buyer_knows_recipient") is True:
        nivel_7 = 0  # el nivel se omite y la comparación continúa en el siguiente
    else:
        nivel_7 = ORDEN_DE_GIFT_RISK.get(producto.gift_risk, 0)

    nivel_8 = ORDEN_DE_DESCRIPTION_QUALITY.get(description_quality, 0)

    return (
        nivel_1,
        nivel_2,
        nivel_3,
        nivel_4,
        nivel_5,
        nivel_6,
        nivel_7,
        nivel_8,
        producto.product_id,
    )


def ordenar_por_precedencia(
    productos: list[Product],
    criterios: dict,
    calidad_por_producto: dict[str, str] | None = None,
) -> list[Product]:
    """Ordena el conjunto válido recorriendo la cadena de arriba abajo.

    Un empate que sobrevive a los ocho niveles es irrelevante para la
    recomendación: se estabiliza con `product_id` para que la salida sea
    reproducible, y eso es todo lo que significa. Nunca con el precio.
    """
    calidad_por_producto = calidad_por_producto or {}
    return sorted(
        productos,
        key=lambda producto: clave_de_precedencia(
            producto, criterios, calidad_por_producto.get(producto.product_id, "ok")
        ),
    )


# --------------------------------------------------------------------------
# El canal `excluded` (B1.6)
# --------------------------------------------------------------------------

TOPE_DE_EXCLUDED = 2


def por_encima_del_presupuesto(
    productos: list[Product],
    criterios: dict,
    exclusivos_de_genero: set[str] | None = None,
    calidad_por_producto: dict[str, str] | None = None,
) -> list[ExcludedProduct]:
    """Hasta dos candidatos relevantes que la frontera de precio dejó fuera.

    Se eligen **por el orden de precedencia, no por ser los más baratos**: elegir
    por precio produce respuestas absurdas. Y cumplen todo lo demás — solo el
    precio les impide entrar.
    """
    if "max_price" not in criterios:
        return []

    sin_precio = {clave: valor for clave, valor in criterios.items() if clave != "max_price"}
    candidatos = [
        producto
        for producto in coger_lo_que_cumple(productos, sin_precio, exclusivos_de_genero)
        if producto.price is not None and producto.price > criterios["max_price"]
    ]
    ordenados = ordenar_por_precedencia(candidatos, criterios, calidad_por_producto)

    return [
        ExcludedProduct(
            product_id=producto.product_id,
            name=producto.name,
            price=producto.price,
            exclusion_reason="over_budget",
            actual=producto.price,
            required=criterios["max_price"],
        )
        for producto in ordenados[:TOPE_DE_EXCLUDED]
    ]


# --------------------------------------------------------------------------
# La lógica de relacionados (B0)
# --------------------------------------------------------------------------

RELACIONES = ("alternative_to", "pairs_with")


def _niveles_de_alternativa(
    ancla: Product | None, productos: list[Product], criterios: dict
) -> list[list[Product]]:
    """Los tres niveles, de donde sale cada candidato y en qué orden.

    Un candidato de un nivel inferior nunca adelanta a uno de un nivel superior:
    primero se agota el de arriba y solo después se completa el `limit`.
    """
    if ancla is None:
        # Sin producto de origen, lo que define la alternativa es la intención
        # acumulada: un solo nivel, el de los criterios semánticos.
        return [[p for p in productos]]

    explicita = [p for p in productos if p.product_id in ancla.alternative_to]
    ya_vistos = {p.product_id for p in explicita} | {ancla.product_id}

    mismo_tipo = [
        p
        for p in productos
        if p.product_type == ancla.product_type and p.product_id not in ya_vistos
    ]
    ya_vistos |= {p.product_id for p in mismo_tipo}

    misma_familia = [
        p
        for p in productos
        if set(p.functional_family) & set(ancla.functional_family)
        and p.product_id not in ya_vistos
    ]

    return [explicita, mismo_tipo, misma_familia]


def relacionados(
    productos: list[Product],
    relacion: str,
    ancla: Product | None,
    criterios: dict,
    limite: int,
    exclusivos_de_genero: set[str] | None = None,
    calidad_por_producto: dict[str, str] | None = None,
) -> list[Product]:
    """Recorre los niveles de la relación aplicando fronteras y precedencia.

    La regla completa, en una línea: relación → fronteras → precedencia dentro
    del nivel → siguiente nivel → `product_id`. No se crea ninguna lógica de
    orden propia para los relacionados: se reutilizan las dos piezas que ya hay.
    """
    if relacion == "pairs_with":
        if ancla is None:
            return []
        niveles = [[p for p in productos if p.product_id in ancla.pairs_with]]
    else:
        niveles = _niveles_de_alternativa(ancla, productos, criterios)

    # El complemento no tiene que sostenerse solo como regalo: es la vía por la
    # que llegan el muestrario de tintas, la piedra de afilar o la funda.
    exigir_regalo_autonomo = relacion != "pairs_with"

    elegidos: list[Product] = []
    for nivel in niveles:
        if len(elegidos) >= limite:
            break
        candidatos = [p for p in nivel if ancla is None or p.product_id != ancla.product_id]
        dentro = coger_lo_que_cumple(
            candidatos, criterios, exclusivos_de_genero, exigir_regalo_autonomo
        )
        ordenados = ordenar_por_precedencia(dentro, criterios, calidad_por_producto)
        for producto in ordenados:
            if len(elegidos) >= limite:
                break
            elegidos.append(producto)

    return elegidos
