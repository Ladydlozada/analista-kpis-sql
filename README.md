# Analista de KPIs

App en Python (Streamlit + DuckDB) para cargar datos de Excel, CSV o SQLite,
explorarlos y consultarlos con SQL.

## Cómo correrla

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                        # pruebas
streamlit run app.py
```

## Estructura

```
app.py              Arma la barra lateral y las pestañas; delega en vistas/.
vistas/
  tablas.py         Pestaña Tablas: resumen, esquema, procedencia, vista previa.
  sugerencias.py    Pestaña Sugerencias de KPIs: probar y guardar en el cuaderno.
  consola.py        Pestaña Consola SQL: ejecutar y guardar en el cuaderno.
  cuaderno.py       Pestaña Cuaderno: buscar, filtrar, re-ejecutar, exportar.
nucleo/             Lógica sin interfaz (se prueba con pytest).
  nombres.py        Normaliza nombres de tablas/columnas para SQL.
  cargadores.py     CSV / Excel / SQLite → DataFrames. No conoce DuckDB ni Streamlit.
  almacen.py        Guarda los DataFrames como tablas DuckDB y describe su esquema.
  perfil.py         Rol de cada columna (id, fecha, número, categoría) y relaciones.
  sugerencias.py    Reglas que proponen KPIs a partir del perfil.
  conceptos.py      Detecta los conceptos SQL de una consulta (JOIN, RANK…).
  cuaderno.py       Guarda las consultas en datos/cuaderno.sqlite.
  exportar.py       Exporta el cuaderno a Markdown y HTML.
tests/              Pruebas con pytest.
```

Las vistas usan el núcleo; el núcleo nunca importa Streamlit.

## Sugerencias de KPIs

La app perfila cada columna (tipo SQL + rol) y detecta relaciones por el nombre
de las claves (`pedidos.id_cliente` → `clientes.id_cliente`). Con eso, reglas
genéricas proponen KPIs con SQL, explicación y la regla que los disparó:
volumen por mes, distribución de categorías, tasas de valores "negativos"
(cancelado, rechazado…; "baja" solo en columnas de estado), métricas numéricas,
escalas (satisfacción 1-5: promedio y % de 4 o 5, nunca suma), tiempos de ciclo
entre fechas (en horas si las fechas tienen hora), cumplimiento de plazos
(fechas "prometida", "límite"…, comparando fecha y hora), rankings e
integridad referencial. Las reglas no usan nombres fijos de tablas: sirven para cualquier
archivo.

## Cuaderno de consultas

Cada consulta guarda: pregunta de negocio, SQL, pasos de "cómo se lee", un
"ojo" con la trampa a recordar, categoría de KPI, etiquetas, notas, autor
(`manual` o `claude`), conceptos SQL detectados, tablas usadas y una foto del
resultado (hasta 200 filas). Se puede buscar sin importar las tildes, filtrar
por categoría, concepto o autor, re-ejecutar y comparar con el resultado
guardado, y exportar a Markdown o HTML.

## Qué hace la carga

| Origen  | Resultado                                                              |
|---------|------------------------------------------------------------------------|
| CSV     | Una tabla. Detecta el separador (`,` `;` tab `|`) y la codificación (UTF-8 / Windows-1252). |
| Excel   | Una tabla por hoja (`archivo_hoja`; si hay una sola hoja, `archivo`). Detecta el encabezado aunque haya títulos arriba. |
| SQLite  | Una tabla por cada tabla de la base, con el mismo nombre.              |

- Los nombres se pasan a `snake_case` sin tildes; la app muestra qué se renombró.
- El archivo original nunca se modifica: DuckDB trabaja sobre una copia en memoria.
- Avisos de calidad desde la carga: columnas totalmente vacías y columnas que mezclan tipos.

## Limitaciones conocidas

- Las fechas guardadas como texto (como en SQLite) quedan como `VARCHAR`: se convierten en SQL con `CAST(col AS DATE)` o `TRY_CAST`.
- En un CSV con `;` y coma decimal (`1.234,56`), esa columna queda como texto.
- Todo vive en memoria: al cerrar la sesión se pierde.
