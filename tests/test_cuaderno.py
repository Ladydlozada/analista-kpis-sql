import pandas as pd

from nucleo.cuaderno import ESQUEMA, Cuaderno, Entrada, mismo_resultado


def _entrada(**cambios):
    datos = dict(
        pregunta="¿Cuál es la tasa de cancelación por depósito?",
        sql="SELECT id_deposito, AVG(CASE WHEN estado = 'cancelado' THEN 1 ELSE 0 END) FROM pedidos GROUP BY 1",
        pasos=["Marca 1 si está cancelado.", "Promedia por depósito."],
        ojo="El promedio de 0/1 es directamente la tasa.",
        categoria="Cancelaciones",
        etiquetas=["logística"],
        conceptos=["GROUP BY", "AVG", "CASE WHEN"],
        tablas=["pedidos"],
        resultado=pd.DataFrame({"id_deposito": [1, 2], "tasa": [0.1, 0.25]}),
        filas_resultado=2,
    )
    datos.update(cambios)
    return Entrada(**datos)


def test_guardar_y_leer(tmp_path):
    c = Cuaderno(tmp_path / "c.sqlite")
    id_ = c.guardar(_entrada())
    e = c.obtener(id_)
    assert e.pregunta.startswith("¿Cuál")
    assert e.pasos == ["Marca 1 si está cancelado.", "Promedia por depósito."]
    assert e.etiquetas == ["logística"]
    assert e.resultado["tasa"].tolist() == [0.1, 0.25]
    assert e.autor == "manual"


def test_el_archivo_persiste(tmp_path):
    ruta = tmp_path / "c.sqlite"
    Cuaderno(ruta).guardar(_entrada())
    assert len(Cuaderno(ruta).buscar()) == 1  # otra instancia, mismo archivo


def test_buscar_sin_tildes_y_filtros(tmp_path):
    c = Cuaderno(tmp_path / "c.sqlite")
    c.guardar(_entrada())
    c.guardar(_entrada(pregunta="Ranking de clientes", categoria="Clientes",
                       conceptos=["RANK", "OVER"], etiquetas=[], autor="claude"))
    assert len(c.buscar("cancelacion")) == 1          # sin tilde encuentra "cancelación"
    assert len(c.buscar("LOGISTICA")) == 1            # busca también en etiquetas
    assert len(c.buscar(concepto="RANK")) == 1        # json_each
    assert len(c.buscar(categoria="Cancelaciones")) == 1
    assert len(c.buscar(autor="claude")) == 1
    assert len(c.buscar()) == 2
    assert c.buscar()[0].pregunta == "Ranking de clientes"  # más reciente primero


def test_categorias_y_conceptos_usados(tmp_path):
    c = Cuaderno(tmp_path / "c.sqlite")
    c.guardar(_entrada(categoria="Mi KPI propio"))
    assert c.categorias()[-1] == "Mi KPI propio"
    assert "Puntualidad" in c.categorias()
    assert c.conceptos_usados() == ["AVG", "CASE WHEN", "GROUP BY"]


def test_editar_notas_y_borrar(tmp_path):
    c = Cuaderno(tmp_path / "c.sqlite")
    id_ = c.guardar(_entrada())
    c.actualizar_notas(id_, "  Probar con HAVING  ")
    assert c.obtener(id_).notas == "Probar con HAVING"
    c.eliminar(id_)
    assert c.obtener(id_) is None


def test_migra_un_cuaderno_de_la_version_anterior(tmp_path):
    import sqlite3
    ruta = tmp_path / "viejo.sqlite"
    con = sqlite3.connect(ruta)
    con.executescript(
        ESQUEMA.replace("CHECK (autor IN ('manual', 'claude', 'sugerencia'))",
                        "CHECK (autor IN ('manual', 'claude'))")
    )
    con.execute("INSERT INTO consultas (creada, actualizada, pregunta, sql) "
                "VALUES ('2026-10-01', '2026-10-01', 'vieja', 'SELECT 1')")
    con.commit()
    con.close()

    c = Cuaderno(ruta)  # al abrirlo, migra
    assert [e.pregunta for e in c.buscar()] == ["vieja"]  # no se perdió nada
    c.guardar(_entrada(autor="sugerencia"))               # y ya acepta el autor nuevo
    assert len(c.buscar(autor="sugerencia")) == 1


def test_mismo_resultado_con_fechas(tmp_path):
    c = Cuaderno(tmp_path / "c.sqlite")
    df = pd.DataFrame({"mes": pd.to_datetime(["2026-01-01", "2026-02-01"]),
                       "n": pd.array([3, None], dtype="Int64")})
    id_ = c.guardar(_entrada(resultado=df, filas_resultado=2))
    e = c.obtener(id_)
    assert mismo_resultado(e, df.copy())
    otro = df.copy()
    otro.loc[0, "n"] = 4
    assert not mismo_resultado(e, otro)
    c.actualizar_resultado(id_, otro)
    assert mismo_resultado(c.obtener(id_), otro)
