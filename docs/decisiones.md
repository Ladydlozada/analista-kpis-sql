# Decisiones de diseño

Registro de las decisiones técnicas del proyecto, en el orden en que se tomaron, con el porqué de cada una y los errores que aparecieron al probar.

## 1 · Reorganización y carga genérica

**Estructura:** `app.py` (interfaz) → `nucleo/almacen.py` (DuckDB) → `nucleo/cargadores.py` (lectura) → `nucleo/nombres.py`. La dependencia va en una sola dirección.

- **`cargadores.py` no importa DuckDB ni Streamlit.** Recibe `(nombre_archivo, bytes)` y devuelve `TablaCargada`. Se puede probar con pytest y es reutilizable.
- **El original no se toca.** Se trabaja sobre los bytes; SQLite se abre desde una copia temporal en modo solo lectura (`mode=ro`).
- **Primero leer crudo, detectar y después convertir tipos.** Los lectores no convierten tipos. `_preparar()` detecta las columnas con tipos mezclados, las pasa a texto y recién después aplica `convert_dtypes()` a las columnas que no son texto, para que un entero con vacíos siga como `Int64` en vez de float (119 → 119.0).
- **Nombres en snake_case sin tildes** (`Fecha de Entrega` → `fecha_de_entrega`), sin repetidos y con `_` al final si son palabras reservadas. Se guarda el mapeo y se muestra.
- **Columnas con tipos mezclados → texto + aviso.** No se pierden datos y queda marcado el problema de calidad. También aplica a SQLite, que tiene tipado dinámico.
- **Copia en tablas DuckDB** (`CREATE OR REPLACE TABLE ... AS SELECT * FROM df`), no vistas sobre el DataFrame.
- **Conexión en `st.session_state`, no en `st.cache_resource`.** El caché es compartido entre usuarios; `session_state` es por usuario. Se verifica con `isinstance`, porque si el código cambia con la app abierta, el objeto guardado sigue siendo de la clase vieja.
- **Botón explícito "Cargar".** Streamlit vuelve a correr el script en cada interacción; sin botón, el archivo se reprocesaría cada vez.
- **Esquema en una sola consulta.** Nulos = `COUNT(*) - COUNT(col)`; blancos = `COUNT(*) FILTER (WHERE TRIM(col) = '')`, solo en VARCHAR. El SQL generado se muestra.
- **Nombres de tablas y columnas con `citar()`, valores con parámetros `?`.**
- **Consulta o comando.** DuckDB responde a todo con una tabla (un `CREATE` o `INSERT` devuelve una columna `Count`). Para distinguirlos se usa `con.extract_statements(sql)` y se mira el `StatementType` de la última sentencia.
- **Encabezado desplazado en Excel.** Se miran las primeras 20 filas sin encabezado. El ancho de la tabla es la cantidad de celdas de la fila más llena; el encabezado es la primera fila que llega al 80 % de ese ancho y es mayormente texto. Se informa en la procedencia.

**Lecciones de las primeras pruebas**
- Convertir tipos al leer (`dtype_backend`) puede borrar evidencia de mala calidad: en algunos entornos, `1, "A-2", 3` llegaba ya como texto, sin aviso.
- En pandas 3, `convert_dtypes(convert_string=False)` igual reconvierte las columnas de texto; por eso se aplica solo a las que no son texto.
- Un aviso de "tipos mezclados" en columnas `unnamed_*` era el síntoma de un encabezado desplazado: el síntoma visible suele estar lejos de la causa.

## 2 · Cuaderno de consultas

- **Cuaderno en SQLite (`datos/cuaderno.sqlite`), datos en DuckDB.** SQLite es transaccional (OLTP: guardar, editar y borrar registros sueltos); DuckDB es analítico (OLAP: recorrer y agregar muchas filas).
- **Una conexión SQLite por operación.** Streamlit puede usar hilos distintos y una conexión sqlite3 solo sirve en el hilo que la creó. Además, `with sqlite3.connect()` hace commit pero no cierra la conexión.
- **Listas como JSON en una columna** (pasos, etiquetas, conceptos), consultables con `json_each()`. Es más simple que una tabla de etiquetas muchos a muchos.
- **Búsqueda sin tildes dentro del SQL**, registrando una función Python en SQLite con `con.create_function("sin_tildes", ...)`.
- **Filtros opcionales en una sola consulta** con el patrón `(:x IS NULL OR col = :x)`.
- **El resultado se guarda como foto** (hasta 200 filas, en JSON). Al volver a ejecutar, el resultado nuevo pasa por el mismo camino JSON de ida y vuelta antes de compararse.
- **Conceptos detectados con expresiones regulares**, después de quitar comentarios y textos. Hay seis familias con color: unir, filtrar, agrupar, funciones, ventanas y estructura. Una CTE no cuenta como subconsulta.
- **Cada consulta tiene autor** (`manual`, `claude` o `sugerencia`), pensando en el modo estudio y en la integración con la API.
- **Exportación a Markdown y a HTML** con los mismos colores; el resaltado de SQL va token por token.
- **Detalles de Streamlit.** La última ejecución vive en `session_state`; el formulario de guardado usa `st.form` para no re-ejecutar mientras se escribe; "Abrir en la consola" y "Guardar notas" usan callbacks (`on_click`), porque el valor de un control solo se puede cambiar antes de dibujarlo.

## 3 · Sugerencias de KPIs por reglas

- **El tipo SQL dice cómo se guarda un dato; el rol dice qué significa.** Roles: id, fecha, número, escala, categoría (hasta 20 valores distintos), texto, booleano y vacía. Una fecha guardada como texto se detecta por patrón (ISO, ISO con hora, dd/mm/aaaa) y se le asigna la expresión SQL que la convierte.
- **El perfil usa una muestra de 10.000 filas.** Alcanza para decidir roles y es rápido con tablas grandes.
- **Relaciones por convención de nombres:** `pedidos.id_cliente` → `clientes.id_cliente` cuando esa columna es la clave de `clientes` (sin vacíos ni repetidos). La regla de integridad verifica que se cumpla.
- **Dimensiones** a partir de las relaciones (etiqueta de la tabla destino y sus categorías) y de las categorías propias. Para desglosar se usan las de pocos valores (comparación legible); para rankings, las de muchos (top 10).
- **Reglas:** volumen por mes (tablas de 30 filas o más), distribución (`SUM(COUNT(*)) OVER ()`), tasa de un valor "negativo" (`AVG(CASE WHEN ...)`), métricas numéricas (total, promedio, mediana), escalas (distribución, promedio y top-2-box), tiempos de ciclo (pares de fechas donde una es posterior en el 95 % o más de la muestra), cumplimiento de plazo (una fecha cuyo nombre sugiere un plazo y que la fecha real supera entre el 1 % y el 99 % de las veces), ranking con `RANK()` e integridad con anti-join.
- **Transparencia:** cada sugerencia trae la regla que la disparó, los pasos para leerla y un "ojo". Las reglas no entienden el negocio: ese criterio lo pone quien analiza.
- **Migración del cuaderno.** SQLite no permite cambiar un `CHECK`: se renombra la tabla, se crea la nueva, se copian los datos y se borra la vieja, todo en una transacción.
- **Pruebas:** `test_sugerencias_duckdb.py` ejecuta cada SQL sugerido en DuckDB sobre una base generada con semilla fija (`tests/datos_ejemplo.py`).

## 4 · Prueba con otro rubro: `soporte.xlsx`

Una mesa de ayuda ficticia (640 tickets, 8 agentes, 25 clientes), más una hoja con título arriba y otra vacía. La prueba destapó cuatro problemas, todos corregidos:

1. **Falso positivo "tasa de Baja"** (era prioridad baja, no algo malo). Ahora hay valores negativos *fuertes* (cancelado, anulado, rechazado, devuelto…), que valen en cualquier columna, y *débiles* (baja, inactivo, vencido…), que solo valen en columnas de tipo estado.
2. **Fechas con hora convertidas a día.** El SLA daba 10,4 % cuando el valor real es 27,1 %, y la primera respuesta daba "0 días" cuando la mediana es de 30 minutos. Ahora, si las dos columnas tienen hora, se comparan momentos exactos y los tiempos se miden en horas (`DATE_DIFF('minute', a, b) / 60.0`). La muestra se compara con la misma precisión que el SQL.
3. **Satisfacción de 1 a 5 sumada.** Nuevo rol `escala`: enteros de 0 a 10 con una pista en el nombre, o con un rango típico (0/1 a 5/10) y un nombre que no parezca cantidad. Se reporta con promedio y top-2-box.
4. **Encabezado desplazado:** ahora se detecta (ver sección 1).

Además: rol `vacia` para columnas sin datos, y nada de "volumen por mes" en tablas chicas.
