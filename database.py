import os
from decimal import Decimal
import mysql.connector.pooling as pool

_pool = pool.MySQLConnectionPool(
    pool_name="fp", pool_size=8,
    host=os.getenv("DB_HOST", "localhost"), user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""), database=os.getenv("DB_NAME", "facepulse"))

def _clean(rows):
    return [{k: float(v) if isinstance(v, Decimal) else v for k, v in r.items()} for r in rows]

def q(sql, args=()):
    """SELECT -> list of dicts."""
    c = _pool.get_connection()
    try:
        cur = c.cursor(dictionary=True); cur.execute(sql, args)
        return _clean(cur.fetchall())
    finally:
        c.close()

def one(sql, args=()):
    r = q(sql, args); return r[0] if r else None

def execute(sql, args=()):
    """INSERT/UPDATE/DELETE -> (lastrowid, rowcount)."""
    c = _pool.get_connection()
    try:
        cur = c.cursor(); cur.execute(sql, args); c.commit()
        return cur.lastrowid, cur.rowcount
    finally:
        c.close()
