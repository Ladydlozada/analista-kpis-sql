"""Sugerencias de KPIs por reglas, a partir del perfil de las tablas.

Cada regla mira los ROLES de las columnas (ver perfil.py), nunca nombres
fijos. Por ejemplo, "si hay una fecha, proponé volumen por mes". Por eso
funciona con cualquier archivo. Cada sugerencia trae:

    pregunta  la pregunta de negocio que responde
    por_que   qué regla la disparó (transparencia: nada de "magia")
    sql       listo para ejecutar en DuckDB
    pasos     cómo se lee, para estudiar
    ojo       el detalle o la trampa para recordar

Límite consciente: las reglas no entienden el negocio. Ven "dos fechas", no
"puntualidad". Algo se infiere por los nombres (una fecha "prometida" o
"límite" es un plazo), pero el criterio de negocio lo ponés vos (o, más
adelante, Claude).
"""

import re
from dataclasses import dataclass

from .nombres import normalizar
from .perfil import PerfilTabla, Relacion, detectar_relaciones, es_plazo

# Valores que casi siempre indican un problema, en cualquier columna.
NEGATIVOS_FUERTES = r"cancel|anul|rechaz|devol|devuel|perdid|fallid"
# Valores que solo son "malos" si la columna es un estado: "Baja" en una columna
# estado es un cliente perdido, pero en una columna prioridad es... prioridad baja.
NEGATIVOS_DEBILES = r"baja|inactiv|vencid|error|reclam|suspend"
COLUMNA_ESTADO = r"estado|status|situaci|resultado|condici"
MIN_FILAS_VOLUMEN = 30
PALABRAS_MONTO = r"monto|importe|precio|total|venta|costo|valor|ingreso|gasto"
MAX_DESGLOSES = 2          # por cuántas dimensiones abrir cada KPI
MAX_CARDINALIDAD = 50      # una dimensión con más valores no sirve para comparar a simple vista


@dataclass
class Sugerencia:
    pregunta: str
    categoria: str      # se usa también como categoría de KPI al guardarla en el cuaderno
    por_que: str
    sql: str
    pasos: list[str]
    ojo: str = ""
    tabla: str = ""


@dataclass
class Dimension:
    """Algo por lo que se puede 'abrir' un KPI: por depósito, por segmento..."""
    expr: str           # transportistas.nombre
    alias: str          # transportista
    join: str           # LEFT JOIN transportistas ON ... (vacío si es de la misma tabla)
    cardinalidad: int   # cuántos valores distintos tiene
    descripcion: str    # para explicar de dónde sale
    de_relacion: bool   # True si viene de otra tabla vía JOIN


# ================================================================ entrada principal

def sugerir(perfiles: list[PerfilTabla]) -> list[Sugerencia]:
    relaciones = detectar_relaciones(perfiles)
    por_nombre = {p.nombre: p for p in perfiles}
    sugerencias = []
    for p in perfiles:
        dims = dimensiones(p, relaciones, por_nombre)
        desglose = [d for d in dims if d.cardinalidad <= MAX_CARDINALIDAD][:MAX_DESGLOSES]
        for regla in (_volumen, _distribuciones, _tasas, _numericos, _escalas, _tiempos, _cumplimiento, _rankings):
            sugerencias += regla(p, dims, desglose)
    sugerencias += _integridad(relaciones)
    return sugerencias


def dimensiones(p: PerfilTabla, relaciones: list[Relacion], por_nombre: dict) -> list[Dimension]:
    """Primero las que vienen de otras tablas (las más útiles: por depósito,
    por transportista), de menos a más valores; después las categorías propias."""
    de_otras, categorias_otras, propias = [], [], []
    for r in relaciones:
        if r.tabla != p.nombre:
            continue
        destino = por_nombre[r.destino]
        join = f"LEFT JOIN {destino.nombre} ON {destino.nombre}.{r.clave_destino} = {p.nombre}.{r.columna}"
        base = re.sub(r"^id_|_id$", "", r.columna) or destino.nombre
        etiqueta = destino.etiqueta()
        if etiqueta:
            de_otras.append(Dimension(
                f"{destino.nombre}.{etiqueta}", base, join, destino.filas,
                f"`{destino.nombre}.{etiqueta}`, unida por `{r.columna}`", True,
            ))
        for c in destino.de_rol("categoria"):
            if c.nombre != etiqueta:
                categorias_otras.append(Dimension(
                    f"{destino.nombre}.{c.nombre}", f"{base}_{c.nombre}", join, c.distintos,
                    f"`{destino.nombre}.{c.nombre}`, unida por `{r.columna}`", True,
                ))
    for c in p.de_rol("categoria"):
        propias.append(Dimension(f"{p.nombre}.{c.nombre}", c.nombre, "", c.distintos,
                                 f"`{p.nombre}.{c.nombre}`", False))
    orden = lambda d: d.cardinalidad  # noqa: E731
    return sorted(de_otras, key=orden) + sorted(categorias_otras, key=orden) + sorted(propias, key=orden)


# ================================================================ reglas

def _volumen(p, dims, desglose):
    fechas = p.de_rol("fecha")
    if not fechas or p.filas < MIN_FILAS_VOLUMEN:
        return []  # en una tabla de 8 agentes, "ingresos por mes" es ruido
    # Preferimos una fecha "de alta" (creación, registro, emisión) si existe.
    f = next((c for c in fechas if re.search(r"crea|alta|registr|emisi|ingres", c.nombre)), fechas[0])
    e = f.fecha_sql(f.nombre)
    sql = f"""SELECT DATE_TRUNC('month', {e}) AS mes,
       COUNT(*) AS cantidad
FROM {p.nombre}
WHERE {e} IS NOT NULL
GROUP BY mes
ORDER BY mes;"""
    return [Sugerencia(
        pregunta=f"¿Cuántos registros de {p.nombre} hay por mes?",
        categoria="Volumen",
        por_que=f"`{f.nombre}` es una fecha: toda tabla con fechas tiene una evolución en el tiempo.",
        sql=sql,
        pasos=[
            f"Lleva cada `{f.nombre}` al primer día de su mes (DATE_TRUNC).",
            f"Cuenta cuántas filas de `{p.nombre}` caen en cada mes.",
            "Ordena cronológicamente para ver la tendencia.",
        ],
        ojo=_ojo_fecha(f),
        tabla=p.nombre,
    )]


def _distribuciones(p, dims, desglose):
    salida = []
    for c in p.de_rol("categoria")[:3]:
        sql = f"""SELECT {c.nombre},
       COUNT(*) AS cantidad,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM {p.nombre}
GROUP BY {c.nombre}
ORDER BY cantidad DESC;"""
        salida.append(Sugerencia(
            pregunta=f"¿Cómo se reparten los registros de {p.nombre} según {c.nombre}?",
            categoria="Distribución",
            por_que=f"`{c.nombre}` es texto con solo {c.distintos} valores distintos: es una categoría.",
            sql=sql,
            pasos=[
                f"Agrupa las filas por cada valor de `{c.nombre}` y las cuenta.",
                "Calcula qué porcentaje del total representa cada grupo.",
                "Ordena de más a menos frecuente.",
            ],
            ojo="SUM(COUNT(*)) OVER () es una función de ventana sobre el resultado ya agrupado: "
                "suma los conteos de todos los grupos para obtener el total.",
            tabla=p.nombre,
        ))
    return salida


def _tasas(p, dims, desglose):
    """Si una categoría tiene un valor 'negativo' (cancelado, rechazado...),
    su tasa es casi siempre un KPI."""
    salida = []
    for c in p.de_rol("categoria"):
        es_estado = bool(re.search(COLUMNA_ESTADO, c.nombre))
        patron = f"{NEGATIVOS_FUERTES}|{NEGATIVOS_DEBILES}" if es_estado else NEGATIVOS_FUERTES
        negativo = next((v for v in c.valores if re.search(patron, v.lower())), None)
        if not negativo:
            continue
        v = negativo.replace("'", "''")
        col_pct = f"pct_{normalizar(negativo)}"
        caso = f"CASE WHEN {p.nombre}.{c.nombre} = '{v}' THEN 1.0 ELSE 0 END"
        categoria = "Cancelaciones" if "cancel" in negativo.lower() else "Tasas"
        ojo = ("AVG de una columna 0/1 da directamente la proporción: es el truco más usado "
               "para calcular tasas en SQL.")
        pasos_base = [
            f"Marca cada fila con 1 si `{c.nombre}` es '{negativo}' y con 0 si no (CASE WHEN).",
            "El promedio de esos 1 y 0 es la proporción; × 100 da el porcentaje.",
        ]
        salida.append(Sugerencia(
            pregunta=f"¿Qué porcentaje de {p.nombre} termina como '{negativo}'?",
            categoria=categoria,
            por_que=(f"`{c.nombre}` " + ("parece una columna de estado" if es_estado else "es una categoría")
                     + f" y tiene el valor '{negativo}', que suele indicar un problema."),
            sql=f"""SELECT COUNT(*) AS total,
       SUM({caso}) AS {normalizar(negativo)},
       ROUND(100 * AVG({caso}), 1) AS {col_pct}
FROM {p.nombre};""",
            pasos=pasos_base,
            ojo=ojo,
            tabla=p.nombre,
        ))
        for d in [d for d in desglose if not d.expr.endswith(f".{c.nombre}")]:
            salida.append(Sugerencia(
                pregunta=f"¿Qué porcentaje de {p.nombre} termina como '{negativo}', por {d.alias}?",
                categoria=categoria,
                por_que=f"Misma tasa, abierta por {d.descripcion}: para ver dónde se concentra.",
                sql=f"""SELECT {d.expr} AS {d.alias},
       COUNT(*) AS total,
       ROUND(100 * AVG({caso}), 1) AS {col_pct}
FROM {p.nombre}
{d.join}
GROUP BY {d.expr}
ORDER BY {col_pct} DESC;""".replace("\n\n", "\n"),
                pasos=[*_paso_join(d), *pasos_base, f"Lo calcula para cada {d.alias} y ordena de mayor a menor tasa."],
                ojo=ojo if not d.de_relacion else
                    "LEFT JOIN conserva las filas aunque no encuentren su pareja: así no se pierden "
                    "registros en el cálculo (aparecerían con la dimensión vacía).",
                tabla=p.nombre,
            ))
    return salida


def _numericos(p, dims, desglose):
    numeros = p.de_rol("numero")
    if not numeros:
        return []
    salida = []
    for n in numeros[:2]:
        categoria = "Montos" if re.search(PALABRAS_MONTO, n.nombre) else "Métricas numéricas"
        salida.append(Sugerencia(
            pregunta=f"¿Cuánto suma y cómo se distribuye {n.nombre}?",
            categoria=categoria,
            por_que=f"`{n.nombre}` es numérica: lo primero es conocer su total, centro y extremos.",
            sql=f"""SELECT COUNT({n.nombre}) AS filas_con_valor,
       ROUND(SUM({n.nombre}), 2) AS total,
       ROUND(AVG({n.nombre}), 2) AS promedio,
       ROUND(MEDIAN({n.nombre}), 2) AS mediana,
       MIN({n.nombre}) AS minimo,
       MAX({n.nombre}) AS maximo
FROM {p.nombre};""",
            pasos=[
                f"Cuenta las filas con `{n.nombre}` informado (COUNT ignora los NULL).",
                "Calcula total, promedio y mediana.",
                "Muestra mínimo y máximo para detectar valores raros.",
            ],
            ojo="Si el promedio está muy lejos de la mediana, hay valores extremos que tiran del promedio.",
            tabla=p.nombre,
        ))

    n = numeros[0]
    categoria = "Montos" if re.search(PALABRAS_MONTO, n.nombre) else "Métricas numéricas"
    for d in desglose:
        salida.append(Sugerencia(
            pregunta=f"¿Cuánto suma {n.nombre} por {d.alias}?",
            categoria=categoria,
            por_que=f"`{n.nombre}` es numérica y se puede abrir por {d.descripcion}.",
            sql=f"""SELECT {d.expr} AS {d.alias},
       COUNT(*) AS filas,
       ROUND(SUM({p.nombre}.{n.nombre}), 2) AS total_{n.nombre},
       ROUND(AVG({p.nombre}.{n.nombre}), 2) AS promedio_{n.nombre}
FROM {p.nombre}
{d.join}
GROUP BY {d.expr}
ORDER BY total_{n.nombre} DESC;""".replace("\n\n", "\n"),
            pasos=[*_paso_join(d), f"Agrupa por {d.alias}.",
                   f"Suma y promedia `{n.nombre}` en cada grupo, y ordena por el total."],
            ojo="Mirá total y promedio juntos: un grupo puede sumar mucho solo por tener muchas filas.",
            tabla=p.nombre,
        ))

    fechas = p.de_rol("fecha")
    if fechas:
        f = next((c for c in fechas if re.search(r"crea|alta|registr|emisi|ingres", c.nombre)), fechas[0])
        e = f.fecha_sql(f.nombre)
        salida.append(Sugerencia(
            pregunta=f"¿Cómo evoluciona {n.nombre} mes a mes?",
            categoria=categoria,
            por_que=f"Hay una métrica (`{n.nombre}`) y una fecha (`{f.nombre}`).",
            sql=f"""SELECT DATE_TRUNC('month', {e}) AS mes,
       ROUND(SUM({n.nombre}), 2) AS total_{n.nombre},
       ROUND(AVG({n.nombre}), 2) AS promedio_{n.nombre}
FROM {p.nombre}
WHERE {e} IS NOT NULL
GROUP BY mes
ORDER BY mes;""",
            pasos=[f"Lleva `{f.nombre}` al primer día del mes.",
                   f"Suma y promedia `{n.nombre}` por mes.", "Ordena cronológicamente."],
            ojo=_ojo_fecha(f),
            tabla=p.nombre,
        ))
    return salida


def _escalas(p, dims, desglose):
    """Una calificación (1-5, 0-10) no se suma: se promedia y se mira qué
    porcentaje está en lo más alto de la escala ("top-2-box", la forma
    habitual de reportar satisfacción o CSAT)."""
    salida = []
    for e in p.de_rol("escala")[:2]:
        maximo = int(e.valores[-1])
        corte = maximo - 1
        categoria = "Satisfacción" if re.search(r"satisf|nps|csat", e.nombre) else "Escalas"
        top = f"CASE WHEN {p.nombre}.{e.nombre} >= {corte} THEN 1.0 ELSE 0 END"
        ojo = (f"{e.nombre} es una escala ({e.valores[0]} a {maximo}): sumarla no significa nada. "
               f"El % de respuestas en {corte} o {maximo} ('top-2-box') es más fácil de comunicar "
               "que un promedio de 3,8.")
        salida.append(Sugerencia(
            pregunta=f"¿Cómo se distribuye {e.nombre}?",
            categoria=categoria,
            por_que=f"`{e.nombre}` tiene enteros de {e.valores[0]} a {maximo}: parece una calificación.",
            sql=f"""SELECT {e.nombre},
       COUNT(*) AS respuestas,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM {p.nombre}
WHERE {e.nombre} IS NOT NULL
GROUP BY {e.nombre}
ORDER BY {e.nombre};""",
            pasos=["Descarta las filas sin calificación.", "Cuenta cuántas respuestas tiene cada valor.",
                   "Calcula el porcentaje de cada valor sobre el total de respuestas."],
            ojo=ojo,
            tabla=p.nombre,
        ))
        promedio = f"""COUNT({p.nombre}.{e.nombre}) AS respuestas,
       ROUND(AVG({p.nombre}.{e.nombre}), 2) AS promedio,
       ROUND(100 * AVG({top}), 1) AS pct_{corte}_o_{maximo}"""
        salida.append(Sugerencia(
            pregunta=f"¿Qué porcentaje califica {corte} o {maximo} en {e.nombre}?",
            categoria=categoria,
            por_que="En una escala, el resumen más útil es el promedio junto al % de calificaciones altas.",
            sql=f"""SELECT {promedio}
FROM {p.nombre}
WHERE {p.nombre}.{e.nombre} IS NOT NULL;""",
            pasos=["Se queda con las filas que tienen calificación.",
                   f"Promedia `{e.nombre}`.",
                   f"Marca con 1 las calificaciones de {corte} o más y promedia: eso es el % de satisfechos."],
            ojo=ojo,
            tabla=p.nombre,
        ))
        for d in desglose:
            salida.append(Sugerencia(
                pregunta=f"¿Qué porcentaje califica {corte} o {maximo} en {e.nombre}, por {d.alias}?",
                categoria=categoria,
                por_que=f"La misma calificación, abierta por {d.descripcion}.",
                sql=f"""SELECT {d.expr} AS {d.alias},
       {promedio}
FROM {p.nombre}
{d.join}
WHERE {p.nombre}.{e.nombre} IS NOT NULL
GROUP BY {d.expr}
ORDER BY pct_{corte}_o_{maximo};""".replace("\n\n", "\n"),
                pasos=[*_paso_join(d), f"Para cada {d.alias}: cantidad de respuestas, promedio y % de satisfechos.",
                       "Ordena de peor a mejor, para ver primero dónde hay problemas."],
                ojo="Mirá la columna respuestas: un 100 % con 2 respuestas no vale lo mismo que un 80 % con 200.",
                tabla=p.nombre,
            ))
    return salida


def _pares_de_fechas(p):
    """Para cada par de fechas, mira en la muestra qué tan seguido una es
    posterior a la otra. Devuelve (a, b, % b>=a, % b>a)."""
    fechas = p.de_rol("fecha")
    pares = []
    for i, a in enumerate(fechas):
        for b in fechas[i + 1:]:
            sa, sb = p.fechas.get(a.nombre), p.fechas.get(b.nombre)
            if sa is None or sb is None:
                continue
            ambas = sa.notna() & sb.notna()
            if ambas.sum() < 5:
                continue
            pares.append((a, b, (sb[ambas] >= sa[ambas]).mean(), (sb[ambas] > sa[ambas]).mean()))
    return pares


def _con_hora(a, b) -> bool:
    """Si las dos columnas tienen hora, medimos en horas y comparamos momentos
    exactos. Si no, en días. (Un SLA de 4 horas comparado por día da cualquier cosa.)"""
    return a.con_hora and b.con_hora


def _base_fechas(p, a, b, d=None):
    """CTE que convierte las dos fechas (y trae la dimensión si hace falta)."""
    ref = (lambda c: f"{p.nombre}.{c}") if d and d.join else (lambda c: c)
    convertir = (lambda col, r: col.momento_sql(r)) if _con_hora(a, b) else (lambda col, r: col.fecha_sql(r))
    lineas = []
    if d:
        lineas.append(f"{d.expr} AS {d.alias}")
    lineas += [f"{convertir(a, ref(a.nombre))} AS {a.nombre}", f"{convertir(b, ref(b.nombre))} AS {b.nombre}"]
    seleccion = ",\n           ".join(lineas)
    join = f"\n    {d.join}" if d and d.join else ""
    return f"WITH base AS (\n    SELECT {seleccion}\n    FROM {p.nombre}{join}\n)"


def _tiempos(p, dims, desglose):
    """Si una fecha casi siempre es posterior a otra, son etapas de un proceso:
    la diferencia es un tiempo de ciclo."""
    salida = []
    for a, b, pct_ge, _ in _pares_de_fechas(p):
        if pct_ge < 0.95 or es_plazo(a.nombre) or es_plazo(b.nombre) or len(salida) >= 3:
            continue
        if _con_hora(a, b):
            unidad, dif = "horas", f"DATE_DIFF('minute', {a.nombre}, {b.nombre}) / 60.0"
            paso = f"DATE_DIFF('minute', ...) / 60.0 calcula las horas (con decimales) de `{a.nombre}` a `{b.nombre}`."
            ojo = ("Las dos columnas tienen hora, por eso se mide en horas. El .0 de 60.0 asegura una "
                   "división con decimales: en motores como SQL Server o PostgreSQL, entero / entero trunca.")
        else:
            unidad, dif = "dias", f"DATE_DIFF('day', {a.nombre}, {b.nombre})"
            paso = f"DATE_DIFF cuenta los días de `{a.nombre}` a `{b.nombre}` en cada fila."
            ojo = "La mediana es más robusta que el promedio: un solo caso de 90 días no la mueve."
        salida.append(Sugerencia(
            pregunta=f"¿Cuántas {'horas' if unidad == 'horas' else 'días'} pasan entre {a.nombre} y {b.nombre}?"
                     .replace("Cuántas días", "Cuántos días"),
            categoria="Tiempos de ciclo",
            por_que=f"En el {pct_ge:.0%} de la muestra `{b.nombre}` es igual o posterior a `{a.nombre}`: "
                    "parecen etapas de un mismo proceso.",
            sql=f"""{_base_fechas(p, a, b)}
SELECT COUNT(*) AS filas_con_ambas,
       ROUND(AVG({dif}), 1) AS {unidad}_promedio,
       ROUND(MEDIAN({dif}), 1) AS {unidad}_mediana,
       ROUND(MAX({dif}), 1) AS {unidad}_maximo
FROM base
WHERE {a.nombre} IS NOT NULL AND {b.nombre} IS NOT NULL;""",
            pasos=[
                "La CTE `base` convierte las dos columnas " + ("conservando la hora." if unidad == "horas" else "a fecha una sola vez."),
                paso,
                "Resume con promedio, mediana y máximo, solo donde ambas fechas existen.",
            ],
            ojo=ojo,
            tabla=p.nombre,
        ))
    return salida


def _cumplimiento(p, dims, desglose):
    """Si una de las fechas parece un plazo (prometida, límite...), el KPI es
    cuántas veces la fecha real lo supera."""
    salida = []
    for a, b, _, _ in _pares_de_fechas(p):
        if es_plazo(a.nombre) == es_plazo(b.nombre):
            continue  # o ninguna es plazo, o las dos
        plazo, real = (a, b) if es_plazo(a.nombre) else (b, a)
        # Solo tiene sentido si la fecha real A VECES supera el plazo. La fecha
        # de creación nunca es posterior a la prometida: comparar eso daría
        # siempre 0 % y no es un KPI.
        sp, sr = p.fechas[plazo.nombre], p.fechas[real.nombre]
        if not _con_hora(plazo, real):  # el SQL comparará días: la muestra también
            sp, sr = sp.dt.normalize(), sr.dt.normalize()
        ambas = sp.notna() & sr.notna()
        pct_tarde = (sr[ambas] > sp[ambas]).mean()
        if not 0.01 <= pct_tarde <= 0.99:
            continue
        tarde = f"{real.nombre} > {plazo.nombre}"
        por_que = (f"`{plazo.nombre}` parece un plazo (por su nombre) y `{real.nombre}` lo supera en "
                   f"el {pct_tarde:.0%} de la muestra: comparar las dos mide cumplimiento.")
        pasos = [
            "La CTE `base` convierte las dos columnas a fecha.",
            f"Cuenta las filas donde `{real.nombre}` es posterior a `{plazo.nombre}` (fuera de plazo).",
            "Lo divide por el total de filas con ambas fechas para obtener el porcentaje.",
        ]
        ojo = (f"Solo cuentan las filas con las dos fechas: si `{real.nombre}` está vacía (todavía no "
               "ocurrió), esa fila no entra ni como a tiempo ni como tarde.")
        if _con_hora(plazo, real):
            ojo += (" Se comparan fecha Y hora: si se comparara solo el día, cerrar a las 18:00 un "
                    "ticket que vencía a las 10:00 contaría como a tiempo.")
        salida.append(Sugerencia(
            pregunta=f"¿Qué porcentaje supera el plazo ({real.nombre} posterior a {plazo.nombre})?",
            categoria="Puntualidad",
            por_que=por_que,
            sql=f"""{_base_fechas(p, plazo, real)}
SELECT COUNT(*) AS filas_con_ambas,
       COUNT(*) FILTER (WHERE {tarde}) AS fuera_de_plazo,
       ROUND(100.0 * COUNT(*) FILTER (WHERE {tarde}) / COUNT(*), 1) AS pct_fuera_de_plazo
FROM base
WHERE {plazo.nombre} IS NOT NULL AND {real.nombre} IS NOT NULL;""",
            pasos=pasos,
            ojo=ojo,
            tabla=p.nombre,
        ))
        for d in desglose:
            salida.append(Sugerencia(
                pregunta=f"¿Qué porcentaje supera el plazo, por {d.alias}?",
                categoria="Puntualidad",
                por_que=f"El mismo cumplimiento, abierto por {d.descripcion}.",
                sql=f"""{_base_fechas(p, plazo, real, d)}
SELECT {d.alias},
       COUNT(*) AS filas_con_ambas,
       COUNT(*) FILTER (WHERE {tarde}) AS fuera_de_plazo,
       ROUND(100.0 * COUNT(*) FILTER (WHERE {tarde}) / COUNT(*), 1) AS pct_fuera_de_plazo
FROM base
WHERE {plazo.nombre} IS NOT NULL AND {real.nombre} IS NOT NULL
GROUP BY {d.alias}
ORDER BY pct_fuera_de_plazo DESC;""",
                pasos=[*_paso_join(d), *pasos[1:], f"Lo calcula para cada {d.alias} y ordena de peor a mejor."],
                ojo="Compará el porcentaje, no la cantidad: quien tiene más filas casi siempre tiene "
                    "más casos tarde, aunque cumpla mejor.",
                tabla=p.nombre,
            ))
    return salida


def _rankings(p, dims, desglose):
    """Ranking por las dimensiones 'grandes' (muchos clientes, productos...)."""
    salida = []
    for d in [d for d in dims if d.de_relacion and d.cardinalidad > 10][:1]:
        salida.append(Sugerencia(
            pregunta=f"Top 10 por {d.alias}: ¿quién concentra más {p.nombre}?",
            categoria="Rankings",
            por_que=f"{d.descripcion} tiene muchos valores ({d.cardinalidad}): sirve un top 10.",
            sql=f"""SELECT {d.expr} AS {d.alias},
       COUNT(*) AS cantidad,
       RANK() OVER (ORDER BY COUNT(*) DESC) AS puesto
FROM {p.nombre}
{d.join}
GROUP BY {d.expr}
ORDER BY puesto
LIMIT 10;""",
            pasos=[*_paso_join(d), f"Cuenta filas por {d.alias}.",
                   "RANK() asigna el puesto según esa cantidad; LIMIT deja los 10 primeros."],
            ojo="RANK repite puesto en los empates y salta el siguiente (1, 2, 2, 4). "
                "ROW_NUMBER no repite nunca; DENSE_RANK repite pero no salta.",
            tabla=p.nombre,
        ))
    return salida


def _integridad(relaciones):
    salida = []
    for r in relaciones:
        salida.append(Sugerencia(
            pregunta=f"¿Hay valores de {r.tabla}.{r.columna} que no existen en {r.destino}?",
            categoria="Calidad de datos",
            por_que=f"`{r.columna}` aparece en `{r.tabla}` y es la clave de `{r.destino}`: "
                    "debería apuntar siempre a una fila existente.",
            sql=f"""SELECT COUNT(*) AS filas_huerfanas,
       COUNT(DISTINCT {r.tabla}.{r.columna}) AS valores_distintos
FROM {r.tabla}
LEFT JOIN {r.destino} ON {r.destino}.{r.clave_destino} = {r.tabla}.{r.columna}
WHERE {r.tabla}.{r.columna} IS NOT NULL
  AND {r.destino}.{r.clave_destino} IS NULL;""",
            pasos=[
                f"LEFT JOIN trae cada fila de `{r.tabla}` con su pareja en `{r.destino}`, si la tiene.",
                f"Si no la tiene, las columnas de `{r.destino}` quedan en NULL.",
                "El WHERE se queda solo con esas filas 'huérfanas' y las cuenta.",
            ],
            ojo="Este patrón (LEFT JOIN + WHERE ... IS NULL) se llama anti-join: "
                "sirve para encontrar lo que está en una tabla y falta en la otra.",
            tabla=r.tabla,
        ))
    return salida


# ================================================================ ayudantes

def _paso_join(d: Dimension) -> list[str]:
    if not d.join:
        return []
    return [f"Une con la tabla de {d.alias} (LEFT JOIN) para mostrar {d.descripcion.split(',')[0]} en vez del id."]


def _ojo_fecha(f) -> str:
    if f.expr_fecha and f.expr_fecha.startswith("TRY_CAST"):
        return (f"`{f.nombre}` está guardada como texto: TRY_CAST la convierte a fecha y deja NULL "
                "las que no puede leer (el WHERE las excluye).")
    if f.expr_fecha and "STRPTIME" in f.expr_fecha:
        return (f"`{f.nombre}` es texto con formato día/mes/año. Se asumió dd/mm/aaaa: si fuera "
                "mm/dd/aaaa, cambiá el formato en TRY_STRPTIME.")
    return ""
