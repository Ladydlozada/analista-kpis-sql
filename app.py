"""Punto de entrada de la app: barra lateral de carga + tres pestañas.

Cómo piensa Streamlit: cada vez que tocás un botón o un control, el script
entero se vuelve a ejecutar de arriba a abajo. Por eso lo que tiene que
sobrevivir entre ejecuciones (la conexión DuckDB, la última consulta) se
guarda en st.session_state, que es un diccionario por usuario.

¿Por qué no @st.cache_resource para la conexión DuckDB? Porque ese caché es
compartido por TODOS los usuarios: si publicás la app, dos personas verían
las tablas de la otra. session_state es individual.
"""

from pathlib import Path

import streamlit as st

from nucleo.almacen import Almacen
from nucleo.cargadores import EXTENSIONES, cargar_archivo
from nucleo.cuaderno import Cuaderno
from vistas import consola, cuaderno as vista_cuaderno, sugerencias, tablas

RUTA_CUADERNO = Path(__file__).parent / "datos" / "cuaderno.sqlite"

st.set_page_config(page_title="Analista de KPIs", page_icon="📊", layout="wide")


def obtener_almacen() -> Almacen:
    # isinstance también protege de un caso sutil: si cambiás el código con la
    # app abierta, Streamlit recarga la clase Almacen, pero el objeto guardado
    # en session_state sigue siendo de la clase VIEJA (sin los métodos nuevos).
    # Para Python son clases distintas, así que isinstance da False y se crea
    # uno nuevo. Las tablas en memoria se pierden: hay que volver a cargarlas.
    if not isinstance(st.session_state.get("almacen"), Almacen):
        if "almacen" in st.session_state:
            st.toast("El código cambió: volvé a cargar tus archivos.", icon="🔄")
        st.session_state.almacen = Almacen()
    return st.session_state.almacen


def obtener_cuaderno() -> Cuaderno:
    # El cuaderno vive en disco, así que recrearlo no pierde nada.
    if not isinstance(st.session_state.get("cuaderno"), Cuaderno):
        st.session_state.cuaderno = Cuaderno(RUTA_CUADERNO)
    return st.session_state.cuaderno


almacen = obtener_almacen()
cuaderno = obtener_cuaderno()

# ================================================================ Barra lateral: carga
with st.sidebar:
    st.header("Cargar datos")
    archivos = st.file_uploader(
        "CSV, Excel o SQLite", type=EXTENSIONES, accept_multiple_files=True
    )
    # Botón explícito: si cargáramos apenas se sube el archivo, cada rerun de
    # Streamlit lo volvería a procesar.
    if st.button("Cargar en DuckDB", type="primary", disabled=not archivos):
        mensajes = []
        for archivo in archivos:
            try:
                tablas_cargadas, avisos = cargar_archivo(archivo.name, archivo.getvalue())
            except ValueError as e:
                mensajes.append(("error", f"**{archivo.name}**: {e}"))
                continue
            for tabla in tablas_cargadas:
                reemplazada = almacen.guardar(tabla)
                verbo = "reemplazada" if reemplazada else "creada"
                mensajes.append(
                    ("ok", f"`{tabla.nombre}` {verbo} ({len(tabla.datos):,} filas)")
                )
            mensajes += [("aviso", a) for a in avisos]
        st.session_state.mensajes_carga = mensajes

    for tipo, texto in st.session_state.get("mensajes_carga", []):
        {"ok": st.success, "error": st.error, "aviso": st.warning}[tipo](texto)

    st.divider()
    st.caption(f"📓 Cuaderno guardado en `{RUTA_CUADERNO.relative_to(Path(__file__).parent)}`")

# ================================================================ Pestañas
st.title("📊 Analista de KPIs")

pestana_tablas, pestana_sugerencias, pestana_sql, pestana_cuaderno = st.tabs(
    ["Tablas", "💡 Sugerencias de KPIs", "Consola SQL", "📓 Cuaderno"]
)

with pestana_tablas:
    tablas.mostrar(almacen)
with pestana_sugerencias:
    sugerencias.mostrar(almacen, cuaderno)
with pestana_sql:
    consola.mostrar(almacen, cuaderno)
with pestana_cuaderno:
    vista_cuaderno.mostrar(almacen, cuaderno)
