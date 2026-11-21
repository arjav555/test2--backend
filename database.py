import os
import mysql.connector.pooling
from decimal import Decimal

_pool = mysql.connector.pooling.MySQLConnectionPool(
    pool_name="facepulse",
    pool_size=5,
    pool_reset_session=True,
    host=os.environ["DB_HOST"],
    port=int(os.getenv("DB_PORT", 3306)),
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    database=os.environ["DB_NAME"],
    autocommit=False,
    connection_timeout=15
)

def _clean(rows):
    return [
        {
            k: float(v) if isinstance(v, Decimal) else v
            for k, v in row.items()
        }
        for row in rows
    ]

def q(sql, args=()):
    c = _pool.get_connection()
    try:
        cur = c.cursor(dictionary=True)
        cur.execute(sql, args)
        return _clean(cur.fetchall())
    finally:
        c.close()

def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None

def execute(sql, args=()):
    c = _pool.get_connection()
    try:
        cur = c.cursor()
        cur.execute(sql, args)
        result = (cur.lastrowid, cur.rowcount)
        c.commit()
        return result
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
