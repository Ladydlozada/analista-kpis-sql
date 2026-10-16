"""Pestaña Sugerencias: KPIs propuestos por reglas a partir del perfil de los datos."""

import hashlib

import duckdb
import pandas as pd
import streamlit as st

from nucleo.almacen import Almacen
from nucleo.conceptos import detectar
from nucleo.cuaderno import Cuaderno, Entrada
from nucleo.exportar import chips_conceptos, ojo_html, pasos_html
from nucleo.perfil import detectar_relaciones, perfilar_almacen
from nucleo.sugerencias import Sugerencia, sugerir
from vistas.consola import tablas_usadas

TODAS = "Todas"
EXPLICACION_ROLES = {
    "id": "identificador",
    "fecha": "fecha",
    "numero": "número (métrica)",
    "categoria": "categoría (pocos valores)",
    "texto": "texto libre",
    "booleano": "verdadero/falso",
    "otro": "otro",
}


def mostrar(almacen: Almacen, cuaderno: Cuaderno) -> None:
    if not almacen.tablas():
        st.info("Primero cargá datos desde la barra lateral.")
        return

    perfiles = perfilar_almacen(almacen)
    sugerencias = sugerir(perfiles)

    st.caption(
        "Estas sugerencias salen de **reglas** sobre el tipo de cada columna, no de un modelo "
        "de lenguaje: cada una dice qué regla la disparó. El criterio de negocio lo ponés vos."
    )

    with st.expander("🔎 Cómo leyó la app tus datos"):
        for p in perfiles:
            st.markdown(f"**{p.nombre}** · {p.filas:,} filas · clave: `{p.clave or '—'}`")
            st.dataframe(
                pd.DataFrame(
                    {
                        "columna": c.nombre,
                        "tipo SQL": c.tipo,
                        "rol": EXPLICACION_ROLES.get(c.rol, c.rol),
                        "valores distintos (muestra)": c.distintos,
                        "conversión a fecha": c.fecha_sql(c.nombre) if c.rol == "fecha" else "",
                    }
                    for c in p.columnas
                ),
                hide_index=True,
                use_container_width=True,
            )
        relaciones = detectar_relaciones(perfiles)
        if relaciones:
            st.markdown("**Relaciones detectadas** (por el nombre de la columna clave):")
            for r in relaciones:
                st.markdown(f"- `{r.tabla}.{r.columna}` → `{r.destino}.{r.clave_destino}`")

    if not sugerencias:
        st.info("No se encontraron patrones para sugerir KPIs en estas tablas.")
        return

    c1, c2 = st.columns(2)
    tabla = c1.selectbox("Tabla", [TODAS] + sorted({s.tabla for s in sugerencias}))
    categorias = list(dict.fromkeys(s.categoria for s in sugerencias))  # sin repetir, en orden
    categoria = c2.selectbox("Tipo de KPI", [TODAS] + categorias)

    visibles = [
        s for s in sugerencias
        if (tabla == TODAS or s.tabla == tabla) and (categoria == TODAS or s.categoria == categoria)
    ]
    st.caption(f"{len(visibles)} sugerencias")

    for cat in dict.fromkeys(s.categoria for s in visibles):
        st.subheader(cat)
        for s in visibles:
            if s.categoria == cat:
                _sugerencia(s, almacen, cuaderno)


def _clave(s: Sugerencia) -> str:
    """Un identificador estable para los controles de esta sugerencia."""
    return hashlib.md5(s.sql.encode()).hexdigest()[:10]


def _abrir_en_consola(sql: str) -> None:
    st.session_state.sql_consola = sql


def _sugerencia(s: Sugerencia, almacen: Almacen, cuaderno: Cuaderno) -> None:
    k = _clave(s)
    with st.expander(s.pregunta):
        st.markdown(f"**Por qué:** {s.por_que}")
        st.markdown(chips_conceptos(detectar(s.sql)), unsafe_allow_html=True)
        st.code(s.sql, language="sql")
        st.caption("Cómo se lee")
        st.markdown(pasos_html(s.pasos), unsafe_allow_html=True)
        if s.ojo:
            st.markdown(ojo_html(s.ojo), unsafe_allow_html=True)

        b1, b2, b3 = st.columns(3)
        if b1.button("▶ Probar", key=f"probar_{k}", use_container_width=True):
            _ejecutar(s, almacen, k)
        if b2.button("📓 Guardar en el cuaderno", key=f"guardar_{k}", use_container_width=True):
            if f"res_{k}" not in st.session_state:
                _ejecutar(s, almacen, k)
            r = st.session_state[f"res_{k}"]
            if "error" not in r:
                id_ = cuaderno.guardar(
                    Entrada(
                        pregunta=s.pregunta,
                        sql=s.sql,
                        pasos=s.pasos,
                        ojo=s.ojo,
                        autor="sugerencia",
                        categoria=s.categoria,
                        conceptos=detectar(s.sql),
                        tablas=tablas_usadas(s.sql, almacen.tablas()),
                        resultado=r["df"],
                        filas_resultado=len(r["df"]),
                    )
                )
                st.session_state[f"guardada_{k}"] = id_
        b3.button(
            "↗ Abrir en la consola", key=f"abrir_{k}", use_container_width=True,
            on_click=_abrir_en_consola, args=(s.sql,),
            help="Para modificarla antes de guardarla (por ejemplo, filtrar un estado).",
        )

        if f"guardada_{k}" in st.session_state:
            st.success(f"Guardada en el cuaderno como consulta #{st.session_state[f'guardada_{k}']}.")
        r = st.session_state.get(f"res_{k}")
        if r and "error" in r:
            st.error(r["error"])
        elif r:
            st.caption(f"Resultado · {len(r['df']):,} filas")
            st.dataframe(r["df"], hide_index=True, use_container_width=True)


def _ejecutar(s: Sugerencia, almacen: Almacen, k: str) -> None:
    try:
        st.session_state[f"res_{k}"] = {"df": almacen.consultar(s.sql)}
    except duckdb.Error as e:
        st.session_state[f"res_{k}"] = {"error": f"DuckDB devolvió un error:\n\n{e}"}
