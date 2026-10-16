"""Normalización de nombres de tablas y columnas.

Por qué existe: en Excel es común tener columnas como "Fecha de Entrega",
"Año" o "Monto ($)". En SQL esos nombres obligan a escribir comillas cada vez
("Fecha de Entrega"), y más adelante el modelo de lenguaje también tendría que
acordarse de ponerlas. Convertirlos a snake_case sin tildes (fecha_de_entrega)
hace que las consultas sean más cómodas y menos propensas a errores.
"""

import re
import unicodedata

# Palabras reservadas de SQL (DuckDB sigue la lista de PostgreSQL). Una columna
# llamada "order" o "group" rompería un SELECT si no se pone entre comillas,
# así que le agregamos un "_" al final: order -> order_
RESERVADAS = {
    "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric",
    "both", "case", "cast", "check", "collate", "column", "constraint", "create",
    "default", "deferrable", "desc", "distinct", "do", "else", "end", "except",
    "false", "fetch", "for", "foreign", "from", "grant", "group", "having", "in",
    "initially", "intersect", "into", "lateral", "leading", "limit", "not", "null",
    "offset", "on", "only", "or", "order", "placing", "primary", "references",
    "returning", "select", "symmetric", "table", "then", "to", "trailing", "true",
    "union", "unique", "using", "variadic", "when", "where", "window", "with",
}


def normalizar(texto, por_defecto: str = "col") -> str:
    """'Fecha de Entrega' -> 'fecha_de_entrega', 'Año' -> 'ano', '2024' -> 'col_2024'."""
    # NFKD separa cada letra de su tilde ("á" -> "a" + "´"); después
    # descartamos las tildes sueltas (caracteres "combining").
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    # Todo lo que no sea letra o número pasa a ser "_", y sacamos "_" de los bordes.
    texto = re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_")
    if not texto:
        texto = por_defecto
    if texto[0].isdigit():  # un identificador SQL no puede empezar con número
        texto = f"{por_defecto}_{texto}"
    if texto in RESERVADAS:
        texto += "_"
    return texto


def hacer_unicos(nombres: list[str]) -> list[str]:
    """Si dos columnas terminan con el mismo nombre ('Monto' y 'monto'),
    renombra las repetidas: ['monto', 'monto_2']."""
    usados: set[str] = set()
    resultado = []
    for nombre in nombres:
        candidato, k = nombre, 2
        while candidato in usados:
            candidato = f"{nombre}_{k}"
            k += 1
        usados.add(candidato)
        resultado.append(candidato)
    return resultado
