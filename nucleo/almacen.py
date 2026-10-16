"""El 'almacén': una base DuckDB en memoria con las tablas que el usuario cargó.

Decisiones:

- Copiamos los datos a tablas DuckDB (CREATE TABLE ... AS SELECT) en lugar de
  dejar vistas sobre el DataFrame. Así la tabla existe por sí misma dentro de
  DuckDB: no depende de que el DataFrame siga vivo y las consultas son rápidas.
  El archivo original nunca se toca; lo que se modifica es esta copia.

- La base es en memoria (":memory:"): al cerrar la sesión desaparece. Para el
  cuaderno de consultas vamos a necesitar algo que persista, y eso irá en un
  archivo aparte (lo vemos en el próximo paso).

- Cada vez que armamos SQL con un nombre de tabla o columna usamos citar(),
  que lo pone entre comillas dobles. Los valores (no los nombres) van como
  parámetros "?" para que DuckDB los escape solo.
"""

import duckdb
import pandas as pd

from .cargadores import TablaCargada


def citar(identificador: str) -> str:
    """mi tabla -> "mi tabla". Una comilla interna se duplica: a"b -> "a""b"."""
    return '"' + identificador.replace('"', '""') + '"'


class Almacen:
    def __init__(self, ruta: str = ":memory:"):
        self.con = duckdb.connect(ruta)
        # Ficha de cada tabla: de dónde vino, qué columnas se renombraron, avisos.
        self.fichas: dict[str, dict] = {}

    # ------------------------------------------------------------ escribir

    def guardar(self, tabla: TablaCargada) -> bool:
        """Crea (o reemplaza) la tabla. Devuelve True si ya existía."""
        existia = tabla.nombre in self.tablas()
        # register() hace que DuckDB vea el DataFrame como si fuera una tabla.
        self.con.register("_entrante", tabla.datos)
        try:
            self.con.execute(
                f"CREATE OR REPLACE TABLE {citar(tabla.nombre)} AS SELECT * FROM _entrante"
            )
        finally:
            self.con.unregister("_entrante")
        self.fichas[tabla.nombre] = {
            "origen": tabla.origen,
            "renombres": tabla.renombres,
            "avisos": tabla.avisos,
        }
        return existia

    def eliminar(self, nombre: str) -> None:
        self.con.execute(f"DROP TABLE IF EXISTS {citar(nombre)}")
        self.fichas.pop(nombre, None)

    # ------------------------------------------------------------ leer

    def tablas(self) -> list[str]:
        # information_schema es estándar SQL: la misma consulta funciona en
        # PostgreSQL, SQL Server, MySQL... Vale la pena conocerla.
        filas = self.con.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        ).fetchall()
        return [f[0] for f in filas]

    def columnas(self, tabla: str) -> list[tuple[str, str]]:
        """[(nombre_columna, tipo), ...] en el orden de la tabla."""
        return self.con.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = ?
            ORDER BY ordinal_position
            """,
            [tabla],
        ).fetchall()

    def contar(self, tabla: str) -> int:
        return self.con.execute(f"SELECT COUNT(*) FROM {citar(tabla)}").fetchone()[0]

    def resumen(self) -> pd.DataFrame:
        filas = []
        for t in self.tablas():
            filas.append(
                {
                    "tabla": t,
                    "filas": self.contar(t),
                    "columnas": len(self.columnas(t)),
                    "origen": self.fichas.get(t, {}).get("origen", "—"),
                }
            )
        return pd.DataFrame(filas)

    def esquema(self, tabla: str) -> tuple[pd.DataFrame, str]:
        """Columnas, tipos, nulos y textos en blanco. Devuelve también el SQL
        que se generó, para poder mostrarlo.

        Truco SQL: COUNT(*) cuenta filas; COUNT(columna) cuenta solo las que NO
        son NULL. La diferencia es la cantidad de nulos. Hacemos todo en UNA
        consulta (una pasada por la tabla) en vez de una consulta por columna.
        """
        cols = self.columnas(tabla)
        partes = ["COUNT(*) AS total"]
        for i, (col, tipo) in enumerate(cols):
            partes.append(f"COUNT(*) - COUNT({citar(col)}) AS nulos_{i}")
            if tipo == "VARCHAR":
                # Un texto vacío o con solo espacios no es NULL, pero en la
                # práctica también es un dato faltante.
                partes.append(
                    f"COUNT(*) FILTER (WHERE TRIM({citar(col)}) = '') AS blancos_{i}"
                )
        sql = "SELECT\n    " + ",\n    ".join(partes) + f"\nFROM {citar(tabla)}"
        fila = self.con.execute(sql).df().iloc[0]

        total = int(fila["total"])
        filas = []
        for i, (col, tipo) in enumerate(cols):
            nulos = int(fila[f"nulos_{i}"])
            blancos = int(fila[f"blancos_{i}"]) if f"blancos_{i}" in fila else None
            filas.append(
                {
                    "columna": col,
                    "tipo": tipo,
                    "nulos": nulos,
                    "% nulos": round(100 * nulos / total, 1) if total else 0.0,
                    "en blanco": blancos,
                }
            )
        return pd.DataFrame(filas), sql

    def vista_previa(self, tabla: str, n: int = 20) -> pd.DataFrame:
        return self.con.execute(f"SELECT * FROM {citar(tabla)} LIMIT {int(n)}").df()

    def consultar(self, sql: str) -> pd.DataFrame | None:
        """Ejecuta SQL libre. Devuelve None si la sentencia no produce filas
        (por ejemplo un CREATE VIEW o un INSERT).

        DuckDB responde a TODO con una tabla: a un CREATE o INSERT le contesta
        con una columna "Count" (filas afectadas). Para distinguir "consulta"
        de "comando" le pedimos a DuckDB que analice el SQL (extract_statements)
        y miramos el tipo de la última sentencia, que es la que da el resultado.
        SUMMARIZE, DESCRIBE y FROM tabla también cuentan como SELECT.
        """
        sentencias = self.con.extract_statements(sql)
        if not sentencias:
            return None
        resultado = self.con.execute(sql)
        if sentencias[-1].type not in TIPOS_CON_FILAS:
            return None
        return resultado.df()


# Tipos de sentencia que devuelven filas para mostrar.
TIPOS_CON_FILAS = {
    duckdb.StatementType.SELECT,
    duckdb.StatementType.EXPLAIN,
    duckdb.StatementType.PRAGMA,
}
