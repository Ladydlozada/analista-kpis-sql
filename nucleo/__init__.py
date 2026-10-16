"""Lógica de la app, separada de la interfaz (Streamlit).

- nombres.py     → normaliza nombres de tablas y columnas para SQL.
- cargadores.py  → lee CSV / Excel / SQLite y devuelve DataFrames (sin DuckDB).
- almacen.py     → guarda esos DataFrames como tablas DuckDB y las describe.
"""
