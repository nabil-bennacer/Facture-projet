from pathlib import Path
from dotenv import load_dotenv
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
LANDING_DIR = DATA_DIR / "landing"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
GOLD_DIR = DATA_DIR / "gold"

for d in [LANDING_DIR, BRONZE_DIR, SILVER_DIR, GOLD_DIR]:
    d.mkdir(parents=True, exist_ok=True)

S3_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL")
S3_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
S3_REGION = os.getenv("AWS_DEFAULT_REGION", "default")
S3_BUCKET = os.getenv("S3_BUCKET", "factures")

OCR_API_URL = os.getenv("OCR_API_URL", "")
OCR_API_KEY = os.getenv("OCR_API_KEY", "")
