"""Pestaña Cuaderno: buscar, filtrar, leer, re-ejecutar, editar notas y exportar."""

import duckdb
import streamlit as st

from nucleo.almacen import Almacen
from nucleo.cuaderno import Cuaderno, Entrada, mismo_resultado
from nucleo.exportar import (
    a_html,
    a_markdown,
    chips_conceptos,
    chips_meta,
    ojo_html,
    pasos_html,
)

TODAS = "Todas"
AUTORES = {
    TODAS: None,
    "Escritas por mí": "manual",
    "Sugeridas por la app": "sugerencia",
    "Generadas por Claude": "claude",
}


def mostrar(almacen: Almacen, cuaderno: Cuaderno) -> None:
    # ---------------------------------------------------------- filtros
    c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
    texto = c1.text_input("Buscar", placeholder="transportista, cancelación, JOIN…")
    categoria = c2.selectbox("Categoría", [TODAS] + cuaderno.categorias())
    concepto = c3.selectbox("Concepto SQL", [TODAS] + cuaderno.conceptos_usados())
    autor = c4.selectbox("Autor", list(AUTORES))

    entradas = cuaderno.buscar(
        texto=texto,
        categoria=None if categoria == TODAS else categoria,
        concepto=None if concepto == TODAS else concepto,
        autor=AUTORES[autor],
    )

    if not entradas:
        st.info(
            "Todavía no hay consultas con esos filtros. Ejecutá una en la Consola SQL "
            "y usá «Guardar en el cuaderno»."
        )
        return

    # ---------------------------------------------------------- exportar
    e1, e2, e3 = st.columns([4, 1, 1])
    e1.caption(f"{len(entradas)} consultas · se exportan las que ves con los filtros actuales")
    e2.download_button(
        "⬇ Markdown", a_markdown(entradas), "cuaderno_sql.md", "text/markdown",
        use_container_width=True,
    )
    e3.download_button(
        "⬇ HTML", a_html(entradas), "cuaderno_sql.html", "text/html",
        use_container_width=True,
    )

    for e in entradas:
        _tarjeta(e, almacen, cuaderno)


def _abrir_en_consola(sql: str) -> None:
    # Esto corre como "callback" (on_click): Streamlit lo ejecuta ANTES de
    # redibujar, que es el único momento en que se puede cambiar el valor de un
    # control (el editor de la consola) desde otro lado.
    st.session_state.sql_consola = sql


def _tarjeta(e: Entrada, almacen: Almacen, cuaderno: Cuaderno) -> None:
    with st.container(border=True):
        meta = f"Consulta #{e.id} · {e.creada[:10]}"
        if e.tablas:
            meta += " · tablas: " + ", ".join(e.tablas)
        st.caption(meta)
        st.markdown(f"#### {e.pregunta}")
        st.markdown(chips_meta(e), unsafe_allow_html=True)

        if e.conceptos:
            st.caption("Conceptos que practica")
            st.markdown(chips_conceptos(e.conceptos), unsafe_allow_html=True)

        st.code(e.sql, language="sql")

        if e.pasos:
            st.caption("Cómo se lee")
            st.markdown(pasos_html(e.pasos), unsafe_allow_html=True)
        if e.ojo:
            st.markdown(ojo_html(e.ojo), unsafe_allow_html=True)

        if e.resultado is not None:
            st.caption(f"Resultado guardado · {e.filas_resultado:,} filas")
            st.dataframe(e.resultado.head(20), hide_index=True, use_container_width=True)

        if e.notas:
            st.caption("Mis notas")
            st.markdown(f"_{e.notas}_")

        # ------------------------------------------------------ acciones
        b1, b2, b3, b4 = st.columns(4)
        if b1.button("▶ Re-ejecutar", key=f"re_{e.id}", use_container_width=True):
            _reejecutar(e, almacen)
        b2.button(
            "↗ Abrir en la consola", key=f"abrir_{e.id}", use_container_width=True,
            on_click=_abrir_en_consola, args=(e.sql,),
            help="Copia el SQL al editor de la Consola SQL para modificarlo.",
        )
        editar = b3.toggle("✏️ Editar notas", key=f"editar_{e.id}")
        if b4.button("🗑 Borrar", key=f"borrar_{e.id}", use_container_width=True):
            st.session_state.confirmar_borrar = e.id

        _resultado_reejecucion(e, cuaderno)

        if editar:
            with st.form(f"notas_{e.id}"):
                st.text_area("Mis notas", value=e.notas, key=f"notas_txt_{e.id}")
                st.form_submit_button(
                    "Guardar notas", on_click=_guardar_notas, args=(cuaderno, e.id)
                )

        if st.session_state.get("confirmar_borrar") == e.id:
            st.warning("¿Borrar esta consulta del cuaderno? No se puede deshacer.")
            s1, s2, _ = st.columns([1, 1, 4])
            if s1.button("Sí, borrar", key=f"si_{e.id}", type="primary"):
                cuaderno.eliminar(e.id)
                del st.session_state.confirmar_borrar
                st.rerun()
            if s2.button("Cancelar", key=f"no_{e.id}"):
                del st.session_state.confirmar_borrar
                st.rerun()


def _reejecutar(e: Entrada, almacen: Almacen) -> None:
    clave = f"reejecucion_{e.id}"
    faltan = [t for t in e.tablas if t not in almacen.tablas()]
    if faltan:
        st.session_state[clave] = {
            "error": "Faltan tablas: " + ", ".join(faltan)
            + ". Cargá el archivo de origen desde la barra lateral."
        }
        return
    try:
        st.session_state[clave] = {"df": almacen.consultar(e.sql)}
    except duckdb.Error as err:
        st.session_state[clave] = {"error": f"DuckDB devolvió un error:\n\n{err}"}


def _resultado_reejecucion(e: Entrada, cuaderno: Cuaderno) -> None:
    clave = f"reejecucion_{e.id}"
    r = st.session_state.get(clave)
    if not r:
        return
    if "error" in r:
        st.error(r["error"])
        return
    nuevo = r["df"]
    if nuevo is None:
        st.info("La sentencia no devuelve filas.")
        return

    if mismo_resultado(e, nuevo):
        st.success(f"Mismo resultado que el guardado ({len(nuevo):,} filas).")
    else:
        st.warning(
            "El resultado cambió respecto del guardado. Puede ser que hayas cargado "
            "datos distintos o más nuevos."
        )
    st.dataframe(nuevo.head(20), hide_index=True, use_container_width=True)
    if st.button("Reemplazar el resultado guardado por este", key=f"actualizar_{e.id}"):
        cuaderno.actualizar_resultado(e.id, nuevo)
        del st.session_state[clave]
        st.rerun()


def _guardar_notas(cuaderno: Cuaderno, id_: int) -> None:
    # Callback: corre antes de redibujar, por eso acá sí se puede apagar el
    # interruptor "Editar notas" (después de dibujado, Streamlit no lo permite).
    cuaderno.actualizar_notas(id_, st.session_state[f"notas_txt_{id_}"])
    st.session_state[f"editar_{id_}"] = False
