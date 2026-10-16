import pytest

from nucleo.conceptos import detectar, familia_de


def test_consulta_de_ejemplo():
    sql = """
    SELECT t.nombre, COUNT(*) AS n
    FROM pedidos p
    JOIN transportistas t ON t.id_transportista = p.id_transportista
    WHERE TRY_CAST(p.fecha_entrega AS DATE) > TRY_CAST(p.fecha_prometida AS DATE)
    GROUP BY t.nombre
    """
    assert detectar(sql) == ["JOIN", "WHERE", "GROUP BY", "COUNT", "CAST"]


@pytest.mark.parametrize(
    "sql, esperado",
    [
        ("SELECT * FROM a LEFT JOIN b ON a.x = b.x", ["LEFT JOIN"]),
        ("SELECT * FROM a LEFT OUTER JOIN b ON 1=1 JOIN c ON 1=1", ["JOIN", "LEFT JOIN"]),
        ("SELECT RANK() OVER (PARTITION BY x ORDER BY y) FROM t", ["OVER", "PARTITION BY", "RANK"]),
        ("WITH base AS (SELECT 1) SELECT * FROM base", ["CTE (WITH)"]),
        ("SELECT * FROM t WHERE id IN (SELECT id FROM u)", ["WHERE", "IN", "Subconsulta"]),
        ("SELECT CASE WHEN x > 1 THEN 'a' END FROM t", ["CASE WHEN"]),
        ("SELECT DATE_TRUNC('month', f) FROM t", ["Fechas"]),
    ],
)
def test_detecta(sql, esperado):
    assert detectar(sql) == esperado


def test_ignora_textos_y_comentarios():
    sql = "SELECT * FROM t -- acá no hay JOIN\nWHERE estado = 'GROUP BY'"
    assert detectar(sql) == ["WHERE"]


def test_familias():
    assert familia_de("LEFT JOIN") == "unir"
    assert familia_de("ROW_NUMBER") == "ventanas"
    assert familia_de("HAVING") == "filtrar"
