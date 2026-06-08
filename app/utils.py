import os
from datetime import datetime

def ensure_data_dirs():
    os.makedirs("data/screenshots", exist_ok=True)
    os.makedirs("data/audits", exist_ok=True)

def timestamp_str():
    return datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
