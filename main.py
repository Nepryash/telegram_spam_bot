import asyncio
import os
import sys
from dotenv import load_dotenv

# Ensure UTF-8 output encoding for Windows
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Load environment variables
load_dotenv()

# Add root directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from core.bot import active_sessions, register_handlers
from database.sqlite import (
    init_db, save_active_sessions, load_active_sessions, load_messages, load_selected_groups, load_available_groups, load_settings
)
from utils.config import SESSION_DIR, DATABASE_PATH
from utils.logger import logger

async def sync_sessions_with_db():
    """Синхронизация .session файлов с базой."""
    try:
        session_files = [f for f in os.listdir(SESSION_DIR) if f.endswith(".session")]
        session_phones = [f.replace(".session", "") for f in session_files]
        
        db_phones = load_active_sessions()

        # Добавляем только те .session файлы, которые есть в базе
        for phone in session_phones:
            if phone in db_phones:
                if phone not in active_sessions:
                    active_sessions.append(phone)
                    logger.info(f"Added {phone} from session files to active sessions")
            else:
                # Удаляем .session файл, если его нет в базе
                session_file = os.path.join(SESSION_DIR, f"{phone}.session")
                try:
                    os.remove(session_file)
                    logger.info(f"Removed stale session file for {phone}")
                except FileNotFoundError:
                    logger.warning(f"Session file {session_file} not found during removal")
        
        if not save_active_sessions():
            logger.error("Failed to save to SQLite database")
    except Exception as e:
        logger.error(f"Error in sync_sessions_with_db: {e}")

async def main():
    load_dotenv()
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not found in .env file")

    # Создаём bot и dp
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="MarkdownV2"))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Регистрируем обработчики
    register_handlers(dp, bot)
    logger.info("Bot token loaded successfully")

    # Инициализация базы данных
    if not os.path.exists(DATABASE_PATH):
        logger.info("Database not found, creating new one")
        init_db()
    else:
        logger.info("Database exists, initializing")
        init_db()

    # Загрузка данных
    load_active_sessions()
    # Удаляем дубликаты из active_sessions
    active_sessions[:] = list(dict.fromkeys(active_sessions))

    await sync_sessions_with_db()

    load_settings()
    for phone in active_sessions:
        load_available_groups(phone)  # Добавляем загрузку доступных групп
        load_selected_groups(phone)
        load_messages(phone)

    # Проверка прав доступа к директориям
    for directory in [SESSION_DIR, os.path.dirname(DATABASE_PATH)]:
        try:
            os.makedirs(directory, exist_ok=True)
            os.chmod(directory, 0o755)
        except Exception as e:
            logger.error(f"Failed to set permissions for {directory}: {e}")

    try:
        # Проверка токена бота
        bot_info = await bot.get_me()
        logger.info(f"Bot token validated: @{bot_info.username}")
        logger.info("Bot initialized, starting polling")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot error: {e}")
        raise
    finally:
        await bot.session.close()
        logger.info("Bot session closed")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Main loop failed: {e}")
    finally:
        logger.info("Bot shutdown complete")