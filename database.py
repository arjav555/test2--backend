import os
from decimal import Decimal
from urllib.parse import urlparse, unquote

import mysql.connector
from mysql.connector import pooling


def get_db_config():
    """
    Supports both custom DB_* variables
    and Railway MYSQL_* variables.
    """

    database_url = os.getenv("MYSQL_URL") or os.getenv("DATABASE_URL")

    if database_url:
        parsed = urlparse(database_url)

        return {
            "host": parsed.hostname,
            "port": parsed.port or 3306,
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "database": parsed.path.lstrip("/"),
        }

    return {
        "host": os.getenv("DB_HOST") or os.getenv("MYSQLHOST"),
        "port": int(os.getenv("DB_PORT") or os.getenv("MYSQLPORT", 3306)),
        "user": os.getenv("DB_USER") or os.getenv("MYSQLUSER"),
        "password": os.getenv("DB_PASSWORD") or os.getenv("MYSQLPASSWORD"),
        "database": os.getenv("DB_NAME") or os.getenv("MYSQLDATABASE"),
    }


config = get_db_config()

missing = [key for key, value in config.items() if not value]

if missing:
    raise RuntimeError(
        "Missing database configuration: " + ", ".join(missing)
    )


_pool = pooling.MySQLConnectionPool(
    pool_name="facepulse",
    pool_size=5,
    pool_reset_session=True,
    host=config["host"],
    port=config["port"],
    user=config["user"],
    password=config["password"],
    database=config["database"],
    autocommit=False,
    connection_timeout=15,
    charset="utf8mb4",
)


def _clean(rows):
    return [
        {
            key: float(value) if isinstance(value, Decimal) else value
            for key, value in row.items()
        }
        for row in rows
    ]


def q(sql, args=()):
    connection = _pool.get_connection()

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, args)

        return _clean(cursor.fetchall())

    finally:
        connection.close()


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def execute(sql, args=()):
    connection = _pool.get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(sql, args)

        result = (cursor.lastrowid, cursor.rowcount)

        connection.commit()

        return result

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
