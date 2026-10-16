"""Exportar el cuaderno a Markdown (para GitHub) y a HTML (con los colores de la app).

También tiene los pedacitos de HTML (etiquetas de colores, pasos numerados)
que usa la interfaz, para que la app y el archivo exportado se vean iguales.
"""

import html
import re
from datetime import datetime

import pandas as pd

from .conceptos import FAMILIAS, familia_de
from .cuaderno import Entrada

AUTORES = {
    "manual": "Escrita por mí",
    "claude": "Generada por Claude",
    "sugerencia": "Sugerida por la app",
}

# ================================================================ piezas HTML

def chip(texto: str, fondo: str, color: str) -> str:
    return (
        f'<span style="display:inline-block;background:{fondo};color:{color};'
        f'font-size:12px;padding:2px 10px;border-radius:8px;margin:0 4px 4px 0">'
        f"{html.escape(texto)}</span>"
    )


def chips_conceptos(conceptos: list[str]) -> str:
    partes = []
    for c in conceptos:
        f = FAMILIAS[familia_de(c)]
        partes.append(chip(c, f.fondo, f.texto))
    return "".join(partes)


def chips_meta(e: Entrada) -> str:
    """Autor, categoría y etiquetas."""
    partes = [chip(AUTORES.get(e.autor, e.autor), "#E6F1FB", "#0C447C")]
    if e.categoria:
        partes.append(chip(f"KPI · {e.categoria}", "#FAEEDA", "#633806"))
    partes += [chip(f"#{t}", "#F1EFE8", "#444441") for t in e.etiquetas]
    return "".join(partes)


def pasos_html(pasos: list[str]) -> str:
    filas = []
    for i, paso in enumerate(pasos, 1):
        filas.append(
            '<div style="display:flex;gap:10px;align-items:flex-start;margin:0 0 8px">'
            '<span style="flex:none;width:24px;height:24px;border-radius:50%;'
            "background:#EEEDFE;color:#3C3489;font-size:12px;font-weight:600;"
            f'display:flex;align-items:center;justify-content:center">{i}</span>'
            f'<span style="line-height:1.6">{html.escape(paso)}</span></div>'
        )
    return "".join(filas)


def ojo_html(ojo: str) -> str:
    return (
        '<div style="border-left:3px solid #EF9F27;background:#FAEEDA;color:#633806;'
        f'padding:8px 12px;margin:4px 0 8px;font-size:14px">💡 <b>Ojo:</b> {html.escape(ojo)}</div>'
    )


# ================================================================ resaltado de SQL

PALABRAS_CLAVE = {
    "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "AS", "ON", "JOIN", "LEFT", "RIGHT",
    "FULL", "INNER", "OUTER", "CROSS", "GROUP", "BY", "ORDER", "HAVING", "LIMIT",
    "OFFSET", "WITH", "UNION", "ALL", "DISTINCT", "CASE", "WHEN", "THEN", "ELSE", "END",
    "IN", "IS", "NULL", "BETWEEN", "LIKE", "ILIKE", "ASC", "DESC", "OVER", "PARTITION",
    "QUALIFY", "DATE", "INTERVAL", "TRUE", "FALSE", "EXISTS",
}
_TOKEN = re.compile(r"(--[^\n]*|/\*.*?\*/)|('(?:[^']|'')*')|([A-Za-z_][A-Za-z_0-9]*)", re.S)


def resaltar_sql(sql: str) -> str:
    """Devuelve el SQL como HTML con colores: palabras clave, funciones,
    textos y comentarios. Va token por token para no pintar algo que está
    dentro de un texto entre comillas."""
    salida, pos = [], 0
    for m in _TOKEN.finditer(sql):
        salida.append(html.escape(sql[pos:m.start()]))
        comentario, cadena, palabra = m.groups()
        token = html.escape(m.group(0))
        if comentario:
            salida.append(f'<span class="cm">{token}</span>')
        elif cadena:
            salida.append(f'<span class="st">{token}</span>')
        elif palabra.upper() in PALABRAS_CLAVE:
            salida.append(f'<span class="kw">{token}</span>')
        elif sql[m.end():].lstrip().startswith("("):
            salida.append(f'<span class="fn">{token}</span>')
        else:
            salida.append(token)
        pos = m.end()
    salida.append(html.escape(sql[pos:]))
    return "".join(salida)


# ================================================================ Markdown

def a_markdown(entradas: list[Entrada], titulo: str = "Cuaderno de consultas SQL") -> str:
    lineas = [
        f"# {titulo}",
        "",
        f"_{len(entradas)} consultas · exportado el {datetime.now():%d/%m/%Y}_",
        "",
    ]
    for e in entradas:
        meta = [AUTORES.get(e.autor, e.autor), e.creada[:10]]
        if e.categoria:
            meta.insert(0, f"**KPI: {e.categoria}**")
        if e.tablas:
            meta.append("tablas: " + ", ".join(f"`{t}`" for t in e.tablas))
        lineas += [f"## {e.id}. {e.pregunta}", "", " · ".join(meta), ""]
        if e.conceptos:
            lineas += ["**Conceptos:** " + " · ".join(f"`{c}`" for c in e.conceptos), ""]
        if e.etiquetas:
            lineas += ["**Etiquetas:** " + " ".join(f"#{t}" for t in e.etiquetas), ""]
        lineas += ["```sql", e.sql, "```", ""]
        if e.pasos:
            lineas += ["**Cómo se lee**", ""]
            lineas += [f"{i}. {p}" for i, p in enumerate(e.pasos, 1)]
            lineas.append("")
        if e.ojo:
            lineas += [f"> 💡 **Ojo:** {e.ojo}", ""]
        if e.resultado is not None:
            lineas += [f"**Resultado** ({e.filas_resultado:,} filas)", "", _tabla_md(e.resultado), ""]
        if e.notas:
            lineas += ["**Mis notas**", "", e.notas, ""]
        lineas += ["---", ""]
    return "\n".join(lineas)


def _tabla_md(df: pd.DataFrame, max_filas: int = 10) -> str:
    def celda(v):
        if v is None or (not isinstance(v, (list, dict)) and pd.isna(v)):
            return ""
        return str(v).replace("|", "\\|").replace("\n", " ")

    columnas = [str(c) for c in df.columns]
    lineas = [
        "| " + " | ".join(columnas) + " |",
        "|" + "|".join("---" for _ in columnas) + "|",
    ]
    for fila in df.head(max_filas).itertuples(index=False):
        lineas.append("| " + " | ".join(celda(v) for v in fila) + " |")
    if len(df) > max_filas:
        lineas.append(f"\n_… y {len(df) - max_filas} filas más_")
    return "\n".join(lineas)


# ================================================================ HTML

_CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#F7F6F2;color:#2C2C2A;
     margin:0;padding:32px 16px;line-height:1.5}
main{max-width:820px;margin:0 auto}
h1{font-size:26px;font-weight:600;margin:0 0 4px}
.sub{color:#5F5E5A;margin:0 0 24px}
article{background:#fff;border:1px solid #E4E2DA;border-radius:12px;padding:20px 24px;margin:0 0 20px}
.meta{font-size:13px;color:#5F5E5A;margin:0 0 4px}
h2{font-size:19px;font-weight:600;margin:0 0 10px}
.lbl{font-size:12px;color:#5F5E5A;margin:14px 0 6px;text-transform:uppercase;letter-spacing:.04em}
pre{background:#F1EFE8;border-radius:8px;padding:12px 14px;font-size:13px;line-height:1.6;
    overflow-x:auto;margin:0}
.kw{color:#534AB7;font-weight:600}.fn{color:#854F0B}.st{color:#3B6D11}.cm{color:#888780;font-style:italic}
table{border-collapse:collapse;font-size:13px;width:100%}
th{text-align:left;color:#5F5E5A;font-weight:500;border-bottom:1px solid #E4E2DA;padding:4px 8px 4px 0}
td{padding:4px 8px 4px 0;border-bottom:1px solid #F1EFE8}
.notas{font-family:Georgia,serif;color:#444441;white-space:pre-wrap}
@media (prefers-color-scheme: dark){
  body{background:#1F1E1D;color:#E8E6DE}.sub,.meta,.lbl,th{color:#B4B2A9}
  article{background:#2A2927;border-color:#3D3C39}pre{background:#1F1E1D}
  .kw{color:#AFA9EC}.fn{color:#FAC775}.st{color:#97C459}.notas{color:#D3D1C7}
  td{border-color:#3D3C39}
}
"""


def a_html(entradas: list[Entrada], titulo: str = "Cuaderno de consultas SQL") -> str:
    articulos = []
    for e in entradas:
        partes = [
            f'<p class="meta">Consulta #{e.id} · {html.escape(e.creada[:10])}'
            + (f" · tablas: {html.escape(', '.join(e.tablas))}" if e.tablas else "")
            + "</p>",
            f"<h2>{html.escape(e.pregunta)}</h2>",
            f"<div>{chips_meta(e)}</div>",
        ]
        if e.conceptos:
            partes += ['<p class="lbl">Conceptos que practica</p>', f"<div>{chips_conceptos(e.conceptos)}</div>"]
        partes += ['<p class="lbl">SQL</p>', f"<pre>{resaltar_sql(e.sql)}</pre>"]
        if e.pasos:
            partes += ['<p class="lbl">Cómo se lee</p>', pasos_html(e.pasos)]
        if e.ojo:
            partes.append(ojo_html(e.ojo))
        if e.resultado is not None:
            tabla = e.resultado.head(15).to_html(index=False, border=0, na_rep="", escape=True)
            partes += [f'<p class="lbl">Resultado · {e.filas_resultado:,} filas</p>', tabla]
        if e.notas:
            partes += ['<p class="lbl">Mis notas</p>', f'<p class="notas">{html.escape(e.notas)}</p>']
        articulos.append("<article>" + "\n".join(partes) + "</article>")

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titulo)}</title><style>{_CSS}</style></head>
<body><main>
<h1>{html.escape(titulo)}</h1>
<p class="sub">{len(entradas)} consultas · exportado el {datetime.now():%d/%m/%Y}</p>
{''.join(articulos)}
</main></body></html>"""
