"""El cuaderno de consultas: guarda cada consulta con su explicación en un archivo SQLite.

Decisiones:

- ¿Por qué SQLite y no DuckDB? Son herramientas para trabajos distintos.
  DuckDB es "analítico" (OLAP): rapidísimo para recorrer millones de filas y
  agregarlas. SQLite es "transaccional" (OLTP): ideal para guardar, editar y
  borrar registros sueltos, uno por vez. El cuaderno hace justamente eso.
  Además el archivo .sqlite se abre con cualquier herramienta (DBeaver, DB
  Browser for SQLite) y viene incluido en Python.

- Una conexión nueva por operación (ver _conectar). Streamlit puede atender
  cada interacción en un hilo distinto, y una conexión sqlite3 por defecto
  solo se puede usar en el hilo que la creó. Abrir una conexión es baratísimo,
  así que abrimos, hacemos la operación y cerramos.

- Listas (pasos, etiquetas, conceptos) guardadas como JSON en una columna.
  Lo "de manual" sería una tabla aparte de etiquetas con relación muchos a
  muchos. Para un cuaderno personal, JSON es más simple, y SQLite igual permite
  consultarlo con json_each() (lo usamos para filtrar por concepto).

- El resultado se guarda como "foto" del momento (hasta 200 filas). Así podés
  ver qué dio la consulta aunque no tengas los datos cargados, y comparar
  cuando la re-ejecutes.
"""

import io
import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

MAX_FILAS_GUARDADAS = 200

CATEGORIAS_SUGERIDAS = [
    "Puntualidad",
    "Cancelaciones",
    "Volumen",
    "Tiempos de ciclo",
    "Clientes",
    "Calidad de datos",
]

ESQUEMA = """
CREATE TABLE IF NOT EXISTS consultas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    creada          TEXT NOT NULL,
    actualizada     TEXT NOT NULL,
    pregunta        TEXT NOT NULL,
    sql             TEXT NOT NULL,
    pasos           TEXT NOT NULL DEFAULT '[]',   -- JSON: ["paso 1", "paso 2"]
    ojo             TEXT NOT NULL DEFAULT '',
    notas           TEXT NOT NULL DEFAULT '',
    autor           TEXT NOT NULL DEFAULT 'manual'
                    CHECK (autor IN ('manual', 'claude', 'sugerencia')),
    categoria       TEXT NOT NULL DEFAULT '',
    etiquetas       TEXT NOT NULL DEFAULT '[]',   -- JSON
    conceptos       TEXT NOT NULL DEFAULT '[]',   -- JSON, detectados del SQL
    tablas          TEXT NOT NULL DEFAULT '[]',   -- JSON
    resultado       TEXT,                         -- JSON (pandas, orient="split")
    filas_resultado INTEGER
);
"""


@dataclass
class Entrada:
    pregunta: str
    sql: str
    pasos: list[str] = field(default_factory=list)
    ojo: str = ""
    notas: str = ""
    autor: str = "manual"           # "manual", "claude" o "sugerencia" (reglas de la app)
    categoria: str = ""
    etiquetas: list[str] = field(default_factory=list)
    conceptos: list[str] = field(default_factory=list)
    tablas: list[str] = field(default_factory=list)
    resultado: pd.DataFrame | None = None
    filas_resultado: int | None = None
    id: int | None = None
    creada: str = ""
    actualizada: str = ""


class Cuaderno:
    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as con:
            con.executescript(ESQUEMA)
            _migrar(con)

    @contextmanager
    def _conectar(self):
        con = sqlite3.connect(self.ruta)
        con.row_factory = sqlite3.Row  # filas accesibles por nombre: fila["pregunta"]
        # Registramos una función Python para usarla DENTRO del SQL. SQLite no
        # sabe que "cancelación" y "cancelacion" son lo mismo; sin_tildes() sí.
        con.create_function("sin_tildes", 1, sin_tildes, deterministic=True)
        try:
            yield con
            con.commit()
        finally:
            con.close()  # ojo: "with sqlite3.connect()" hace commit pero NO cierra

    # ------------------------------------------------------------ escribir

    def guardar(self, e: Entrada) -> int:
        ahora = _ahora()
        with self._conectar() as con:
            cursor = con.execute(
                """
                INSERT INTO consultas (creada, actualizada, pregunta, sql, pasos, ojo,
                    notas, autor, categoria, etiquetas, conceptos, tablas,
                    resultado, filas_resultado)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ahora, ahora, e.pregunta.strip(), e.sql.strip(),
                    _a_json(e.pasos), e.ojo.strip(), e.notas.strip(), e.autor,
                    e.categoria.strip(), _a_json(e.etiquetas), _a_json(e.conceptos),
                    _a_json(e.tablas), _df_a_json(e.resultado), e.filas_resultado,
                ],
            )
            return cursor.lastrowid

    def actualizar_notas(self, id_: int, notas: str) -> None:
        with self._conectar() as con:
            con.execute(
                "UPDATE consultas SET notas = ?, actualizada = ? WHERE id = ?",
                [notas.strip(), _ahora(), id_],
            )

    def actualizar_resultado(self, id_: int, df: pd.DataFrame) -> None:
        with self._conectar() as con:
            con.execute(
                "UPDATE consultas SET resultado = ?, filas_resultado = ?, actualizada = ? "
                "WHERE id = ?",
                [_df_a_json(df), len(df), _ahora(), id_],
            )

    def eliminar(self, id_: int) -> None:
        with self._conectar() as con:
            con.execute("DELETE FROM consultas WHERE id = ?", [id_])

    # ------------------------------------------------------------ leer

    def buscar(
        self,
        texto: str = "",
        categoria: str | None = None,
        concepto: str | None = None,
        autor: str | None = None,
    ) -> list[Entrada]:
        """Todos los filtros son opcionales. El patrón "(? IS NULL OR col = ?)"
        permite tener UNA sola consulta con filtros que se activan o no."""
        sql = """
            SELECT *
            FROM consultas
            WHERE (:texto = '' OR sin_tildes(
                       pregunta || ' ' || sql || ' ' || pasos || ' ' || ojo || ' ' ||
                       notas || ' ' || categoria || ' ' || etiquetas
                   ) LIKE '%' || :texto || '%')
              AND (:categoria IS NULL OR categoria = :categoria)
              AND (:autor IS NULL OR autor = :autor)
              AND (:concepto IS NULL OR EXISTS (
                       SELECT 1 FROM json_each(consultas.conceptos) WHERE value = :concepto))
            ORDER BY creada DESC, id DESC
        """
        parametros = {
            "texto": sin_tildes(texto.strip()),
            "categoria": categoria,
            "autor": autor,
            "concepto": concepto,
        }
        with self._conectar() as con:
            return [_fila_a_entrada(f) for f in con.execute(sql, parametros)]

    def obtener(self, id_: int) -> Entrada | None:
        with self._conectar() as con:
            fila = con.execute("SELECT * FROM consultas WHERE id = ?", [id_]).fetchone()
        return _fila_a_entrada(fila) if fila else None

    def categorias(self) -> list[str]:
        """Las sugeridas más las que ya usaste, sin repetir."""
        with self._conectar() as con:
            usadas = [f[0] for f in con.execute(
                "SELECT DISTINCT categoria FROM consultas WHERE categoria <> '' ORDER BY 1"
            )]
        return CATEGORIAS_SUGERIDAS + [c for c in usadas if c not in CATEGORIAS_SUGERIDAS]

    def conceptos_usados(self) -> list[str]:
        with self._conectar() as con:
            return [f[0] for f in con.execute(
                "SELECT DISTINCT value FROM consultas, json_each(consultas.conceptos) ORDER BY 1"
            )]


def _migrar(con: sqlite3.Connection) -> None:
    """Migración: actualiza un cuaderno creado con una versión anterior.

    La primera versión solo aceptaba autor 'manual' o 'claude'. SQLite no
    permite modificar un CHECK, así que la receta estándar es: renombrar la
    tabla vieja, crear la nueva, copiar los datos y borrar la vieja. Todo en
    una transacción: si algo falla, no se pierde nada.
    """
    sql_actual = con.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'consultas'"
    ).fetchone()[0]
    if "'sugerencia'" in sql_actual:
        return  # ya está al día
    con.executescript(
        "BEGIN;"
        "ALTER TABLE consultas RENAME TO consultas_vieja;"
        + ESQUEMA
        + "INSERT INTO consultas SELECT * FROM consultas_vieja;"
        "DROP TABLE consultas_vieja;"
        "COMMIT;"
    )


def mismo_resultado(e: Entrada, nuevo: pd.DataFrame) -> bool:
    """¿La re-ejecución dio lo mismo que el resultado guardado?

    Truco: el guardado pasó por JSON (una fecha vuelve como texto, un entero
    puede volver como float...). Para comparar justo, hacemos pasar el nuevo
    por el MISMO camino de ida y vuelta, y comparamos los valores como texto.
    """
    if e.resultado is None or e.filas_resultado != len(nuevo):
        return False
    ida_y_vuelta = _json_a_df(_df_a_json(nuevo))
    if list(ida_y_vuelta.columns) != list(e.resultado.columns):
        return False
    return ida_y_vuelta.astype(str).equals(e.resultado.astype(str))


# ---------------------------------------------------------------- ayudantes

def sin_tildes(texto) -> str:
    if texto is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _a_json(lista) -> str:
    return json.dumps(list(lista), ensure_ascii=False)


def _df_a_json(df: pd.DataFrame | None) -> str | None:
    if df is None:
        return None
    return df.head(MAX_FILAS_GUARDADAS).to_json(
        orient="split", index=False, date_format="iso", default_handler=str, force_ascii=False
    )


def _json_a_df(texto: str | None) -> pd.DataFrame | None:
    if not texto:
        return None
    # dtype=False / convert_dates=False: devolvemos los valores tal cual se
    # guardaron, sin que pandas intente "adivinar" tipos de nuevo.
    return pd.read_json(io.StringIO(texto), orient="split", dtype=False, convert_dates=False)


def _fila_a_entrada(f: sqlite3.Row) -> Entrada:
    return Entrada(
        id=f["id"],
        creada=f["creada"],
        actualizada=f["actualizada"],
        pregunta=f["pregunta"],
        sql=f["sql"],
        pasos=json.loads(f["pasos"]),
        ojo=f["ojo"],
        notas=f["notas"],
        autor=f["autor"],
        categoria=f["categoria"],
        etiquetas=json.loads(f["etiquetas"]),
        conceptos=json.loads(f["conceptos"]),
        tablas=json.loads(f["tablas"]),
        resultado=_json_a_df(f["resultado"]),
        filas_resultado=f["filas_resultado"],
    )
