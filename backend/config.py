import os
from dotenv import load_dotenv

load_dotenv()

# Project root directory (parent of backend/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# JWT Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "ag-mgmt-secret-key-change-in-production-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12  # 12 hours

# S1: Detect insecure default SECRET_KEY
_DEFAULT_SECRET_KEYS = ["ag-mgmt-secret-key-change-in-production-2024"]
IS_DEFAULT_SECRET_KEY = SECRET_KEY in _DEFAULT_SECRET_KEYS

# Database
DATA_DIR = os.path.join(BASE_DIR, "data")
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(DATA_DIR, "agreement_management.db"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATABASE_PATH}")

# Groq AI
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

# File Upload
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(DATA_DIR, "uploads"))
PROFORMA_DIR = os.path.join(DATA_DIR, "proforma_invoices")
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB (standard PDF / DOCX)
MAX_SCANNED_FILE_SIZE = 100 * 1024 * 1024  # 100MB (scanned PDF with OCR)
ALLOWED_EXTENSIONS = {".pdf", ".docx"}

# Server
HOST = os.getenv("HOST", "localhost")
PORT = int(os.getenv("PORT", 8000))

# Email System
EMAIL_REMINDER_DAYS = 7  # Reference value for payment reminder window

# Public-facing application URL (used for email tracking pixels)
# Auto-detects: explicit APP_URL > Railway's RAILWAY_PUBLIC_DOMAIN > local HOST:PORT
_app_url_env = os.getenv("APP_URL", "").rstrip("/")
_railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
if _app_url_env:
    APP_URL = _app_url_env
elif _railway_domain:
    APP_URL = f"https://{_railway_domain}"
else:
    APP_URL = f"http://{HOST}:{PORT}"

# Encryption key for sensitive credentials (derived from SECRET_KEY)
import hashlib
import base64
_key_bytes = hashlib.sha256(SECRET_KEY.encode()).digest()
ENCRYPTION_KEY = base64.urlsafe_b64encode(_key_bytes)
