"""Crea una base SQLite chica con la misma forma que operaciones.db, para las pruebas."""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path


def crear_base(ruta: Path, n_pedidos: int = 120) -> bytes:
    azar = random.Random(42)  # semilla fija: siempre los mismos datos
    con = sqlite3.connect(ruta)
    con.executescript(
        """
        CREATE TABLE depositos (id_deposito INTEGER PRIMARY KEY, nombre TEXT, ciudad TEXT);
        CREATE TABLE transportistas (id_transportista INTEGER PRIMARY KEY, nombre TEXT);
        CREATE TABLE clientes (id_cliente INTEGER PRIMARY KEY, nombre TEXT, ciudad TEXT, segmento TEXT);
        CREATE TABLE pedidos (id_pedido INTEGER PRIMARY KEY, id_cliente INTEGER, id_deposito INTEGER,
            id_transportista INTEGER, fecha_creacion TEXT, fecha_despacho TEXT,
            fecha_prometida TEXT, fecha_entrega TEXT, estado TEXT, monto REAL);
        """
    )
    con.executemany("INSERT INTO depositos VALUES (?,?,?)",
                    [(1, "Centro", "Montevideo"), (2, "Norte", "Salto"), (3, "Este", "Maldonado")])
    con.executemany("INSERT INTO transportistas VALUES (?,?)",
                    [(1, "Rápido"), (2, "Express"), (3, "Lento")])
    con.executemany("INSERT INTO clientes VALUES (?,?,?,?)",
                    [(i, f"Cliente {i}", azar.choice(["Montevideo", "Salto"]),
                      azar.choice(["Minorista", "Mayorista"])) for i in range(1, 21)])
    filas = []
    for i in range(1, n_pedidos + 1):
        creada = date(2026, 1, 1) + timedelta(days=azar.randint(0, 120))
        estado = azar.choices(["entregado", "cancelado", "en_transito"], [85, 10, 5])[0]
        despacho = creada + timedelta(days=azar.randint(0, 3)) if estado != "cancelado" else None
        prometida = creada + timedelta(days=4)
        entrega = creada + timedelta(days=azar.randint(2, 7)) if estado == "entregado" else None
        filas.append((
            i, azar.randint(1, 21),  # el 21 no existe: algunas filas huérfanas
            azar.randint(1, 3), azar.randint(1, 3), creada.isoformat(),
            despacho and despacho.isoformat(), prometida.isoformat(),
            entrega and entrega.isoformat(), estado, round(azar.uniform(500, 9000), 2),
        ))
    con.executemany("INSERT INTO pedidos VALUES (?,?,?,?,?,?,?,?,?,?)", filas)
    con.commit()
    con.close()
    return Path(ruta).read_bytes()
