# database/sqlite.py
import sqlite3
import json
import os
from utils.config import DATABASE_PATH, API_ID, API_HASH
from utils.logger import logger

# Глобальные переменные для хранения данных
active_sessions = []
available_groups = {}  # Все группы для каждого аккаунта
selected_groups = {}   # Выбранные группы для каждого аккаунта
message_lists = {}     # Сообщения для каждого аккаунта
sending_mode = None    # Режим отправки

# Инициализация базы данных
def init_db():
    try:
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
        os.chmod(os.path.dirname(DATABASE_PATH), 0o755)
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS sessions
                     (phone TEXT PRIMARY KEY, username TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS groups
                     (phone TEXT, group_id INTEGER, title TEXT, selected INTEGER,
                      PRIMARY KEY (phone, group_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS messages
                     (phone TEXT, message TEXT, photo TEXT, custom_emojis TEXT, entities TEXT DEFAULT '[]')''')
        # Миграция: добавляем столбец entities если его нет
        try:
            c.execute("ALTER TABLE messages ADD COLUMN entities TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass  # Столбец уже существует
        c.execute('''CREATE TABLE IF NOT EXISTS reports
                     (timestamp TEXT, phone TEXT, group_name TEXT, has_photo INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings
                     (key TEXT PRIMARY KEY, value TEXT)''')
        conn.commit()
        logger.info("База данных успешно инициализирована")
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}")
    finally:
        conn.close()

# Сохранение активных сессий
def save_active_sessions():
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        for phone in active_sessions:
            username = get_username(phone) or phone
            c.execute("INSERT OR REPLACE INTO sessions (phone, username) VALUES (?, ?)", (phone, username))
        conn.commit()
        logger.info(f"Активные сессии сохранены в базе данных: {active_sessions}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении активных сессий: {e}")
        return False
    finally:
        conn.close()

# Загрузка активных сессий
def load_active_sessions():
    global active_sessions
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        active_sessions = [row[0] for row in c.execute("SELECT phone FROM sessions")]
        logger.info(f"Загружены активные сессии из базы данных: {active_sessions}")
        return active_sessions
    except Exception as e:
        logger.error(f"Ошибка при загрузке активных сессий: {e}")
        return []
    finally:
        conn.close()

# Получение имени пользователя
def get_username(phone):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("SELECT username FROM sessions WHERE phone = ?", (phone,))
        result = c.fetchone()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка при получении имени пользователя для {phone}: {e}")
        return None
    finally:
        conn.close()

# Обновление имени пользователя аккаунта
def update_account_username(phone, username):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO sessions (phone, username) VALUES (?, ?)", (phone, username))
        conn.commit()
        logger.info(f"Обновлено имя пользователя для {phone}: {username}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении имени пользователя для {phone}: {e}")
    finally:
        conn.close()

# Сохранение всех доступных групп
def save_available_groups(phone, groups):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("DELETE FROM groups WHERE phone = ?", (phone,))
        for name, entity in groups:
            group_id = entity.id
            title = name
            selected = 1 if any(g["id"] == group_id for g in selected_groups.get(phone, [])) else 0
            c.execute("INSERT INTO groups (phone, group_id, title, selected) VALUES (?, ?, ?, ?)",
                      (phone, group_id, title, selected))
        conn.commit()
        logger.info(f"Сохранены доступные группы для {phone}: {len(groups)} групп")
    except Exception as e:
        logger.error(f"Ошибка при сохранении доступных групп для {phone}: {e}")
    finally:
        conn.close()

# Загрузка всех доступных групп
def load_available_groups(phone):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("SELECT group_id, title, selected FROM groups WHERE phone = ?", (phone,))
        groups = []
        selected = []
        for row in c.fetchall():
            group_id, title, is_selected = row
            groups.append((title, type('Entity', (), {'id': group_id})()))
            if is_selected:
                selected.append({"id": group_id, "title": title})
        available_groups[phone] = groups
        selected_groups[phone] = selected
        logger.info(f"Загружены доступные группы для {phone}: {len(groups)} групп, {len(selected)} выбрано")
    except Exception as e:
        logger.error(f"Ошибка при загрузке доступных групп для {phone}: {e}")
    finally:
        conn.close()

# Сохранение выбранных групп
def save_selected_groups(phone):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("UPDATE groups SET selected = 0 WHERE phone = ?", (phone,))
        for group in selected_groups.get(phone, []):
            c.execute("UPDATE groups SET selected = 1 WHERE phone = ? AND group_id = ?",
                      (phone, group["id"]))
        conn.commit()
        logger.info(f"Сохранены выбранные группы для {phone}: {len(selected_groups.get(phone, []))} групп")
    except Exception as e:
        logger.error(f"Ошибка при сохранении выбранных групп для {phone}: {e}")
    finally:
        conn.close()

# Загрузка выбранных групп
def load_selected_groups(phone):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("SELECT group_id, title FROM groups WHERE phone = ? AND selected = 1", (phone,))
        selected_groups[phone] = [{"id": row[0], "title": row[1]} for row in c.fetchall()]
        logger.info(f"Загружены выбранные группы для {phone}: {len(selected_groups[phone])} групп")
    except Exception as e:
        logger.error(f"Ошибка при загрузке выбранных групп для {phone}: {e}")
    finally:
        conn.close()

# Сохранение сообщений
def save_messages(phone):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        
        # Проверяем наличие столбца entities и добавляем его если нужно
        c.execute("PRAGMA table_info(messages)")
        columns = [column[1] for column in c.fetchall()]
        if "entities" not in columns:
            try:
                c.execute("ALTER TABLE messages ADD COLUMN entities TEXT")
                conn.commit()
                logger.info("Добавлен столбец entities в таблицу messages")
            except Exception as e:
                logger.warning(f"Столбец entities уже существует или не может быть добавлен: {e}")
        
        c.execute("DELETE FROM messages WHERE phone = ?", (phone,))
        for msg in message_lists.get(phone, []):
            custom_emojis_json = json.dumps(msg.get("custom_emojis", []))
            entities_json = json.dumps(msg.get("entities", []))
            c.execute("INSERT INTO messages (phone, message, photo, custom_emojis, entities) VALUES (?, ?, ?, ?, ?)",
                      (phone, msg["text"], msg["photo"], custom_emojis_json, entities_json))
        conn.commit()
        logger.info(f"Сохранены сообщения для {phone}: {len(message_lists.get(phone, []))} сообщений")
    except Exception as e:
        logger.error(f"Ошибка при сохранении сообщений для {phone}: {e}")
    finally:
        conn.close()

# Загрузка сообщений
def load_messages(phone):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        
        # Проверяем наличие столбца entities
        c.execute("PRAGMA table_info(messages)")
        columns = [column[1] for column in c.fetchall()]
        has_entities = "entities" in columns
        
        if has_entities:
            c.execute("SELECT message, photo, custom_emojis, entities FROM messages WHERE phone = ?", (phone,))
        else:
            c.execute("SELECT message, photo, custom_emojis FROM messages WHERE phone = ?", (phone,))
        
        messages = []
        for row in c.fetchall():
            if has_entities:
                message, photo, custom_emojis_json, entities_json = row
                entities = json.loads(entities_json) if entities_json else []
            else:
                message, photo, custom_emojis_json = row
                entities = []
            
            custom_emojis = json.loads(custom_emojis_json) if custom_emojis_json else []
            messages.append({
                "text": message,
                "photo": photo,
                "custom_emojis": custom_emojis,
                "entities": entities
            })
        message_lists[phone] = messages
        logger.info(f"Загружены сообщения для {phone}: {len(messages)} сообщений")
    except Exception as e:
        logger.error(f"Ошибка при загрузке сообщений для {phone}: {e}")
    finally:
        conn.close()

# Сохранение отчётов
def save_report(timestamp, phone, group_name, has_photo):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("INSERT INTO reports (timestamp, phone, group_name, has_photo) VALUES (?, ?, ?, ?)",
                  (timestamp, phone, group_name, int(has_photo)))
        conn.commit()
        logger.info(f"Сохранён отчёт: {timestamp} {phone} -> {group_name} (фото={has_photo})")
    except Exception as e:
        logger.error(f"Ошибка при сохранении отчёта для {phone} в {group_name}: {e}")
    finally:
        conn.close()

# Загрузка отчётов
def load_reports(limit=10):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("SELECT timestamp, phone, group_name, has_photo FROM reports ORDER BY timestamp DESC LIMIT ?",
                  (limit,))
        reports = c.fetchall()
        logger.info(f"Загружено {len(reports)} отчётов из базы данных")
        return reports
    except Exception as e:
        logger.error(f"Ошибка при загрузке отчётов: {e}")
        return []
    finally:
        conn.close()

# Очистка отчётов
def clear_reports():
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("DELETE FROM reports")
        conn.commit()
        logger.info("Все отчёты удалены из базы данных")
    except Exception as e:
        logger.error(f"Ошибка при очистке отчётов: {e}")
    finally:
        conn.close()

# Сохранение настроек
def save_settings():
    global sending_mode
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        if sending_mode:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                      ("sending_mode", sending_mode))
        conn.commit()
        logger.info(f"Сохранены настройки: sending_mode={sending_mode}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении настроек: {e}")
    finally:
        conn.close()

# Загрузка настроек
def load_settings():
    global sending_mode
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", ("sending_mode",))
        result = c.fetchone()
        sending_mode = result[0] if result else None
        logger.info(f"Загружены настройки: sending_mode={sending_mode}")
    except Exception as e:
        logger.error(f"Ошибка при загрузке настроек: {e}")
    finally:
        conn.close()

# Удаление данных аккаунта
def delete_account_data(phone):
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10)
        c = conn.cursor()
        c.execute("DELETE FROM sessions WHERE phone = ?", (phone,))
        c.execute("DELETE FROM groups WHERE phone = ?", (phone,))
        c.execute("DELETE FROM messages WHERE phone = ?", (phone,))
        conn.commit()
        logger.info(f"Удалены все данные для {phone} из базы данных")
        return True
    except Exception as e:
        logger.error(f"Ошибка при удалении данных для {phone}: {e}")
        return False
    finally:
        conn.close()