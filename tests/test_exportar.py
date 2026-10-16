import pandas as pd

from nucleo.cuaderno import Entrada
from nucleo.exportar import a_html, a_markdown, resaltar_sql


def _entrada():
    return Entrada(
        id=3,
        creada="2026-10-15T13:00:00",
        pregunta="¿Qué transportista tiene más entregas tarde?",
        sql="SELECT nombre, COUNT(*) FROM t WHERE estado = 'a|b' GROUP BY nombre",
        pasos=["Filtra.", "Cuenta."],
        ojo="Las fechas son texto.",
        categoria="Puntualidad",
        etiquetas=["logística"],
        conceptos=["WHERE", "GROUP BY", "COUNT"],
        tablas=["t"],
        resultado=pd.DataFrame({"nombre": ["Rápido|Sur"], "n": [3]}),
        filas_resultado=1,
        notas="Probar en %.",
    )


def test_markdown():
    md = a_markdown([_entrada()])
    assert "## 3. ¿Qué transportista" in md
    assert "```sql" in md
    assert "1. Filtra." in md
    assert "**KPI: Puntualidad**" in md
    assert "Rápido\\|Sur" in md  # el | se escapa para no romper la tabla


def test_html_escapa_y_resalta():
    pagina = a_html([_entrada()])
    assert pagina.startswith("<!doctype html>")
    assert '<span class="kw">SELECT</span>' in pagina
    assert '<span class="fn">COUNT</span>' in pagina
    assert "KPI · Puntualidad" in pagina


def test_resaltar_no_pinta_dentro_de_textos():
    assert resaltar_sql("SELECT 'FROM <x>'") == (
        '<span class="kw">SELECT</span> <span class="st">&#x27;FROM &lt;x&gt;&#x27;</span>'
    )
