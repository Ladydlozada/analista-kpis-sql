"""La prueba definitiva: cada SQL sugerido se ejecuta en DuckDB sin errores.

Se salta si duckdb no está instalado.
"""

import pytest

pytest.importorskip("duckdb")

from nucleo.almacen import Almacen  # noqa: E402
from nucleo.cargadores import cargar_archivo  # noqa: E402
from nucleo.perfil import perfilar_almacen  # noqa: E402
from nucleo.sugerencias import sugerir  # noqa: E402
from tests.datos_ejemplo import crear_base  # noqa: E402


def test_todas_las_sugerencias_corren_en_duckdb(tmp_path):
    almacen = Almacen()
    tablas, _ = cargar_archivo("ops.db", crear_base(tmp_path / "ops.db"))
    for t in tablas:
        almacen.guardar(t)

    sugerencias = sugerir(perfilar_almacen(almacen))
    assert len(sugerencias) >= 15

    errores = []
    for s in sugerencias:
        try:
            df = almacen.consultar(s.sql)
            assert df is not None and len(df) > 0, "no devolvió filas"
        except Exception as e:  # juntamos todos los errores para verlos de una vez
            errores.append(f"{s.pregunta}\n{e}\n{s.sql}")
    assert not errores, "\n\n".join(errores)


def test_resultados_con_sentido(tmp_path):
    almacen = Almacen()
    for t in cargar_archivo("ops.db", crear_base(tmp_path / "ops.db"))[0]:
        almacen.guardar(t)
    sugerencias = {s.pregunta: s for s in sugerir(perfilar_almacen(almacen))}

    huerfanos = sugerencias["¿Hay valores de pedidos.id_cliente que no existen en clientes?"]
    assert almacen.consultar(huerfanos.sql)["filas_huerfanas"][0] > 0  # el cliente 21 no existe

    volumen = sugerencias["¿Cuántos registros de pedidos hay por mes?"]
    assert almacen.consultar(volumen.sql)["cantidad"].sum() == 120


def test_mesa_de_ayuda_con_horas_en_duckdb():
    from tests.test_sugerencias import _tickets
    from nucleo.cargadores import TablaCargada

    almacen = Almacen()
    df = _tickets()
    df["prioridad"] = df["prioridad"].astype(object)
    df["estado"] = df["estado"].astype(object)
    almacen.guardar(TablaCargada("tickets", df, "prueba"))
    sugerencias = sugerir(perfilar_almacen(almacen))
    for s in sugerencias:
        assert almacen.consultar(s.sql) is not None, s.sql
    sla = next(s for s in sugerencias if s.pregunta.startswith("¿Qué porcentaje supera el plazo ("))
    # Tickets 2 (6 h) y 4 (5 h) superan el límite de 4 h: 2 de 6 = 33,3 %
    assert almacen.consultar(sla.sql)["pct_fuera_de_plazo"][0] == 33.3
