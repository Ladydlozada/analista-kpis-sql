"""Lectura de archivos CSV, Excel y SQLite → DataFrames de pandas.

Decisiones importantes:

1. Este módulo NO importa DuckDB ni Streamlit. Recibe el nombre del archivo y
   sus bytes y devuelve tablas. Así se puede probar con pytest sin levantar la
   app, y si mañana cambiás de interfaz (o de motor SQL) este código no cambia.

2. Nunca tocamos el archivo original: trabajamos sobre los bytes en memoria.
   Para SQLite, sqlite3 necesita un archivo en disco, así que escribimos una
   copia temporal, la abrimos en modo solo lectura (mode=ro) y la borramos al final.

3. Primero se lee "en crudo" y recién en _preparar() se convierten los tipos.
   El orden importa: si pandas convierte los tipos al leer, una columna con
   1, "A-2", 3 puede quedar como texto "1", "A-2", "3" y ya no hay forma de
   saber que estaba mezclada. Primero se detecta y después se convierte.

4. convert_dtypes(): sin esto, una columna de enteros con algún vacío queda
   en decimales (119 -> 119.0), porque el NaN clásico es un float. Con los
   tipos "nullable" (Int64, boolean) los enteros siguen siendo enteros y los
   vacíos quedan como <NA>, que DuckDB lee como NULL.
"""

import csv
import io
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .nombres import hacer_unicos, normalizar

EXT_CSV = {".csv", ".tsv", ".txt"}
EXT_EXCEL = {".xlsx", ".xlsm"}
EXT_SQLITE = {".db", ".sqlite", ".sqlite3"}
EXTENSIONES = sorted(e.lstrip(".") for e in EXT_CSV | EXT_EXCEL | EXT_SQLITE)


@dataclass
class TablaCargada:
    """Una tabla lista para guardarse en DuckDB, con su 'ficha' de procedencia."""

    nombre: str                 # nombre normalizado que tendrá en SQL
    datos: pd.DataFrame
    origen: str                 # descripción legible: archivo, hoja, separador...
    renombres: dict[str, str] = field(default_factory=dict)  # original -> nuevo
    avisos: list[str] = field(default_factory=list)


def cargar_archivo(nombre_archivo: str, contenido: bytes) -> tuple[list[TablaCargada], list[str]]:
    """Punto de entrada único. Devuelve (tablas, avisos_generales).

    Lanza ValueError con un mensaje legible si el archivo no se puede leer.
    """
    ext = Path(nombre_archivo).suffix.lower()
    if ext in EXT_CSV:
        crudas, avisos = _leer_csv(nombre_archivo, contenido)
    elif ext in EXT_EXCEL:
        crudas, avisos = _leer_excel(nombre_archivo, contenido)
    elif ext in EXT_SQLITE:
        crudas, avisos = _leer_sqlite(nombre_archivo, contenido)
    else:
        raise ValueError(f"Extensión no soportada: '{ext}'. Usá: {', '.join(EXTENSIONES)}")
    tablas = [_preparar(nombre, df, origen) for nombre, df, origen in crudas]
    return tablas, avisos


# ---------------------------------------------------------------- CSV

def _leer_csv(nombre_archivo, contenido):
    texto, codificacion = _decodificar(contenido)
    separador = _detectar_separador(texto)
    try:
        df = pd.read_csv(io.StringIO(texto), sep=separador)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise ValueError(f"No se pudo leer el CSV: {e}") from e
    sep_legible = {"\t": "tabulador"}.get(separador, separador)
    origen = f"CSV «{nombre_archivo}» (separador «{sep_legible}», codificación {codificacion})"
    return [(Path(nombre_archivo).stem, df, origen)], []


def _decodificar(contenido: bytes) -> tuple[str, str]:
    """Prueba UTF-8 y, si falla, Windows-1252 (lo que suele exportar Excel en
    Windows con tildes y ñ). latin-1 nunca falla: es la última red."""
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return contenido.decode(codificacion), codificacion.replace("-sig", "")
        except UnicodeDecodeError:
            continue
    raise ValueError("No se pudo decodificar el archivo de texto.")  # inalcanzable


def _detectar_separador(texto: str) -> str:
    """csv.Sniffer mira las primeras líneas y adivina el delimitador.
    En Uruguay/Argentina es muy común el ';' porque la ',' es el separador decimal."""
    muestra = "\n".join(texto.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(muestra, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


# ---------------------------------------------------------------- Excel

def _leer_excel(nombre_archivo, contenido):
    try:
        # sheet_name=None -> lee TODAS las hojas: {nombre_hoja: DataFrame}
        hojas = pd.read_excel(io.BytesIO(contenido), sheet_name=None)
    except Exception as e:  # openpyxl lanza varios tipos según el problema
        raise ValueError(f"No se pudo leer el Excel: {e}") from e

    # Segunda lectura, liviana: las primeras filas SIN encabezado, para ver
    # dónde empieza realmente la tabla en cada hoja.
    primeras = pd.read_excel(io.BytesIO(contenido), sheet_name=None, header=None, nrows=20)

    base = Path(nombre_archivo).stem
    crudas, avisos = [], []
    for hoja, df in hojas.items():
        if df.empty and len(df.columns) == 0:
            avisos.append(f"La hoja «{hoja}» de «{nombre_archivo}» está vacía y se omitió.")
            continue
        origen = f"Excel «{nombre_archivo}», hoja «{hoja}»"
        fila = fila_de_encabezado(primeras[hoja])
        if fila > 0:
            df = pd.read_excel(io.BytesIO(contenido), sheet_name=hoja, header=fila)
            origen += f" (encabezado detectado en la fila {fila + 1}; las de arriba se ignoraron)"
        # Con una sola hoja, la tabla se llama como el archivo (ventas.xlsx -> ventas).
        # Con varias, archivo_hoja, para que no choquen entre sí.
        nombre = base if len(hojas) == 1 else f"{base}_{hoja}"
        crudas.append((nombre, df, origen))
    return crudas, avisos


def fila_de_encabezado(primeras: pd.DataFrame) -> int:
    """¿En qué fila está el encabezado? (0 = la primera, lo normal).

    Idea: una tabla "llena" casi todas sus columnas en cada fila. Los títulos
    de arriba ("Reporte mensual...") ocupan una o dos celdas. Entonces:
    medimos cuántas celdas tiene la fila más llena (el ancho de la tabla) y
    el encabezado es la primera fila que llega a ese ancho y es mayormente texto.
    """
    if primeras.empty:
        return 0
    llenas = primeras.notna().sum(axis=1)
    ancho = llenas.max()
    if ancho < 2:
        return 0  # una sola columna: no hay forma de distinguir título de encabezado
    for i, fila in primeras.iterrows():
        valores = fila.dropna()
        es_texto = sum(isinstance(v, str) for v in valores)
        if len(valores) >= 0.8 * ancho and es_texto >= 0.5 * len(valores):
            return int(i)
    return 0


# ---------------------------------------------------------------- SQLite

def _leer_sqlite(nombre_archivo, contenido):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(contenido)
        ruta = tmp.name
    try:
        # mode=ro: aunque sea una copia, dejamos explícito que solo leemos.
        con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
        try:
            nombres = [
                fila[0]
                for fila in con.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            crudas = []
            for tabla in nombres:
                sql = f'SELECT * FROM "{tabla.replace(chr(34), chr(34) * 2)}"'
                df = pd.read_sql_query(sql, con)
                crudas.append((tabla, df, f"SQLite «{nombre_archivo}», tabla «{tabla}»"))
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise ValueError(f"No parece una base SQLite válida: {e}") from e
    finally:
        os.unlink(ruta)

    avisos = [] if crudas else [f"«{nombre_archivo}» no tiene tablas."]
    return crudas, avisos


# ---------------------------------------------------------------- Preparación común

def _preparar(nombre_sugerido: str, df: pd.DataFrame, origen: str) -> TablaCargada:
    """Lo que se hace igual sin importar de dónde vino la tabla."""
    avisos = []
    df = df.copy()  # no modificamos el DataFrame que nos pasaron

    # 1) Nombres de columnas aptos para SQL, guardando el mapeo para mostrarlo.
    originales = [str(c) for c in df.columns]
    nuevos = hacer_unicos([normalizar(c) for c in originales])
    renombres = {o: n for o, n in zip(originales, nuevos) if o != n}
    df.columns = nuevos

    # 2) Columnas de texto u "object": las dejamos en un formato que DuckDB
    #    entienda sin ambigüedad (objetos Python str o None).
    columnas_texto = []
    for col in df.columns:
        serie = df[col]
        if serie.isna().all():
            avisos.append(f"La columna «{col}» está completamente vacía.")
        if pd.api.types.is_string_dtype(serie) or serie.dtype == object:
            tipo = pd.api.types.infer_dtype(serie, skipna=True)
            if tipo not in ("string", "empty"):
                # Ej.: una columna de Excel con números y textos mezclados.
                # La guardamos como texto para no perder datos; es una señal
                # de calidad que conviene revisar.
                avisos.append(
                    f"La columna «{col}» mezcla tipos ({tipo}); se guardó como texto."
                )
            columnas_texto.append(col)
            df[col] = pd.Series(
                [None if _es_nulo(v) else str(v) for v in serie],
                index=serie.index,
                dtype=object,
            )

    # 3) Recién ahora pasamos las columnas que NO son texto a tipos "nullable":
    #    enteros con vacíos -> Int64 (no float), etc. A las de texto no las
    #    tocamos: en pandas 3, convert_dtypes las vuelve a convertir aunque le
    #    digas convert_string=False.
    no_texto = [c for c in df.columns if c not in columnas_texto]
    if no_texto:
        df[no_texto] = df[no_texto].convert_dtypes(convert_string=False)

    return TablaCargada(
        nombre=normalizar(nombre_sugerido, por_defecto="tabla"),
        datos=df,
        origen=origen,
        renombres=renombres,
        avisos=avisos,
    )


def _es_nulo(valor) -> bool:
    try:
        return bool(pd.isna(valor))
    except (TypeError, ValueError):  # listas u objetos raros: no son nulos
        return False
