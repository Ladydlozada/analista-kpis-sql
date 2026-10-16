"""Pruebas de la carga. No necesitan DuckDB ni Streamlit.

Correr con:  pytest -q
"""

import io
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from nucleo.cargadores import cargar_archivo
from nucleo.nombres import hacer_unicos, normalizar


# ---------------------------------------------------------------- nombres

@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("Fecha de Entrega", "fecha_de_entrega"),
        ("Año", "ano"),
        ("Monto ($)", "monto"),
        ("2024", "col_2024"),
        ("order", "order_"),
        ("   ", "col"),
        ("ID-Cliente", "id_cliente"),
    ],
)
def test_normalizar(entrada, esperado):
    assert normalizar(entrada) == esperado


def test_hacer_unicos():
    assert hacer_unicos(["monto", "monto", "monto_2"]) == ["monto", "monto_2", "monto_2_2"]


# ---------------------------------------------------------------- CSV

def test_csv_punto_y_coma_en_windows_1252():
    texto = "Cliente;Año;Monto\nPérez;2025;100\nNúñez;2026;\n"
    tablas, _ = cargar_archivo("Ventas Mensuales.csv", texto.encode("cp1252"))
    t = tablas[0]
    assert t.nombre == "ventas_mensuales"
    assert list(t.datos.columns) == ["cliente", "ano", "monto"]
    assert t.renombres == {"Cliente": "cliente", "Año": "ano", "Monto": "monto"}
    assert t.datos["cliente"].tolist() == ["Pérez", "Núñez"]
    # El entero con un vacío sigue siendo entero (Int64), no 100.0
    assert str(t.datos["monto"].dtype) == "Int64"
    assert "cp1252" in t.origen and "«;»" in t.origen


def test_csv_utf8_con_coma():
    tablas, _ = cargar_archivo("a.csv", "x,y\n1,hola\n2,\n".encode("utf-8"))
    assert tablas[0].datos["y"].tolist() == ["hola", None]


def test_extension_no_soportada():
    with pytest.raises(ValueError, match="no soportada"):
        cargar_archivo("datos.json", b"{}")


# ---------------------------------------------------------------- Excel

def _excel(hojas: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for nombre, df in hojas.items():
            df.to_excel(w, sheet_name=nombre, index=False)
    return buf.getvalue()


def test_excel_varias_hojas_y_tipos_mezclados():
    contenido = _excel(
        {
            "Ene": pd.DataFrame({"Código": [1, "A-2", 3], "Fecha": pd.to_datetime(["2026-01-01"] * 3)}),
            "Feb": pd.DataFrame({"Código": [4, 5, 6]}),
        }
    )
    tablas, avisos = cargar_archivo("Stock.xlsx", contenido)
    assert [t.nombre for t in tablas] == ["stock_ene", "stock_feb"]
    ene = tablas[0]
    assert ene.datos["codigo"].tolist() == ["1", "A-2", "3"]
    assert any("mezcla tipos" in a for a in ene.avisos)
    assert pd.api.types.is_datetime64_any_dtype(ene.datos["fecha"])


def test_excel_una_hoja_se_llama_como_el_archivo():
    tablas, _ = cargar_archivo("ventas.xlsx", _excel({"Hoja1": pd.DataFrame({"a": [1]})}))
    assert tablas[0].nombre == "ventas"


# ---------------------------------------------------------------- SQLite

def test_sqlite_lee_todas_las_tablas_sin_modificar_original():
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "x.db"
        con = sqlite3.connect(ruta)
        con.execute("CREATE TABLE pedidos (id INTEGER, id_cliente INTEGER, estado TEXT)")
        con.executemany("INSERT INTO pedidos VALUES (?,?,?)", [(1, 10, "ok"), (2, None, "")])
        con.execute("CREATE TABLE clientes (id INTEGER)")
        con.commit()
        con.close()
        original = ruta.read_bytes()

        tablas, _ = cargar_archivo("x.db", original)

        assert ruta.read_bytes() == original
    por_nombre = {t.nombre: t for t in tablas}
    assert set(por_nombre) == {"pedidos", "clientes"}
    assert str(por_nombre["pedidos"].datos["id_cliente"].dtype) == "Int64"


def test_sqlite_columna_con_tipos_mezclados():
    # SQLite permite guardar texto en una columna INTEGER (tipado dinámico).
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "m.db"
        con = sqlite3.connect(ruta)
        con.execute("CREATE TABLE t (codigo INTEGER)")
        con.executemany("INSERT INTO t VALUES (?)", [(1,), ("A-2",), (3,)])
        con.commit()
        con.close()
        tablas, _ = cargar_archivo("m.db", ruta.read_bytes())
    assert tablas[0].datos["codigo"].tolist() == ["1", "A-2", "3"]
    assert any("mezcla tipos" in a for a in tablas[0].avisos)


def test_sqlite_invalido():
    with pytest.raises(ValueError, match="SQLite"):
        cargar_archivo("roto.db", b"esto no es una base")


def test_excel_encabezado_desplazado():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Resumen"
    ws["A1"] = "Reporte mensual"          # título
    ws.append([])                          # fila vacía
    ws.append(["Mes", "Abiertos", "Cerrados"])
    ws.append(["2026-01", 62, 48])
    ws.append(["2026-02", 85, 70])
    buf = io.BytesIO()
    wb.save(buf)
    tablas, _ = cargar_archivo("r.xlsx", buf.getvalue())
    t = tablas[0]
    assert list(t.datos.columns) == ["mes", "abiertos", "cerrados"]
    assert t.datos["abiertos"].tolist() == [62, 85]
    assert "fila 3" in t.origen
    assert not t.avisos  # ya no hay "tipos mezclados"


def test_fila_de_encabezado_normal_es_cero():
    from nucleo.cargadores import fila_de_encabezado
    assert fila_de_encabezado(pd.DataFrame([["a", "b"], [1, 2]])) == 0
    assert fila_de_encabezado(pd.DataFrame([["Título", None, None], [None] * 3, ["x", "y", "z"], [1, 2, 3]])) == 2
