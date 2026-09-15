# utils/config.py
import os

# Основные пути
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
SESSION_DIR = os.path.join(DATA_DIR, "sessions")
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
DATABASE_PATH = os.path.join(DATA_DIR, "bot_database.db")

# API ID и API Hash для Telegram
API_ID = "29294039"
API_HASH = "2eac3d1c5148432824b64f32fc3de18d"

# Создание директорий
for directory in [DATA_DIR, SESSION_DIR, PHOTO_DIR]:
    try:
        os.makedirs(directory, exist_ok=True)
    except Exception:
        pass