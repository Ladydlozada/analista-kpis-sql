"""Detecta qué conceptos de SQL usa una consulta y les asigna una familia con color.

Para qué: el cuaderno marca cada consulta con sus conceptos (JOIN, GROUP BY,
RANK...) para poder filtrar "todas mis consultas con funciones de ventana" y
repasar por tema.

Cómo: con expresiones regulares sobre el SQL, después de quitar comentarios y
textos entre comillas (si no, un WHERE estado = 'JOIN' contaría como JOIN).
No es un analizador perfecto de SQL, pero para estudiar alcanza y es fácil de
leer y de ampliar: agregar un concepto es agregar una línea a CONCEPTOS.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Familia:
    nombre: str
    fondo: str   # color de fondo de la etiqueta
    texto: str   # color del texto (el tono oscuro de la misma familia)


FAMILIAS = {
    "unir": Familia("Unir tablas", "#EEEDFE", "#3C3489"),
    "filtrar": Familia("Filtrar", "#FAECE7", "#712B13"),
    "agrupar": Familia("Agrupar y contar", "#E1F5EE", "#085041"),
    "funciones": Familia("Funciones y fechas", "#FAEEDA", "#633806"),
    "ventanas": Familia("Funciones de ventana", "#E6F1FB", "#0C447C"),
    "estructura": Familia("Estructura", "#FBEAF0", "#72243E"),
}

# (nombre visible, familia, patrón). Los patrones se aplican sobre el SQL en mayúsculas.
CONCEPTOS = [
    ("WHERE", "filtrar", r"\bWHERE\b"),
    ("HAVING", "filtrar", r"\bHAVING\b"),
    ("IN", "filtrar", r"\bIN\s*\("),
    ("BETWEEN", "filtrar", r"\bBETWEEN\b"),
    ("LIKE", "filtrar", r"\bI?LIKE\b"),
    ("IS NULL", "filtrar", r"\bIS\s+(?:NOT\s+)?NULL\b"),
    ("GROUP BY", "agrupar", r"\bGROUP\s+BY\b"),
    ("COUNT", "agrupar", r"\bCOUNT\s*\("),
    ("SUM", "agrupar", r"\bSUM\s*\("),
    ("AVG", "agrupar", r"\bAVG\s*\("),
    ("MIN / MAX", "agrupar", r"\b(?:MIN|MAX)\s*\("),
    ("DISTINCT", "agrupar", r"\bDISTINCT\b"),
    ("CASE WHEN", "funciones", r"\bCASE\b"),
    ("CAST", "funciones", r"\b(?:TRY_)?CAST\s*\(|::"),
    ("COALESCE", "funciones", r"\b(?:COALESCE|IFNULL|NULLIF)\s*\("),
    ("Fechas", "funciones",
     r"\b(?:DATE_TRUNC|DATE_DIFF|DATEDIFF|DATE_PART|DATE_ADD|EXTRACT|STRFTIME|"
     r"STRPTIME|CURRENT_DATE|NOW|INTERVAL)\b"),
    ("OVER", "ventanas", r"\bOVER\s*\("),
    ("PARTITION BY", "ventanas", r"\bPARTITION\s+BY\b"),
    ("RANK", "ventanas", r"\b(?:DENSE_)?RANK\s*\("),
    ("ROW_NUMBER", "ventanas", r"\bROW_NUMBER\s*\("),
    ("LAG / LEAD", "ventanas", r"\b(?:LAG|LEAD)\s*\("),
    ("QUALIFY", "ventanas", r"\bQUALIFY\b"),
    ("UNION", "estructura", r"\bUNION\b"),
]

# Los JOIN se tratan aparte para distinguir JOIN (inner) de LEFT JOIN, etc.
_JOIN = re.compile(r"\b(?:(LEFT|RIGHT|FULL|CROSS|INNER)\s+(?:OUTER\s+)?)?JOIN\b")
_CTE = re.compile(r"\bWITH\s+(?:RECURSIVE\s+)?\w+\s+AS\s*\(\s*SELECT\b")
_SUBCONSULTA = re.compile(r"\(\s*SELECT\b")
_COMENTARIOS_Y_TEXTOS = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'", re.S)

# Para mostrar los conceptos siempre en el mismo orden: por familia.
_ORDEN_FAMILIAS = list(FAMILIAS)


def familia_de(concepto: str) -> str:
    return _FAMILIA_POR_CONCEPTO.get(concepto, "estructura")


def detectar(sql: str) -> list[str]:
    """'SELECT ... FROM a LEFT JOIN b ... GROUP BY x' -> ['LEFT JOIN', 'GROUP BY', ...]"""
    limpio = _COMENTARIOS_Y_TEXTOS.sub(" ", sql).upper()
    encontrados = set()

    for calificador in _JOIN.findall(limpio):
        encontrados.add("JOIN" if calificador in ("", "INNER") else f"{calificador} JOIN")

    for nombre, _familia, patron in CONCEPTOS:
        if re.search(patron, limpio):
            encontrados.add(nombre)

    # Una CTE es "WITH x AS (SELECT ...)": ese "(SELECT" no es una subconsulta,
    # así que restamos las CTE antes de contar subconsultas.
    ctes = len(_CTE.findall(limpio))
    if ctes:
        encontrados.add("CTE (WITH)")
    if len(_SUBCONSULTA.findall(limpio)) > ctes:
        encontrados.add("Subconsulta")

    return sorted(encontrados, key=_clave_orden)


def _clave_orden(concepto: str):
    return (_ORDEN_FAMILIAS.index(familia_de(concepto)), _TODOS.index(concepto))


_TODOS = (
    ["JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN"]
    + [c[0] for c in CONCEPTOS]
    + ["CTE (WITH)", "Subconsulta"]
)
_FAMILIA_POR_CONCEPTO = {
    **{j: "unir" for j in _TODOS[:5]},
    **{nombre: familia for nombre, familia, _ in CONCEPTOS},
    "CTE (WITH)": "estructura",
    "Subconsulta": "estructura",
}
