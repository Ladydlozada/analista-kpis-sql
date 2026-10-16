"""Pestaña Consola SQL: escribir, ejecutar y guardar en el cuaderno.

Detalle de Streamlit importante acá: al tocar cualquier control, el script
corre de nuevo y el resultado de "Ejecutar" se perdería (el botón solo vale
True en la ejecución en la que se apretó). Por eso guardamos la última
consulta ejecutada y su resultado en st.session_state["ultima"].
"""

import re

import duckdb
import streamlit as st

from nucleo.almacen import Almacen
from nucleo.conceptos import detectar
from nucleo.cuaderno import Cuaderno, Entrada
from nucleo.exportar import chips_conceptos

SIN_CATEGORIA = "(sin categoría)"


def mostrar(almacen: Almacen, cuaderno: Cuaderno) -> None:
    nombres = almacen.tablas()
    if not nombres:
        st.info("Primero cargá datos desde la barra lateral.")
        return

    # El texto del editor vive en session_state con la clave "sql_consola".
    # Así otras pantallas (el cuaderno) pueden mandarte una consulta acá.
    if "sql_consola" not in st.session_state:
        st.session_state.sql_consola = f"SELECT *\nFROM {nombres[0]}\nLIMIT 10"

    st.caption(
        "Las tablas son copias: podés experimentar sin miedo, el archivo original no se toca. "
        "Probá `SUMMARIZE nombre_tabla` para un perfil rápido de cada columna."
    )
    st.text_area("Consulta", key="sql_consola", height=200)

    if st.button("Ejecutar", type="primary"):
        sql = st.session_state.sql_consola
        try:
            st.session_state.ultima = {"sql": sql, "df": almacen.consultar(sql)}
        except duckdb.Error as e:
            st.session_state.ultima = {"sql": sql, "error": str(e)}

    ultima = st.session_state.get("ultima")
    if not ultima:
        return
    if "error" in ultima:
        st.error(f"DuckDB devolvió un error:\n\n{ultima['error']}")
        return
    df = ultima["df"]
    if df is None:
        st.success("Sentencia ejecutada (no devuelve filas).")
        return

    st.caption(f"{len(df):,} filas")
    st.dataframe(df, hide_index=True, use_container_width=True)

    conceptos = detectar(ultima["sql"])
    if conceptos:
        st.markdown(
            "Conceptos que usa: " + chips_conceptos(conceptos), unsafe_allow_html=True
        )

    _formulario_guardar(ultima["sql"], df, conceptos, nombres, cuaderno)


def _formulario_guardar(sql, df, conceptos, nombres_tablas, cuaderno: Cuaderno) -> None:
    with st.expander("📓 Guardar en el cuaderno"):
        if sql != st.session_state.sql_consola:
            st.caption("⚠️ Editaste el SQL después de ejecutarlo: se guarda la versión ejecutada.")
        # st.form agrupa los campos: Streamlit no re-ejecuta el script mientras
        # escribís, solo cuando apretás "Guardar".
        with st.form("guardar_consulta", clear_on_submit=True):
            pregunta = st.text_input(
                "Pregunta de negocio que responde *",
                placeholder="¿Qué transportista tiene más entregas tarde?",
            )
            pasos = st.text_area(
                "Cómo se lee (un paso por línea)",
                placeholder="Une cada pedido con su transportista.\n"
                "Se queda con los entregados después de la fecha prometida.\n"
                "Cuenta por transportista y ordena de mayor a menor.",
            )
            ojo = st.text_input(
                "Ojo (opcional): la trampa o el detalle para recordar",
                placeholder="Las fechas son texto: sin TRY_CAST la comparación es alfabética.",
            )
            c1, c2 = st.columns(2)
            categoria = c1.selectbox(
                "Categoría de KPI", [SIN_CATEGORIA] + cuaderno.categorias()
            )
            nueva = c2.text_input("…o una categoría nueva (tiene prioridad)")
            etiquetas = st.text_input("Etiquetas (separadas por coma)", placeholder="logística, mensual")
            notas = st.text_area("Mis notas")

            if st.form_submit_button("Guardar", type="primary"):
                if not pregunta.strip():
                    st.error("Escribí la pregunta: es lo que vas a buscar después.")
                    return
                categoria_final = nueva.strip() or ("" if categoria == SIN_CATEGORIA else categoria)
                id_ = cuaderno.guardar(
                    Entrada(
                        pregunta=pregunta,
                        sql=sql,
                        pasos=[p.strip() for p in pasos.splitlines() if p.strip()],
                        ojo=ojo,
                        notas=notas,
                        autor="manual",
                        categoria=categoria_final,
                        etiquetas=[t.strip() for t in etiquetas.split(",") if t.strip()],
                        conceptos=conceptos,
                        tablas=tablas_usadas(sql, nombres_tablas),
                        resultado=df,
                        filas_resultado=len(df),
                    )
                )
                st.success(f"Guardada como consulta #{id_}. La vas a encontrar en la pestaña Cuaderno.")


def tablas_usadas(sql: str, nombres_tablas: list[str]) -> list[str]:
    """Qué tablas cargadas aparecen mencionadas en el SQL."""
    return [t for t in nombres_tablas if re.search(rf"\b{re.escape(t)}\b", sql, re.IGNORECASE)]
