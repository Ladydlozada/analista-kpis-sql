"""Pestaña Tablas: resumen, esquema, procedencia y vista previa."""

import streamlit as st

from nucleo.almacen import Almacen


def mostrar(almacen: Almacen) -> None:
    nombres = almacen.tablas()
    if not nombres:
        st.info(
            "Cargá uno o más archivos desde la barra lateral. Cada CSV, cada hoja de "
            "Excel y cada tabla de SQLite queda como una tabla consultable con SQL."
        )
        return

    st.dataframe(almacen.resumen(), hide_index=True, use_container_width=True)

    tabla = st.selectbox("Ver detalle de", nombres)
    ficha = almacen.fichas.get(tabla, {})

    col_esquema, col_ficha = st.columns([3, 2])
    with col_esquema:
        st.subheader("Esquema")
        esquema, sql_esquema = almacen.esquema(tabla)
        st.dataframe(esquema, hide_index=True, use_container_width=True)
        with st.expander("Ver el SQL que calculó nulos y blancos"):
            st.code(sql_esquema, language="sql")
    with col_ficha:
        st.subheader("Procedencia")
        st.write(ficha.get("origen", "—"))
        for aviso in ficha.get("avisos", []):
            st.warning(aviso)
        if ficha.get("renombres"):
            st.caption("Columnas renombradas para que sean cómodas en SQL:")
            st.dataframe(
                [{"original": o, "en SQL": n} for o, n in ficha["renombres"].items()],
                hide_index=True,
                use_container_width=True,
            )

    st.subheader("Vista previa")
    st.dataframe(almacen.vista_previa(tabla), hide_index=True, use_container_width=True)

    if st.button(f"Quitar `{tabla}`"):
        almacen.eliminar(tabla)
        st.rerun()
