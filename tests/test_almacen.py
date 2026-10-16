"""Pruebas del almacén DuckDB (se saltan si duckdb no está instalado)."""

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from nucleo.almacen import Almacen  # noqa: E402
from nucleo.cargadores import TablaCargada, cargar_archivo  # noqa: E402


def _tabla():
    df = pd.DataFrame(
        {
            "id": pd.array([1, 2, 3], dtype="Int64"),
            "estado": pd.Series(["ok", "  ", None], dtype=object),
        }
    )
    return TablaCargada(nombre="pedidos", datos=df, origen="prueba")


def test_guardar_y_consultar():
    a = Almacen()
    assert a.guardar(_tabla()) is False
    assert a.guardar(_tabla()) is True  # la segunda vez reemplaza
    assert a.tablas() == ["pedidos"]
    assert a.consultar("SELECT SUM(id) AS s FROM pedidos")["s"][0] == 6


def test_esquema_cuenta_nulos_y_blancos():
    a = Almacen()
    a.guardar(_tabla())
    esquema, sql = a.esquema("pedidos")
    fila = esquema.set_index("columna").loc["estado"]
    assert fila["tipo"] == "VARCHAR"
    assert fila["nulos"] == 1
    assert fila["en blanco"] == 1
    assert esquema.set_index("columna").loc["id", "tipo"] == "BIGINT"
    assert "COUNT(*) - COUNT" in sql


def test_sentencia_sin_resultado():
    a = Almacen()
    a.guardar(_tabla())
    assert a.consultar("CREATE VIEW v AS SELECT * FROM pedidos") is None
    assert a.consultar("INSERT INTO pedidos VALUES (4, 'ok')") is None
    assert len(a.consultar("SELECT * FROM v")) == 4


def test_summarize_y_from_primero_devuelven_filas():
    a = Almacen()
    a.guardar(_tabla())
    assert len(a.consultar("SUMMARIZE pedidos")) == 2  # una fila por columna
    assert len(a.consultar("FROM pedidos")) == 3       # sintaxis "FROM primero" de DuckDB


def test_csv_de_punta_a_punta():
    tablas, _ = cargar_archivo("v.csv", "Año;Monto\n2025;10\n2026;\n".encode("cp1252"))
    a = Almacen()
    a.guardar(tablas[0])
    assert a.columnas("v") == [("ano", "BIGINT"), ("monto", "BIGINT")]
    assert a.consultar("SELECT COUNT(monto) AS n FROM v")["n"][0] == 1
