"""
database.py - Database Connection and Initialization Module
=============================================================
This file manages the connection between Flask and the database.
Supports:
1. Local MySQL (default: localhost:3306, user: root)
2. Cloud MySQL (via environment variables: MYSQL_URL, DATABASE_URL, TIDB_URL,
   JAWSDB_URL, CLEARDB_DATABASE_URL, or MYSQL_HOST, TIDB_HOST, etc.)
   with automatic SSL support for TiDB Cloud, Aiven, AWS RDS, PlanetScale.
3. SQLite (fallback when deployed on Render without a cloud database,
   or enabled manually via USE_SQLITE=true, with persistent disk support).
"""

import os
import re
import sqlite3
from urllib.parse import urlparse, unquote

# -----------------------------------------------------------------------------
# Configuration Settings (Local vs Cloud Environment)
# -----------------------------------------------------------------------------
# Check if deployed on Render or requested SQLite explicitly
IS_RENDER = os.environ.get('RENDER') is not None
ENV_USE_SQLITE = os.environ.get('USE_SQLITE', '').strip().lower() in ('1', 'true', 'yes')

# Check for database URL from various cloud providers (TiDB, Aiven, Railway, Render, Heroku)
DATABASE_URL = (
    os.environ.get('MYSQL_URL')
    or os.environ.get('DATABASE_URL')
    or os.environ.get('TIDB_URL')
    or os.environ.get('JAWSDB_URL')
    or os.environ.get('CLEARDB_DATABASE_URL')
)

if DATABASE_URL and (DATABASE_URL.startswith('mysql://') or DATABASE_URL.startswith('mysql2://')):
    # Normalize mysql2:// to mysql:// for standard urlparse
    norm_url = DATABASE_URL.replace('mysql2://', 'mysql://', 1)
    parsed = urlparse(norm_url)
    DB_HOST = parsed.hostname or 'localhost'
    DB_USER = unquote(parsed.username) if parsed.username else 'root'
    DB_PASSWORD = unquote(parsed.password) if parsed.password else ''
    DB_PORT = parsed.port or 3306
    # Strip any query parameters (e.g. ?ssl-mode=REQUIRED) from db name
    raw_path = parsed.path.lstrip('/')
    DB_NAME = raw_path.split('?')[0] if raw_path else 'personal_expense_tracker'
else:
    # Check individual environment variables (supports standard MySQL, TiDB Cloud, and Railway)
    DB_HOST = (
        os.environ.get('MYSQL_HOST')
        or os.environ.get('TIDB_HOST')
        or os.environ.get('MYSQLHOST')
        or 'localhost'
    )
    DB_USER = (
        os.environ.get('MYSQL_USER')
        or os.environ.get('TIDB_USER')
        or os.environ.get('MYSQLUSER')
        or 'root'
    )
    DB_PASSWORD = (
        os.environ.get('MYSQL_PASSWORD')
        or os.environ.get('TIDB_PASSWORD')
        or os.environ.get('MYSQLPASSWORD')
        or 'Pandu*33'
    )
    DB_PORT = int(
        os.environ.get('MYSQL_PORT')
        or os.environ.get('TIDB_PORT')
        or os.environ.get('MYSQLPORT')
        or 3306
    )
    DB_NAME = (
        os.environ.get('MYSQL_DB')
        or os.environ.get('MYSQL_DATABASE')
        or os.environ.get('TIDB_DATABASE')
        or os.environ.get('MYSQLDATABASE')
        or 'personal_expense_tracker'
    )

is_remote_mysql = DB_HOST not in ('localhost', '127.0.0.1')

# Determine active engine:
# If a remote MySQL is configured (e.g., TiDB Cloud, Aiven), ALWAYS use 'mysql'
# so registration data is permanently preserved across container restarts/days.
# If on Render without a remote MySQL host, fallback to SQLite.
if ENV_USE_SQLITE or (IS_RENDER and not is_remote_mysql):
    ACTIVE_ENGINE = 'sqlite'
else:
    ACTIVE_ENGINE = 'mysql'

# Support persistent volume path if mounted on cloud hosting (e.g., Render/Railway persistent disks)
SQLITE_DB_PATH = (
    os.environ.get('SQLITE_PATH')
    or (os.path.join('/var/data', 'expense_tracker.db') if os.path.exists('/var/data') else None)
    or (os.path.join('/data', 'expense_tracker.db') if os.path.exists('/data') else None)
    or os.path.join(os.path.dirname(__file__), 'expense_tracker.db')
)

# Flag to avoid duplicate table checks
_tables_initialized = False


# -----------------------------------------------------------------------------
# SQLite Compatibility Layer (Wraps SQLite to provide MySQL-compatible syntax)
# -----------------------------------------------------------------------------
class SQLiteCursorWrapper:
    """Wraps an SQLite cursor to emulate mysql.connector dictionary cursor."""
    def __init__(self, cursor):
        self.cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid

    def execute(self, query, params=None):
        q = query
        # Fix escaped %% in Jinja/SQL
        q = q.replace('%%Y-%%m', '%Y-%m')
        # Replace MySQL DATE_FORMAT with SQLite strftime
        q = re.sub(r"DATE_FORMAT\(([^,]+),\s*'%Y-%m'\)", r"strftime('%Y-%m', \1)", q)
        # Replace %s with ? for parameterized queries
        q = q.replace('%s', '?')

        # Clean SQLite UNION ALL syntax (remove leading/trailing parentheses around SELECTs)
        if 'UNION ALL' in q.upper():
            q = re.sub(r'^\s*\(\s*SELECT\b', 'SELECT', q, flags=re.IGNORECASE)
            q = re.sub(r'\)\s*UNION\s+ALL\s*\(', ' UNION ALL ', q, flags=re.IGNORECASE)
            q = re.sub(r'\)\s*(ORDER\s+BY\b)', r' \1', q, flags=re.IGNORECASE)

        if params:
            self.cursor.execute(q, params)
        else:
            self.cursor.execute(q)

        self.rowcount = self.cursor.rowcount
        self.lastrowid = self.cursor.lastrowid
        return self

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        rows = self.cursor.fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.cursor.close()


class SQLiteConnectionWrapper:
    """Wraps an SQLite connection to emulate mysql.connector connection."""
    def __init__(self, conn):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        # Enable WAL mode and synchronous=NORMAL for safe and durable disk writes
        try:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass

    def cursor(self, dictionary=True):
        return SQLiteCursorWrapper(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


# -----------------------------------------------------------------------------
# Database Connection Functions
# -----------------------------------------------------------------------------
def _create_raw_connection():
    """Internal helper to create a physical connection without triggering auto-init."""
    if ACTIVE_ENGINE == 'sqlite':
        conn = sqlite3.connect(SQLITE_DB_PATH, timeout=30.0)
        return SQLiteConnectionWrapper(conn)

    import mysql.connector

    config = {
        'host': DB_HOST,
        'user': DB_USER,
        'password': DB_PASSWORD,
        'port': DB_PORT,
        'database': DB_NAME,
        'use_pure': True,
        'connection_timeout': 15,
        'buffered': True
    }

    if is_remote_mysql:
        # Enable TLS/SSL for cloud databases (TiDB Cloud, Aiven, PlanetScale, AWS RDS)
        config['ssl_disabled'] = False
        config['ssl_verify_cert'] = False
        config['ssl_verify_identity'] = False

    return mysql.connector.connect(**config)


def get_db_connection():
    """
    Establish and return a connection to the active database.
    In 'mysql' mode, returns a MySQL connection.
    In 'sqlite' mode, returns a wrapped SQLite connection.
    """
    global _tables_initialized

    # Auto-initialize tables on first connection if not yet done
    if not _tables_initialized:
        _tables_initialized = True
        try:
            init_db()
        except Exception as e:
            print(f"[WARNING] Table initialization on connect: {e}")

    return _create_raw_connection()


def init_db():
    """
    Initializes the database and creates tables ('users', 'income', 'expenses')
    if they do not already exist.
    """
    global _tables_initialized
    _tables_initialized = True

    if ACTIVE_ENGINE == 'sqlite':
        print(f"[INFO] Using SQLite database at: {SQLITE_DB_PATH}")
        if IS_RENDER:
            print("[WARNING] Running on Render with ephemeral local SQLite storage.")
            print("[WARNING] Render free tier restarts wipe local files overnight.")
            print("[WARNING] To keep user registrations permanently, set MYSQL_URL in Render Environment Variables.")

        conn = sqlite3.connect(SQLITE_DB_PATH)
        cursor = conn.cursor()

        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS income (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            description TEXT NOT NULL,
            date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)

        conn.commit()
        cursor.close()
        conn.close()
        print("[SUCCESS] SQLite database & tables ('users', 'income', 'expenses') ready!")
        return

    # MySQL initialization
    import mysql.connector

    # Only run CREATE DATABASE on localhost (local developer machine where user has root privileges)
    # Cloud MySQL providers (TiDB Cloud, Aiven, Clever Cloud) already provision the database
    # and will throw permission error 1044 if CREATE DATABASE is executed!
    if not is_remote_mysql:
        server_config = {
            'host': DB_HOST,
            'user': DB_USER,
            'password': DB_PASSWORD,
            'port': DB_PORT,
            'use_pure': True
        }
        print(f"[INFO] Connecting to local MySQL server on {DB_HOST}:{DB_PORT}...")
        server_conn = mysql.connector.connect(**server_config)
        cursor = server_conn.cursor()

        print(f"[INFO] Ensuring database '{DB_NAME}' exists...")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` DEFAULT CHARACTER SET utf8mb4;")
        cursor.close()
        server_conn.close()

    print(f"[INFO] Connecting to MySQL database '{DB_NAME}' on {DB_HOST}:{DB_PORT}...")
    conn = _create_raw_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(50) NOT NULL UNIQUE,
        email VARCHAR(100) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS income (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        amount DECIMAL(10, 2) NOT NULL,
        description VARCHAR(255) NOT NULL,
        date DATE NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        amount DECIMAL(10, 2) NOT NULL,
        description VARCHAR(255) NOT NULL,
        category VARCHAR(50) NOT NULL,
        date DATE NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print(f"[SUCCESS] MySQL database '{DB_NAME}' and tables ready for permanent storage!")


if __name__ == '__main__':
    print(f"--- Running Database Setup (Active Engine: {ACTIVE_ENGINE}, Host: {DB_HOST}) ---")
    init_db()
