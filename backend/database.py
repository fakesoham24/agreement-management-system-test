import sqlite3
import os
from backend.config import DATABASE_PATH, DATA_DIR, PROFORMA_DIR


def get_db():
    """Get database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def get_db_connection():
    """Get a direct database connection (non-generator)."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize database tables."""
    os.makedirs(DATA_DIR, exist_ok=True)
    db_dir = os.path.dirname(DATABASE_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user' CHECK(role IN ('admin', 'user')),
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Agreements table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agreements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'active', 'expired', 'terminated')),
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Agreement Analysis table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agreement_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agreement_id INTEGER UNIQUE NOT NULL,
            company_name TEXT,
            agreement_date TEXT,
            consulting_start_date TEXT,
            consulting_end_date TEXT,
            payment_type TEXT,
            payment_amount REAL,
            payment_frequency TEXT,
            payment_schedule TEXT,
            raw_text TEXT,
            summary TEXT,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE CASCADE
        )
    """)

    # Payments table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agreement_id INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'paid', 'overdue')),
            paid_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE CASCADE
        )
    """)

    # Notifications table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            agreement_id INTEGER,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            type TEXT DEFAULT 'info' CHECK(type IN ('info', 'warning', 'alert')),
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE SET NULL
        )
    """)

    # Email Settings table (Gmail OAuth2 credentials and template)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_client_id TEXT,
            gmail_client_secret_encrypted TEXT,
            gmail_refresh_token_encrypted TEXT,
            sender_email TEXT,
            cc_emails TEXT,
            email_subject TEXT DEFAULT 'Payment Reminder — {{company_name}}',
            email_template_type TEXT DEFAULT 'text' CHECK(email_template_type IN ('text', 'html')),
            email_template TEXT,
            is_enabled INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Email Log table (track sent emails to prevent duplicates)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER,
            agreement_id INTEGER NOT NULL,
            recipient_email TEXT NOT NULL,
            subject TEXT,
            status TEXT DEFAULT 'sent',
            error_message TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE CASCADE
        )
    """)

    # Migrate agreements table — add is_viewed column for "New" badge
    try:
        cursor.execute("ALTER TABLE agreements ADD COLUMN is_viewed INTEGER DEFAULT 0")
    except Exception:
        pass  # Column already exists

    # Migrate agreements table — add renewal_status column
    try:
        cursor.execute("ALTER TABLE agreements ADD COLUMN renewal_status TEXT DEFAULT NULL")
    except Exception:
        pass  # Column already exists

    # Migrate agreements table — add renewal_increase_percent column
    try:
        cursor.execute("ALTER TABLE agreements ADD COLUMN renewal_increase_percent REAL DEFAULT 10")
    except Exception:
        pass  # Column already exists

    # Migrate agreement_analysis table — add new columns for deep analysis
    new_columns = [
        # Agreement Overview
        ("agreement_title", "TEXT"),
        ("agreement_type", "TEXT"),
        ("contact_person", "TEXT"),
        ("effective_date", "TEXT"),
        ("expiry_date", "TEXT"),
        ("priority_level", "TEXT"),
        ("auto_renewal", "TEXT"),
        ("currency", "TEXT DEFAULT '₹'"),
        # Company Information
        ("industry", "TEXT"),
        ("website", "TEXT"),
        ("gst_number", "TEXT"),
        ("company_size", "TEXT"),
        ("email", "TEXT"),
        ("phone", "TEXT"),
        ("alternate_contact", "TEXT"),
        # Timeline
        ("approved_date", "TEXT"),
        ("signed_date", "TEXT"),
        ("active_date", "TEXT"),
        ("renewal_due_date", "TEXT"),
        # Payment Structure
        ("payment_method", "TEXT"),
        ("remaining_balance", "REAL"),
        ("next_due_date", "TEXT"),
        ("late_fee_policy", "TEXT"),
        ("payment_plans", "TEXT"),
        # Consulting Visit Schedule
        ("consulting_visits", "TEXT"),
        # Legal Clauses
        ("nda_included", "TEXT"),
        ("non_solicitation", "TEXT"),
        ("non_compete", "TEXT"),
        ("confidentiality_clause", "TEXT"),
        ("data_protection_clause", "TEXT"),
        ("arbitration_clause", "TEXT"),
        ("jurisdiction", "TEXT"),
        # Services
        ("services", "TEXT"),
        # Manual Upload Support
        ("note", "TEXT"),
        ("upload_type", "TEXT DEFAULT 'automatic'"),
    ]

    for col_name, col_type in new_columns:
        try:
            cursor.execute(f"ALTER TABLE agreement_analysis ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass  # Column already exists

    # Migrate email_settings table — add email_template_html column for separate HTML template storage
    try:
        cursor.execute("ALTER TABLE email_settings ADD COLUMN email_template_html TEXT")
    except Exception:
        pass  # Column already exists

    # Migrate users table — add global_payment_access column (HR role)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN global_payment_access INTEGER DEFAULT 0")
    except Exception:
        pass  # Column already exists

    # ── Consultants table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS consultants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            designation TEXT NOT NULL,
            email TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Agreement-Consultant junction table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agreement_consultants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agreement_id INTEGER NOT NULL,
            consultant_id INTEGER NOT NULL,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE CASCADE,
            FOREIGN KEY (consultant_id) REFERENCES consultants(id) ON DELETE CASCADE,
            UNIQUE(agreement_id, consultant_id)
        )
    """)

    # Migrate email_settings — add consultant email template columns
    consultant_email_cols = [
        ("consultant_email_subject", "TEXT DEFAULT 'Payment Reminder — {{company_name}} (Internal)'"),
        ("consultant_email_template", "TEXT"),
        ("consultant_email_template_html", "TEXT"),
        ("consultant_email_template_type", "TEXT DEFAULT 'text'"),
    ]
    for col_name, col_type in consultant_email_cols:
        try:
            cursor.execute(f"ALTER TABLE email_settings ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass  # Column already exists

    # Migrate email_log — add email_type column to distinguish client vs consultant emails
    try:
        cursor.execute("ALTER TABLE email_log ADD COLUMN email_type TEXT DEFAULT 'client'")
    except Exception:
        pass  # Column already exists

    # ── Pro Forma Invoices table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS proforma_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agreement_id INTEGER NOT NULL,
            payment_id INTEGER NOT NULL,
            invoice_no TEXT,
            file_path TEXT NOT NULL,
            form_data TEXT,
            status TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'confirmed')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE CASCADE,
            FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE
        )
    """)

    # Migrate email_log — add proforma_invoice_path to track attached PDF
    try:
        cursor.execute("ALTER TABLE email_log ADD COLUMN proforma_invoice_path TEXT")
    except Exception:
        pass  # Column already exists

    # Migrate email_log — remove NOT NULL on payment_id and remove ON DELETE CASCADE
    # on payment_id foreign key. This preserves email history when payments are
    # recreated (e.g., when due dates are changed). Email logs should only be
    # removed after 30 days or by manual admin deletion.
    try:
        # Check if migration is needed by inspecting the table schema
        table_info = cursor.execute("PRAGMA table_info(email_log)").fetchall()
        payment_id_col = next((col for col in table_info if col[1] == 'payment_id'), None)
        if payment_id_col and payment_id_col[3] == 1:  # col[3] = notnull flag
            # payment_id is still NOT NULL — need to migrate
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS email_log_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_id INTEGER,
                    agreement_id INTEGER NOT NULL,
                    recipient_email TEXT NOT NULL,
                    subject TEXT,
                    status TEXT DEFAULT 'sent',
                    error_message TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    email_type TEXT DEFAULT 'client',
                    proforma_invoice_path TEXT,
                    FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                INSERT INTO email_log_new (id, payment_id, agreement_id, recipient_email, subject, status, error_message, sent_at, email_type, proforma_invoice_path)
                SELECT id, payment_id, agreement_id, recipient_email, subject, status, error_message, sent_at, email_type, proforma_invoice_path
                FROM email_log
            """)
            cursor.execute("DROP TABLE email_log")
            cursor.execute("ALTER TABLE email_log_new RENAME TO email_log")
            cursor.execute("PRAGMA foreign_keys=ON")
            conn.commit()
    except Exception:
        pass  # Migration already done or table doesn't exist yet

    # Migrate email_log — add tracking_id and opened_at for email open tracking
    email_tracking_cols = [
        ("tracking_id", "TEXT"),        # UUID used in tracking pixel URL
        ("opened_at", "TIMESTAMP"),     # When the email was first opened (NULL = pending)
    ]
    for col_name, col_type in email_tracking_cols:
        try:
            cursor.execute(f"ALTER TABLE email_log ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass  # Column already exists

    # Index on tracking_id for fast pixel lookup
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_log_tracking_id ON email_log(tracking_id)")

    # Create proforma invoices directory
    os.makedirs(PROFORMA_DIR, exist_ok=True)

    # Cleanup: delete all draft (unconfirmed) proforma invoices and their PDF files
    # Drafts are temporary and should not persist between server restarts
    draft_invoices = cursor.execute(
        "SELECT id, file_path FROM proforma_invoices WHERE status = 'draft'"
    ).fetchall()
    for draft in draft_invoices:
        d_path = draft[1]  # file_path
        if d_path and os.path.exists(d_path):
            try:
                os.remove(d_path)
            except OSError:
                pass
    cursor.execute("DELETE FROM proforma_invoices WHERE status = 'draft'")

    conn.commit()

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agreements_user_id ON agreements(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_agreement_id ON agreement_analysis(agreement_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_agreement_id ON payments(agreement_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agreement_consultants_aid ON agreement_consultants(agreement_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agreement_consultants_cid ON agreement_consultants(consultant_id)")

    conn.commit()

    # One-time data migration: consolidate date fields
    # Copy consulting_start_date → effective_date where effective_date is empty
    cursor.execute("""
        UPDATE agreement_analysis
        SET effective_date = consulting_start_date
        WHERE (effective_date IS NULL OR effective_date = '')
          AND consulting_start_date IS NOT NULL AND consulting_start_date != ''
    """)
    # Copy consulting_end_date → expiry_date where expiry_date is empty
    cursor.execute("""
        UPDATE agreement_analysis
        SET expiry_date = consulting_end_date
        WHERE (expiry_date IS NULL OR expiry_date = '')
          AND consulting_end_date IS NOT NULL AND consulting_end_date != ''
    """)
    conn.commit()

    # Create default admin if not exists
    from passlib.hash import bcrypt
    admin_exists = cursor.execute("SELECT id FROM users WHERE role='admin'").fetchone()
    if not admin_exists:
        admin_hash = bcrypt.hash("admin123")
        cursor.execute(
            "INSERT INTO users (username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("admin", "admin@dvconsulting.co.in", "System Administrator", admin_hash, "admin")
        )
        conn.commit()

    conn.close()
