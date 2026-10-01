import os
import sqlite3

# Database configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('DATABASE_PATH', os.path.join(BASE_DIR, 'utilityops.db'))


def get_db_connection():
    """Create and return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create database tables if they do not exist."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. USERS table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        );
    """)

    # 2. STORES table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_code TEXT UNIQUE NOT NULL,
            store_name TEXT NOT NULL,
            supervisor TEXT NOT NULL
        );
    """)

    # 3. UTILITY_BILLS table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS utility_bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL,
            utility_type TEXT NOT NULL,
            billing_period TEXT NOT NULL,
            bill_number TEXT,
            units REAL,
            unit_rate REAL,
            amount REAL,
            bill_date TEXT,
            file_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (store_id) REFERENCES stores(id)
        );
    """)

    # Migrate existing utility_bills table if 'units' still has NOT NULL constraint
    cursor.execute("PRAGMA table_info(utility_bills)")
    columns = {row['name']: row for row in cursor.fetchall()}
    if 'units' in columns and columns['units']['notnull'] == 1:
        cursor.execute("PRAGMA foreign_keys = OFF;")
        cursor.execute("ALTER TABLE utility_bills RENAME TO utility_bills_old;")
        cursor.execute("""
            CREATE TABLE utility_bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                utility_type TEXT NOT NULL,
                billing_period TEXT NOT NULL,
                bill_number TEXT,
                units REAL,
                unit_rate REAL,
                amount REAL,
                bill_date TEXT,
                file_path TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (store_id) REFERENCES stores(id)
            );
        """)
        cursor.execute("""
            INSERT INTO utility_bills (
                id, store_id, utility_type, billing_period, bill_number,
                units, unit_rate, amount, bill_date, file_path, status, created_at
            )
            SELECT id, store_id, utility_type, billing_period, bill_number,
                   units, unit_rate, amount, bill_date, file_path, status, created_at
            FROM utility_bills_old;
        """)
        cursor.execute("DROP TABLE utility_bills_old;")
        cursor.execute("PRAGMA foreign_keys = ON;")

    # 4. ALERTS table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER,
            severity TEXT NOT NULL,
            reason TEXT NOT NULL,
            recommended_action TEXT,
            is_reviewed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bill_id) REFERENCES utility_bills(id)
        );
    """)

    conn.commit()

    # Rebuilding utility_bills can cause SQLite to rewrite alerts.bill_id to
    # reference the temporary utility_bills_old table. Repair that reference
    # after the migration, preserving any alert rows already in the database.
    cursor.execute("PRAGMA foreign_key_list(alerts)")
    alert_foreign_keys = cursor.fetchall()
    if not any(row['table'] == 'utility_bills' for row in alert_foreign_keys):
        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("""
            CREATE TABLE alerts_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id INTEGER,
                severity TEXT NOT NULL,
                reason TEXT NOT NULL,
                recommended_action TEXT,
                is_reviewed INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bill_id) REFERENCES utility_bills(id)
            );
        """)
        cursor.execute("""
            INSERT INTO alerts_new (
                id, bill_id, severity, reason, recommended_action,
                is_reviewed, created_at
            )
            SELECT id, bill_id, severity, reason, recommended_action,
                   is_reviewed, created_at
            FROM alerts;
        """)
        cursor.execute("DROP TABLE alerts")
        cursor.execute("ALTER TABLE alerts_new RENAME TO alerts")
        conn.commit()
        cursor.execute("PRAGMA foreign_keys = ON")

    conn.close()


def seed_data():
    """
    Seed initial demo data if it does not already exist.
    Guarantees idempotency (no duplicates created on restarts).
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # --- Seed Demo Users ---
    demo_users = [
        ('admin', 'admin123', 'admin'),
        ('supervisor', 'super123', 'supervisor')
    ]
    for username, password, role in demo_users:
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        if cursor.fetchone() is None:
            cursor.execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                (username, password, role)
            )

    # --- Seed Demo Stores ---
    supervisor_name = "Suda Veeraswamy"
    demo_stores = [
        ("K175", "K175 - Metro Supermarket", supervisor_name),
        ("K144", "K144 - Downtown Central Store", supervisor_name),
        ("K137", "K137 - Westside Express", supervisor_name),
        ("K130", "K130 - Harbourfront Market", supervisor_name),
        ("K187", "K187 - Parkside Retail", supervisor_name),
        ("K141", "K141 - Midtown Plaza Store", supervisor_name),
        ("K277", "K277 - Northpoint Hub", supervisor_name),
        ("K164", "K164 - South Valley Mart", supervisor_name),
        ("K165", "K165 - East End Store", supervisor_name),
        ("K305", "K305 - Boulevard Megastore", supervisor_name),
    ]

    for store_code, store_name, supervisor in demo_stores:
        cursor.execute("SELECT id FROM stores WHERE store_code = ?", (store_code,))
        if cursor.fetchone() is None:
            cursor.execute(
                "INSERT INTO stores (store_code, store_name, supervisor) VALUES (?, ?, ?)",
                (store_code, store_name, supervisor)
            )

    conn.commit()

    # --- Seed Historical Electricity Data for K144 ---
    cursor.execute("SELECT id FROM stores WHERE store_code = ?", ("K144",))
    store_k144 = cursor.fetchone()

    if store_k144:
        store_id = store_k144["id"]
        historical_bills = [
            {
                "billing_period": "June 2026",
                "bill_number": "BILL-K144-202606",
                "units": 9840.0,
                "unit_rate": 0.15,
                "amount": 1476.00,
                "bill_date": "2026-06-30",
                "status": "processed"
            },
            {
                "billing_period": "July 2026",
                "bill_number": "BILL-K144-202607",
                "units": 10120.0,
                "unit_rate": 0.15,
                "amount": 1518.00,
                "bill_date": "2026-07-31",
                "status": "processed"
            },
            {
                "billing_period": "August 2026",
                "bill_number": "BILL-K144-202608",
                "units": 10040.0,
                "unit_rate": 0.15,
                "amount": 1506.00,
                "bill_date": "2026-08-31",
                "status": "processed"
            }
        ]

        for bill in historical_bills:
            cursor.execute("""
                SELECT id FROM utility_bills 
                WHERE store_id = ? AND utility_type = ? AND billing_period = ?
            """, (store_id, "Electricity", bill["billing_period"]))
            
            if cursor.fetchone() is None:
                cursor.execute("""
                    INSERT INTO utility_bills (
                        store_id, utility_type, billing_period, bill_number,
                        units, unit_rate, amount, bill_date, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    store_id,
                    "Electricity",
                    bill["billing_period"],
                    bill["bill_number"],
                    bill["units"],
                    bill["unit_rate"],
                    bill["amount"],
                    bill["bill_date"],
                    bill["status"]
                ))

    conn.commit()
    conn.close()


def get_user_by_credentials(username, password):
    """Retrieve user record if username and password match."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, role FROM users WHERE username = ? AND password = ?",
        (username, password)
    )
    user = cursor.fetchone()
    conn.close()
    return user


def get_all_stores():
    """Retrieve all stores."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stores ORDER BY store_code ASC")
    stores = cursor.fetchall()
    conn.close()
    return stores


def get_bills_by_store(store_code):
    """Retrieve all utility bills for a given store code."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ub.*, s.store_code, s.store_name 
        FROM utility_bills ub
        JOIN stores s ON ub.store_id = s.id
        WHERE s.store_code = ?
        ORDER BY ub.id ASC
    """, (store_code,))
    bills = cursor.fetchall()
    conn.close()
    return bills


def get_dashboard_summary():
    """Retrieve live statistics from the database for dashboard metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) AS count FROM stores")
    store_count = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(*) AS count FROM utility_bills")
    total_bills = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) AS count FROM utility_bills WHERE LOWER(status) = 'processed'")
    processed_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) AS count FROM utility_bills WHERE LOWER(status) IN ('verify', 'pending')")
    verify_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) AS count FROM utility_bills WHERE LOWER(status) = 'anomaly'")
    anomaly_count = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(*) AS count FROM alerts WHERE is_reviewed = 0")
    pending_alerts_count = cursor.fetchone()["count"]
    
    conn.close()
    return {
        "store_count": store_count,
        "total_bills": total_bills,
        "bill_count": total_bills,  # Backward compatibility
        "processed_count": processed_count,
        "verify_count": verify_count,
        "anomaly_count": anomaly_count,
        "pending_alerts_count": pending_alerts_count
    }


def get_store_by_id(store_id):
    """Retrieve a store record by its ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stores WHERE id = ?", (store_id,))
    store = cursor.fetchone()
    conn.close()
    return store


def get_all_bills():
    """Retrieve all utility bills ordered newest first."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ub.*, s.store_code, s.store_name, s.supervisor
        FROM utility_bills ub
        JOIN stores s ON ub.store_id = s.id
        ORDER BY ub.id DESC
    """)
    bills = cursor.fetchall()
    conn.close()
    return bills


def get_bills_requiring_verification():
    """Retrieve bills requiring verification (VERIFY or pending) ordered newest first."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ub.*, s.store_code, s.store_name, s.supervisor
        FROM utility_bills ub
        JOIN stores s ON ub.store_id = s.id
        WHERE LOWER(ub.status) IN ('verify', 'pending')
        ORDER BY ub.id DESC
    """)
    bills = cursor.fetchall()
    conn.close()
    return bills


def get_bill_by_id(bill_id):
    """Retrieve a single bill by its ID with store information."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ub.*, s.store_code, s.store_name, s.supervisor
        FROM utility_bills ub
        JOIN stores s ON ub.store_id = s.id
        WHERE ub.id = ?
    """, (bill_id,))
    bill = cursor.fetchone()
    conn.close()
    return bill


def insert_bill(store_id, utility_type, billing_period, bill_number=None,
                units=None, unit_rate=None, amount=None, bill_date=None,
                file_path=None, status='VERIFY'):
    """Insert a newly submitted utility bill."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO utility_bills (
            store_id, utility_type, billing_period, bill_number,
            units, unit_rate, amount, bill_date, file_path, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        store_id,
        utility_type,
        billing_period,
        bill_number,
        units,
        unit_rate,
        amount,
        bill_date,
        file_path,
        status
    ))
    bill_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return bill_id

