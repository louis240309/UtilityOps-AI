import os
import sqlite3
import uuid
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# Database configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('DATABASE_PATH') or os.path.join(BASE_DIR, 'utilityops.db')


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

    # Upgrade legacy plaintext demo credentials in place. Werkzeug hashes are
    # self identifying; this migration is safe to rerun on every startup.
    for user in cursor.execute("SELECT id, password FROM users").fetchall():
        stored = user['password'] or ''
        if not stored.startswith(('scrypt:', 'pbkdf2:')):
            cursor.execute("UPDATE users SET password = ? WHERE id = ?",
                           (generate_password_hash(stored), user['id']))

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

    # Additive n8n automation fields. Existing rows and their relationships are
    # left in place; the default labels pre-integration bills as not configured.
    cursor.execute("PRAGMA table_info(utility_bills)")
    bill_columns = {row['name'] for row in cursor.fetchall()}
    if 'automation_status' not in bill_columns:
        cursor.execute(
            "ALTER TABLE utility_bills ADD COLUMN automation_status TEXT NOT NULL DEFAULT 'not_configured'"
        )
    if 'decision_reason' not in bill_columns:
        cursor.execute("ALTER TABLE utility_bills ADD COLUMN decision_reason TEXT")
    if 'automation_request_id' not in bill_columns:
        cursor.execute("ALTER TABLE utility_bills ADD COLUMN automation_request_id TEXT")

    if 'processing_state' not in bill_columns:
        cursor.execute("ALTER TABLE utility_bills ADD COLUMN processing_state TEXT NOT NULL DEFAULT 'RECEIVED'")
    if 'analysis_status' not in bill_columns:
        cursor.execute("ALTER TABLE utility_bills ADD COLUMN analysis_status TEXT NOT NULL DEFAULT 'NOT_ANALYZED'")

    # Keep legacy status readable while exposing distinct workflow concepts.
    cursor.execute("""
        UPDATE utility_bills
        SET processing_state = CASE
            WHEN automation_status = 'completed' AND UPPER(status) IN ('NORMAL','ANOMALY') THEN 'COMPLETED'
            WHEN UPPER(status) IN ('VERIFY','PENDING') THEN 'REVIEW'
            WHEN LOWER(status) = 'processed' THEN 'COMPLETED'
            ELSE processing_state END,
            analysis_status = CASE
            WHEN UPPER(status) IN ('NORMAL','ANOMALY') THEN UPPER(status)
            ELSE 'NOT_ANALYZED' END
    """)

    # Normalize recognizable legacy month labels to YYYY-MM.
    for bill in cursor.execute("SELECT id, billing_period FROM utility_bills").fetchall():
        try:
            normalized_period = datetime.strptime(bill['billing_period'], '%B %Y').strftime('%Y-%m')
        except (ValueError, TypeError):
            continue
        cursor.execute("UPDATE utility_bills SET billing_period = ? WHERE id = ?",
                       (normalized_period, bill['id']))

    # Enforce one bill for each store/utility/period, including blank numbers.
    # Avoid failing startup if an old database already contains duplicates.
    duplicate = cursor.execute("""
        SELECT 1 FROM utility_bills GROUP BY store_id, utility_type, billing_period
        HAVING COUNT(*) > 1 LIMIT 1
    """).fetchone()
    if not duplicate:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_store_utility_period
            ON utility_bills(store_id, utility_type, billing_period)
        """)

    conn.commit()

    conn.close()


def set_bill_automation_request(bill_id, request_id):
    """Record the request before dispatch so a fast callback can be matched."""
    try:
        uuid.UUID(request_id)
    except (ValueError, TypeError, AttributeError):
        return False

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE utility_bills
        SET automation_request_id = ?, automation_status = 'queued'
        WHERE id = ?
    """, (request_id, bill_id))
    updated = cursor.rowcount == 1
    conn.commit()
    conn.close()
    return updated


def set_bill_automation_status(bill_id, request_id, status):
    """Store dispatch outcome without overwriting a callback already completed."""
    if status not in {'not_configured', 'unavailable', 'queued'}:
        return False
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE utility_bills
        SET automation_status = ?
        WHERE id = ? AND automation_request_id = ?
          AND automation_status != 'completed'
    """, (status, bill_id, request_id))
    updated = cursor.rowcount == 1
    conn.commit()
    conn.close()
    return updated


def apply_n8n_bill_result(bill_id, request_id, decision, extracted_fields=None,
                          reason=None, recommended_action=None):
    """Atomically apply one validated n8n result and any corresponding alert.

    Returns one of: applied, duplicate, not_found, request_mismatch, invalid.
    """
    if decision not in {'NORMAL', 'VERIFY', 'ANOMALY'}:
        return 'invalid'
    try:
        uuid.UUID(request_id)
    except (ValueError, TypeError, AttributeError):
        return 'invalid'
    if extracted_fields is None:
        extracted_fields = {}
    allowed_fields = {'bill_number', 'units', 'unit_rate', 'amount', 'bill_date'}
    if not isinstance(extracted_fields, dict) or set(extracted_fields) - allowed_fields:
        return 'invalid'

    conn = get_db_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        cursor = conn.cursor()
        cursor.execute(
            "SELECT automation_request_id, automation_status FROM utility_bills WHERE id = ?",
            (bill_id,)
        )
        bill = cursor.fetchone()
        if bill is None:
            conn.rollback()
            return 'not_found'
        if bill['automation_request_id'] != request_id:
            conn.rollback()
            return 'request_mismatch'
        if bill['automation_status'] == 'completed':
            conn.rollback()
            return 'duplicate'

        updates = {
            key: value for key, value in extracted_fields.items()
            if value is not None
        }
        assignments = ['status = ?', 'decision_reason = ?',
                       "automation_status = 'completed'", "processing_state = ?", "analysis_status = ?"]
        values = [decision, reason, 'REVIEW' if decision == 'VERIFY' else 'COMPLETED',
                  decision if decision in {'NORMAL', 'ANOMALY'} else 'NOT_ANALYZED']
        for field, value in updates.items():
            assignments.append(f'"{field}" = ?')
            values.append(value)
        values.extend([request_id, bill_id])
        cursor.execute(
            f"UPDATE utility_bills SET {', '.join(assignments)} "
            "WHERE automation_request_id = ? AND id = ?",
            values,
        )

        if decision in {'VERIFY', 'ANOMALY'}:
            alert_reason = (reason or f'n8n returned {decision}.')[:2000]
            alert_action = recommended_action
            severity = 'high' if decision == 'ANOMALY' else 'medium'
            cursor.execute("""
                INSERT INTO alerts (bill_id, severity, reason, recommended_action)
                VALUES (?, ?, ?, ?)
            """, (bill_id, severity, alert_reason, alert_action))

        conn.commit()
        return 'applied'
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
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
        ('admin', generate_password_hash('admin123'), 'admin'),
        ('supervisor', generate_password_hash('super123'), 'supervisor')
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
                "billing_period": "2026-06",
                "bill_number": "BILL-K144-202606",
                "units": 9840.0,
                "unit_rate": 0.15,
                "amount": 1476.00,
                "bill_date": "2026-06-30",
                "status": "processed"
            },
            {
                "billing_period": "2026-07",
                "bill_number": "BILL-K144-202607",
                "units": 10120.0,
                "unit_rate": 0.15,
                "amount": 1518.00,
                "bill_date": "2026-07-31",
                "status": "processed"
            },
            {
                "billing_period": "2026-08",
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
    """Verify a password hash, upgrading legacy plaintext credentials if found."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role, password FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    if user:
        stored = user['password'] or ''
        try:
            valid = check_password_hash(stored, password)
        except (ValueError, TypeError):
            valid = False
        if not valid and not stored.startswith(('scrypt:', 'pbkdf2:')) and stored == password:
            cursor.execute("UPDATE users SET password = ? WHERE id = ?",
                           (generate_password_hash(password), user['id']))
            valid = True
        if not valid:
            user = None
        elif user:
            user = {key: user[key] for key in ('id', 'username', 'role')}
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

    cursor.execute("SELECT COUNT(*) AS count FROM utility_bills WHERE processing_state = 'COMPLETED'")
    processed_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) AS count FROM utility_bills WHERE processing_state IN ('RECEIVED','PROCESSING','REVIEW','FAILED')")
    verify_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) AS count FROM utility_bills WHERE analysis_status = 'ANOMALY'")
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


def bill_exists_for_period(store_id, utility_type, billing_period):
    conn = get_db_connection()
    try:
        return conn.execute("""
            SELECT 1 FROM utility_bills
            WHERE store_id = ? AND utility_type = ? AND billing_period = ? LIMIT 1
        """, (store_id, utility_type, billing_period)).fetchone() is not None
    finally:
        conn.close()


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
        WHERE ub.processing_state IN ('RECEIVED','PROCESSING','REVIEW','FAILED')
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
            units, unit_rate, amount, bill_date, file_path, status,
            processing_state, analysis_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECEIVED', 'NOT_ANALYZED')
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
