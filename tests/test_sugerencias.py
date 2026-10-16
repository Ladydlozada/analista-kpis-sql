"""Perfil y reglas de sugerencias, sin DuckDB (los tipos SQL se simulan)."""

import pandas as pd

from nucleo.cargadores import cargar_archivo
from nucleo.perfil import detectar_relaciones, perfilar_tabla
from nucleo.sugerencias import sugerir
from tests.datos_ejemplo import crear_base

TIPOS = {"Int64": "BIGINT", "Float64": "DOUBLE", "object": "VARCHAR"}


def _perfiles(tmp_path):
    tablas, _ = cargar_archivo("ops.db", crear_base(tmp_path / "ops.db"))
    return [
        perfilar_tabla(
            t.nombre,
            [(c, TIPOS.get(str(t.datos[c].dtype), "VARCHAR")) for c in t.datos.columns],
            t.datos,
            len(t.datos),
        )
        for t in tablas
    ]


def test_roles(tmp_path):
    pedidos = next(p for p in _perfiles(tmp_path) if p.nombre == "pedidos")
    roles = {c.nombre: c.rol for c in pedidos.columnas}
    assert roles["id_cliente"] == "id"
    assert roles["fecha_entrega"] == "fecha"          # era texto en SQLite
    assert roles["estado"] == "categoria"
    assert roles["monto"] == "numero"
    assert pedidos.clave == "id_pedido"
    assert pedidos.columna("fecha_entrega").fecha_sql("x") == "TRY_CAST(x AS DATE)"


def test_relaciones(tmp_path):
    relaciones = {(r.tabla, r.columna, r.destino) for r in detectar_relaciones(_perfiles(tmp_path))}
    assert relaciones == {
        ("pedidos", "id_cliente", "clientes"),
        ("pedidos", "id_deposito", "depositos"),
        ("pedidos", "id_transportista", "transportistas"),
    }


def test_fecha_dd_mm_aaaa():
    muestra = pd.DataFrame({"f": ["01/02/2026", "15/03/2026", None]})
    p = perfilar_tabla("t", [("f", "VARCHAR")], muestra, 3)
    assert p.columna("f").rol == "fecha"
    assert "TRY_STRPTIME" in p.columna("f").expr_fecha


def test_sugerencias_esperadas(tmp_path):
    sugerencias = sugerir(_perfiles(tmp_path))
    preguntas = [s.pregunta for s in sugerencias]
    categorias = {s.categoria for s in sugerencias}
    assert {"Volumen", "Cancelaciones", "Montos", "Tiempos de ciclo", "Puntualidad",
            "Calidad de datos"} <= categorias
    # El plazo se compara con la entrega (que a veces lo supera), no con la creación.
    puntualidad = [s for s in sugerencias if s.categoria == "Puntualidad"]
    assert puntualidad and all("fecha_entrega > fecha_prometida" in s.sql for s in puntualidad)
    assert any("por transportista" in p for p in preguntas)
    # Toda sugerencia trae explicación.
    assert all(s.por_que and s.pasos and s.sql.rstrip().endswith(";") for s in sugerencias)


def test_sin_patrones_no_inventa():
    muestra = pd.DataFrame({"comentario": [f"texto {i}" for i in range(50)]})
    p = perfilar_tabla("notas", [("comentario", "VARCHAR")], muestra, 50)
    assert sugerir([p]) == []


def _tickets():
    """Mini mesa de ayuda: fechas con hora, prioridad 'Baja' y satisfacción 1-5."""
    ap = pd.to_datetime(["2026-01-05 09:00", "2026-01-05 10:00", "2026-01-06 08:00",
                         "2026-01-06 15:00", "2026-01-07 11:00", "2026-01-08 09:30"])
    return pd.DataFrame({
        "id_ticket": pd.array(range(1, 7), dtype="Int64"),
        "fecha_apertura": ap,
        "fecha_limite_sla": ap + pd.Timedelta(hours=4),
        # 2 de 6 cierran el mismo día pero DESPUÉS del límite: por día no se verían
        "fecha_cierre": ap + pd.to_timedelta([2, 6, 3, 5, 1, 2], unit="h"),
        "prioridad": ["Baja", "Alta", "Baja", "Media", "Alta", "Baja"],
        "estado": ["Cerrado", "Cerrado", "Rechazado", "Cerrado", "Cerrado", "Cerrado"],
        "satisfaccion": pd.array([5, 2, 4, 3, 5, 4], dtype="Int64"),
    })


def _perfil_tickets():
    df = _tickets()
    tipos = [("id_ticket", "BIGINT"), ("fecha_apertura", "TIMESTAMP"), ("fecha_limite_sla", "TIMESTAMP"),
             ("fecha_cierre", "TIMESTAMP"), ("prioridad", "VARCHAR"), ("estado", "VARCHAR"),
             ("satisfaccion", "BIGINT")]
    return perfilar_tabla("tickets", tipos, df, len(df))


def test_baja_no_es_negativa_fuera_de_un_estado():
    preguntas = [s.pregunta for s in sugerir([_perfil_tickets()])]
    assert not any("'Baja'" in p for p in preguntas)
    assert any("'Rechazado'" in p for p in preguntas)


def test_fechas_con_hora_se_miden_en_horas():
    p = _perfil_tickets()
    assert p.columna("fecha_cierre").con_hora
    sugerencias = sugerir([p])
    sla = next(s for s in sugerencias if s.categoria == "Puntualidad")
    assert "CAST(" not in sla.sql.split("SELECT", 2)[1]  # compara momentos, no días
    assert "fecha_cierre > fecha_limite_sla" in sla.sql
    tiempos = [s for s in sugerencias if s.categoria == "Tiempos de ciclo"]
    assert tiempos and all("DATE_DIFF('minute'" in s.sql for s in tiempos)


def test_escala_no_se_suma():
    p = _perfil_tickets()
    assert p.columna("satisfaccion").rol == "escala"
    sugerencias = sugerir([p])
    assert not any("SUM(satisfaccion)" in s.sql for s in sugerencias)
    assert any(s.categoria == "Satisfacción" and "pct_4_o_5" in s.sql for s in sugerencias)


def test_columna_vacia_y_tabla_chica():
    df = pd.DataFrame({"f": pd.to_datetime(["2026-01-01", "2026-02-01"]), "obs": [None, None]})
    p = perfilar_tabla("chica", [("f", "DATE"), ("obs", "VARCHAR")], df, 2)
    assert p.columna("obs").rol == "vacia"
    assert sugerir([p]) == []  # 2 filas: sin "volumen por mes"
