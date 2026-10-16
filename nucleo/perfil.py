"""Perfilado: entender qué es cada columna para poder razonar sobre ella.

Es la base de las sugerencias de KPIs (y, más adelante, de las validaciones
de calidad genéricas). A cada columna se le asigna un ROL:

    id         identificador (id, id_algo, algo_id)
    fecha      fecha real (DATE/TIMESTAMP) o texto que parece fecha
    numero     cantidad o monto
    escala     número entero de una escala corta (satisfacción 1-5, NPS 0-10)
    categoria  texto con pocos valores distintos (estado, ciudad, segmento)
    texto      texto libre o con muchos valores (nombres, descripciones)
    booleano   verdadero/falso
    vacia      sin ningún dato en la muestra (no sirve para KPIs)

El tipo SQL dice cómo se GUARDA el dato; el rol dice qué SIGNIFICA. Una fecha
guardada como texto (como en tu SQLite) tiene tipo VARCHAR pero rol "fecha".

Se trabaja sobre una muestra (las primeras 10.000 filas): para decidir roles
alcanza y funciona igual de rápido con tablas grandes.
"""

import re
from dataclasses import dataclass, field

import pandas as pd

NUMERICOS = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
    "UINTEGER", "UBIGINT", "FLOAT", "REAL", "DOUBLE",
}
MAX_CATEGORIAS = 20
NOMBRES_ETIQUETA = ("nombre", "descripcion", "razon_social", "titulo", "name")

PISTAS_ESCALA = r"satisf|puntaj|calific|nps|rating|nota|valoraci|score|escala|estrellas"

# (patrón de los textos, formato para pandas, SQL a DATE, SQL a TIMESTAMP o None si no tiene hora)
FORMATOS_FECHA = [
    (r"^\d{4}-\d{2}-\d{2}$", "%Y-%m-%d", "TRY_CAST({c} AS DATE)", None),
    (r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", "ISO8601",
     "CAST(TRY_CAST({c} AS TIMESTAMP) AS DATE)", "TRY_CAST({c} AS TIMESTAMP)"),
    (r"^\d{1,2}/\d{1,2}/\d{4}$", "%d/%m/%Y", "CAST(TRY_STRPTIME({c}, '%d/%m/%Y') AS DATE)", None),
]


@dataclass
class Columna:
    nombre: str
    tipo: str                       # tipo SQL en DuckDB
    rol: str
    distintos: int                  # valores distintos en la muestra
    expr_fecha: str | None = None   # cómo convertirla a DATE en SQL, con {c} = la columna
    expr_momento: str | None = None # cómo convertirla a TIMESTAMP (fecha + hora)
    con_hora: bool = False          # ¿la muestra tiene horas distintas de 00:00?
    valores: list[str] = field(default_factory=list)  # para categorías: de más a menos frecuente

    def fecha_sql(self, ref: str) -> str:
        """La expresión que convierte esta columna en DATE. ref = cómo nombrarla
        en el SQL (fecha_entrega o pedidos.fecha_entrega)."""
        return (self.expr_fecha or "{c}").format(c=ref)

    def momento_sql(self, ref: str) -> str:
        """Igual que fecha_sql pero conservando la hora, si la tiene. Para medir
        un SLA de 4 horas, convertir a DATE borraría justamente lo que importa."""
        if self.con_hora and self.expr_momento:
            return self.expr_momento.format(c=ref)
        return self.fecha_sql(ref)


@dataclass
class PerfilTabla:
    nombre: str
    filas: int
    columnas: list[Columna]
    clave: str | None               # columna id que identifica cada fila
    fechas: dict[str, pd.Series] = field(default_factory=dict, repr=False)  # muestra ya convertida

    def de_rol(self, rol: str) -> list[Columna]:
        return [c for c in self.columnas if c.rol == rol]

    def columna(self, nombre: str) -> Columna | None:
        return next((c for c in self.columnas if c.nombre == nombre), None)

    def etiqueta(self) -> str | None:
        """La columna que mejor 'nombra' cada fila (para mostrar en vez del id)."""
        for c in self.columnas:
            if c.nombre in NOMBRES_ETIQUETA:
                return c.nombre
        for c in self.columnas:
            if c.rol in ("texto", "categoria"):
                return c.nombre
        return None


@dataclass
class Relacion:
    tabla: str          # pedidos
    columna: str        # id_cliente
    destino: str        # clientes
    clave_destino: str  # id_cliente


# ================================================================ perfilado

def perfilar_tabla(
    nombre: str, tipos: list[tuple[str, str]], muestra: pd.DataFrame, filas: int
) -> PerfilTabla:
    columnas, fechas = [], {}
    for col, tipo in tipos:
        serie = muestra[col] if col in muestra.columns else pd.Series(dtype=object)
        no_nulos = serie.dropna()
        distintos = int(no_nulos.nunique())
        t = tipo.upper()
        c = Columna(col, tipo, "otro", distintos)

        if es_id(col):
            c.rol = "id"
        elif distintos == 0:
            c.rol = "vacia"
        elif t == "DATE" or t.startswith("TIMESTAMP"):
            c.rol = "fecha"
            c.expr_fecha = "{c}" if t == "DATE" else "CAST({c} AS DATE)"
            # TIMESTAMP_NS, TIMESTAMP WITH TIME ZONE...: los llevamos a TIMESTAMP común.
            c.expr_momento = "{c}" if t in ("DATE", "TIMESTAMP") else "CAST({c} AS TIMESTAMP)"
            fechas[col] = pd.to_datetime(serie, errors="coerce")
            c.con_hora = _tiene_hora(fechas[col])
        elif t == "BOOLEAN":
            c.rol = "booleano"
        elif t in NUMERICOS or t.startswith("DECIMAL"):
            if _es_escala(col, no_nulos):
                c.rol = "escala"
                c.valores = [str(int(v)) for v in sorted(pd.to_numeric(no_nulos).unique())]
            else:
                c.rol = "numero"
        elif t == "VARCHAR":
            expr, momento, convertida = _detectar_fecha_texto(serie, no_nulos)
            if expr:
                c.rol, c.expr_fecha, c.expr_momento = "fecha", expr, momento
                fechas[col] = convertida
                c.con_hora = momento is not None and _tiene_hora(convertida)
            elif 0 < distintos <= MAX_CATEGORIAS and distintos <= max(2, len(no_nulos) // 2):
                c.rol = "categoria"
                c.valores = no_nulos.astype(str).value_counts().index.tolist()
            else:
                c.rol = "texto"
        columnas.append(c)

    return PerfilTabla(nombre, filas, columnas, _clave(columnas, muestra), fechas)


def perfilar_almacen(almacen, n_muestra: int = 10_000) -> list[PerfilTabla]:
    """Perfila todas las tablas cargadas. Recibe el Almacen pero no importa
    DuckDB: solo usa sus métodos (tablas, columnas, contar, vista_previa)."""
    return [
        perfilar_tabla(t, almacen.columnas(t), almacen.vista_previa(t, n_muestra), almacen.contar(t))
        for t in almacen.tablas()
    ]


def detectar_relaciones(perfiles: list[PerfilTabla]) -> list[Relacion]:
    """pedidos.id_cliente -> clientes.id_cliente si en clientes esa columna es
    la clave. Es la convención de nombres más común; no es infalible, por eso
    la integridad se puede verificar con una de las sugerencias."""
    claves = {p.clave: p.nombre for p in perfiles if p.clave}
    relaciones = []
    for p in perfiles:
        for c in p.de_rol("id"):
            destino = claves.get(c.nombre)
            if destino and destino != p.nombre:
                relaciones.append(Relacion(p.nombre, c.nombre, destino, c.nombre))
    return relaciones


# ================================================================ ayudantes

def es_id(nombre: str) -> bool:
    return nombre == "id" or nombre.startswith("id_") or nombre.endswith("_id")


def _detectar_fecha_texto(serie: pd.Series, no_nulos: pd.Series):
    if no_nulos.empty:
        return None, None, None
    textos = no_nulos.astype(str).str.strip()
    for patron, formato, expr, momento in FORMATOS_FECHA:
        if textos.str.match(patron).mean() >= 0.9:
            convertida = pd.to_datetime(serie.astype(object), format=formato, errors="coerce")
            if convertida.notna().sum() >= 0.9 * len(no_nulos):
                return expr, momento, convertida
    return None, None, None


def _tiene_hora(fechas: pd.Series) -> bool:
    validas = fechas.dropna()
    return bool(len(validas)) and bool((validas != validas.dt.normalize()).any())


def _es_escala(nombre: str, no_nulos: pd.Series) -> bool:
    """Enteros entre 0 y 10 con pocos valores: una calificación, no una cantidad.
    Sumar una satisfacción de 1 a 5 no tiene sentido; promediarla y ver su
    distribución, sí. Para no confundir con cantidades chicas (ítems: 1, 2, 3),
    pedimos una pista en el nombre o que el rango sea típico de escala."""
    if no_nulos.empty:
        return False
    valores = pd.to_numeric(no_nulos, errors="coerce").dropna()
    if valores.empty or (valores % 1 != 0).any():
        return False
    minimo, maximo = valores.min(), valores.max()
    if minimo < 0 or maximo > 10 or valores.nunique() > 11:
        return False
    if re.search(PISTAS_ESCALA, nombre):
        return True
    parece_cantidad = re.search(r"cant|num|nro|qty|item|unidad|stock|edad|dias|horas", nombre)
    rango_tipico = minimo in (0, 1) and maximo in (5, 10) and valores.nunique() >= 3
    return rango_tipico and not parece_cantidad


def _clave(columnas: list[Columna], muestra: pd.DataFrame) -> str | None:
    """La primera columna id sin vacíos ni repetidos."""
    for c in columnas:
        if c.rol == "id" and c.nombre in muestra.columns and len(muestra):
            serie = muestra[c.nombre]
            if serie.notna().all() and serie.is_unique:
                return c.nombre
    return None


def es_plazo(nombre: str) -> bool:
    """¿El nombre sugiere una fecha comprometida (prometida, límite, vencimiento)?"""
    return bool(re.search(r"prometid|limite|plazo|compromet|vencim|estimad|esperad|programad|pactad", nombre))
