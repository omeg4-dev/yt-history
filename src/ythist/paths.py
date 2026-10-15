from pathlib import Path
import os

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "ythist"
PROFILE_DIR = DATA_DIR / "profile"
DB_PATH = DATA_DIR / "history.db"
RAW_DIR = DATA_DIR / "raw"
