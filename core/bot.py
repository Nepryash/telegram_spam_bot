# core/bot.py
import asyncio
import os
import re
import random
from datetime import datetime
from aiogram import types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from utils.config import SESSION_DIR, PHOTO_DIR
from database.sqlite import (
    active_sessions, available_groups, selected_groups, message_lists, API_ID, API_HASH, sending_mode,
    load_messages, save_messages, load_selected_groups, save_selected_groups, load_available_groups, save_available_groups,
    update_account_username, save_active_sessions, load_reports, clear_reports, save_settings, save_report,
    delete_account_data
)
from utils.logger import logger
from core.client import send_message
from core.group_manager import (
    cache_groups, get_cached_groups, clear_cache, 
    select_all_page_groups, deselect_all_page_groups,
    copy_selection_to_account, get_accounts_with_similar_groups
)

# Состояния для FSM
class AddAccount(StatesGroup):
    WaitingPhone = State()
    WaitingCode = State()
    WaitingPassword = State()

class AddMessage(StatesGroup):
    WaitingText = State()

class StartSpamming(StatesGroup):
    SelectingAccounts = State()
    SelectingMode = State()
    SettingInterval = State()

# Глобальные переменные для хранения состояния рассылки
spamming_task = None
spamming_accounts = []
spamming_mode = None
spamming_interval = 60  # Значение по умолчанию
is_spamming = False
clients = {}

# Количество групп на одной странице
GROUPS_PER_PAGE = 10

# Функция для экранирования специальных символов в MarkdownV2
def escape_markdown_v2(text: str) -> str:
    special_chars = r'([_*[\]()~`>#+-=|{}.!])'
    return re.sub(special_chars, r'\\\1', text)

# Главное меню
async def get_main_inline_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    # Верхний ряд: статус
    buttons.append([InlineKeyboardButton(text="📊 Статус", callback_data="show_status")])

    # Второй ряд: аккаунты/сообще��ия
    buttons.append([
        InlineKeyboardButton(text="👥 Аккаунты", callback_data="manage_accounts"),
        InlineKeyboardButton(text="✉️ Сообщения", callback_data="manage_messages"),
    ])

    # Третий ряд: группы/отчёты
    buttons.append([
        InlineKeyboardButton(text="👥 Группы", callback_data="select_groups"),
        InlineKeyboardButton(text="📈 Отчёты", callback_data="show_report"),
    ])

    # Четвёртый ряд: настройки
    buttons.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu")])

    # Нижний ряд: динамическая кнопка запуска/остановки
    can_start = (
        bool(active_sessions)
        and any(message_lists.get(p) for p in active_sessions)
        and any(selected_groups.get(p) for p in active_sessions)
    )

    if is_spamming:
        buttons.append([InlineKeyboardButton(text="🛑 Остановить рассылку", callback_data="stop_spamming")])
    elif can_start:
        buttons.append([InlineKeyboardButton(text="🚀 Запустить рассылку", callback_data="start_spamming")])
    else:
        # Подсказка, если запуск невозможен (не засоряе�� меню лишними функциями)
        buttons.append([InlineKeyboardButton(text="ℹ️ Запуск недоступен", callback_data="show_status")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Текст статуса
async def get_status_text() -> str:
    active_accounts = len(active_sessions)
    total_groups = sum(len(groups) for groups in selected_groups.values())
    total_messages = sum(len(messages) for messages in message_lists.values())
    reports = load_reports(limit=999999999999)  # Загружаем больше отчётов для подсчёта
    total_sent = len(reports)
    spamming_status = "Запущена" if is_spamming else "Остановлена"
    accounts_status = []
    for phone in active_sessions:
        session_path = os.path.join(SESSION_DIR, f"{phone}.session")
        authorized = os.path.exists(session_path)
        status = "✅ Авторизован" if authorized else "❌ Не авторизован"
        accounts_status.append(f"{phone}: {status}")
    accounts_text = "\n".join(accounts_status) if accounts_status else "Нет аккаунтов"
    return (
        f"*Статус бота*:\n"
        f"🔄 Рассылка: {spamming_status}\n"
        f"👥 Аккаунты: {active_accounts}\n"
        f"👥 Группы: {total_groups}\n"
        f"✉️ Сообщения: {total_messages}\n"
        f"📬 Отправлено сообщений: {total_sent}\n"
        f"📤 Режим: {spamming_mode if spamming_mode else 'Не выбран'}\n"
        f"⏱ Интервал: {spamming_interval if spamming_interval else 'Не задан'} сек\n"
        f"🔑 API ID: {API_ID}\n"
        f"🔒 API Hash: {API_HASH}\n"
        f"\n*Статус аккаунтов*:\n{escape_markdown_v2(accounts_text)}"
    )

# Функция для создания и подключения клиентов
async def setup_clients():
    global clients
    for phone in spamming_accounts:
        if phone not in active_sessions:
            continue
        if phone in clients:
            continue
        session_path = os.path.join(SESSION_DIR, phone)
        client = TelegramClient(session_path, API_ID, API_HASH)
        try:
            await client.connect()
            if await client.is_user_authorized():
                clients[phone] = client
                logger.info(f"Клиент для {phone} успешно настроен")
            else:
                logger.warning(f"Клиент для {phone} не авторизован, пропускаем...")
        except Exception as e:
            logger.error(f"Ошибка при настройке клиента для {phone}: {str(e)}")

# Функция для отключения всех клиентов
async def disconnect_clients():
    global clients
    for phone, client in list(clients.items()):
        try:
            if client.is_connected():
                await client.disconnect()
                logger.info(f"Клиент для {phone} отключён")
        except Exception as e:
            logger.error(f"Ошибка при отключении клиента для {phone}: {str(e)}")
        finally:
            clients.pop(phone, None)
    logger.info("Все клиенты отключены")

# Функция рассылки
async def start_spamming():
    global is_spamming
    is_spamming = True

    async def sleep_after_success():
        try:
            for _ in range(int(spamming_interval)):
                if not is_spamming:
                    break
                await asyncio.sleep(1)
        except Exception as e:
            logger.warning(f"Ошибка ожидания интервала: {e}")

    try:
        while is_spamming:
            if not is_spamming:
                break

            for phone in spamming_accounts:
                if not is_spamming:
                    break
                if phone not in active_sessions or phone not in message_lists or not message_lists[phone]:
                    logger.warning(f"Пропуск {phone}: нет активной сессии или сообщений")
                    continue
                if phone not in selected_groups or not selected_groups[phone]:
                    logger.warning(f"Пропуск {phone}: нет выбранных групп")
                    continue
                if phone not in clients:
                    logger.warning(f"Пропуск {phone}: клиент недоступен")
                    continue

                client = clients[phone]
                msg = random.choice(message_lists[phone])
                text = msg.get("text")
                photo_path = msg.get("photo")
                custom_emojis = msg.get("custom_emojis", [])
                entities = msg.get("entities", [])

                if spamming_mode == "simultaneous":
                    for group in selected_groups[phone]:
                        if not is_spamming:
                            break
                        group_id = group["id"]
                        group_title = group["title"]
                        success, has_photo = await send_message(phone, client, group_id, group_title, text, photo_path, custom_emojis, entities)
                        if success:
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            save_report(timestamp, phone, group_title, has_photo)
                            logger.info(f"Отчёт сохранён для {phone} в {group_title}")
                            await sleep_after_success()
                        else:
                            logger.error(f"Не удалось отправить сообщение от {phone} в {group_title}")

                elif spamming_mode == "sequential":
                    group = random.choice(selected_groups[phone])
                    group_id = group["id"]
                    group_title = group["title"]
                    success, has_photo = await send_message(phone, client, group_id, group_title, text, photo_path, custom_emojis, entities)
                    if success:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        save_report(timestamp, phone, group_title, has_photo)
                        logger.info(f"Отчёт сохранён для {phone} в {group_title}")
                        await sleep_after_success()
                    else:
                        logger.error(f"Не удалось отправить сообщение от {phone} в {group_title}")

                elif spamming_mode == "random":
                    num_groups = max(1, len(selected_groups[phone]) // 2)
                    num_to_send = random.randint(1, num_groups)
                    groups_to_send = random.sample(selected_groups[phone], num_to_send)
                    for group in groups_to_send:
                        if not is_spamming:
                            break
                        group_id = group["id"]
                        group_title = group["title"]
                        success, has_photo = await send_message(phone, client, group_id, group_title, text, photo_path, custom_emojis, entities)
                        if success:
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            save_report(timestamp, phone, group_title, has_photo)
                            logger.info(f"Отчёт сохранён для {phone} в {group_title}")
                            await sleep_after_success()
                        else:
                            logger.error(f"Не удалось отправить сообщение от {phone} в {group_title}")
            # В режиме "только при успехе" дополнительная задержка в конце цикла не требуется
    except asyncio.CancelledError:
        logger.info("Задача рассылки была отменена")
    except Exception as e:
        logger.error(f"Неожиданная ошибка в start_spamming: {str(e)}")
    finally:
        await disconnect_clients()
        is_spamming = False
        logger.info("Рассылка остановлена")

# Регистрация обработчиков
def register_handlers(dp, bot):
    # Команда /start
    @dp.message(Command("start"))
    async def start_command(message: types.Message):
        try:
            status = await get_status_text()
            await message.answer(
                f"{status}\n\n*Главное меню*",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            logger.error(f"Ошибка в start_command: {e}")
            await message.answer(
                "❌ Произошла ошибка при запуске бота.",
                parse_mode="MarkdownV2"
            )

    # Показать статус
    @dp.callback_query(lambda c: c.data == "show_status")
    async def show_status(callback: types.CallbackQuery):
        status = await get_status_text()
        await callback.message.edit_text(
            status,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
                ]
            ),
            parse_mode="MarkdownV2"
        )
        logger.info("Отображён статус бота")

    # Меню настроек
    @dp.callback_query(lambda c: c.data == "settings_menu")
    async def settings_menu(callback: types.CallbackQuery):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Очистить данные", callback_data="clear_data")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
        ])
        await callback.message.edit_text(
            "*Настройки*",
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    # Управление аккаунтами
    @dp.callback_query(lambda c: c.data == "manage_accounts")
    async def manage_accounts(callback: types.CallbackQuery):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить", callback_data="add_account")],
            [InlineKeyboardButton(text="📋 Список", callback_data="show_accounts")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data="delete_account")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
        ])
        await callback.message.edit_text(
            "*Управление аккаунтами*",
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    # Добавление аккаунта
    @dp.callback_query(lambda c: c.data == "add_account")
    async def add_account(callback: types.CallbackQuery, state: FSMContext):
        await callback.message.edit_text(
            escape_markdown_v2("📱 Введите номер телефона (+1234567890):"),
            reply_markup=None,
            parse_mode="MarkdownV2"
        )
        await state.set_state(AddAccount.WaitingPhone)

    @dp.message(AddAccount.WaitingPhone)
    async def process_phone(message: types.Message, state: FSMContext):
        phone = message.text.strip()
        if not phone.startswith("+") or not phone[1:].isdigit():
            status = await get_status_text()
            await message.answer(
                f"{status}\n\n{escape_markdown_v2('❌ Неверный формат номера. Используйте +1234567890:')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            await state.clear()
            return
        await state.update_data(phone=phone)
        session_path = os.path.join(SESSION_DIR, phone)
        client = TelegramClient(session_path, API_ID, API_HASH)
        try:
            await client.connect()
            sent_code = await client.send_code_request(phone)
            await state.update_data(phone_code_hash=sent_code.phone_code_hash, client=client)
            await message.answer(
                escape_markdown_v2("🔑 Введите код из Telegram:"),
                reply_markup=None,
                parse_mode="MarkdownV2"
            )
            await state.set_state(AddAccount.WaitingCode)
        except Exception as e:
            status = await get_status_text()
            await message.answer(
                f"{status}\n\n{escape_markdown_v2(f'❌ Ошибка: {str(e)}')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            await state.clear()

    @dp.message(AddAccount.WaitingCode)
    async def process_code(message: types.Message, state: FSMContext):
        data = await state.get_data()
        phone = data["phone"]
        phone_code_hash = data.get("phone_code_hash")
        code = message.text.strip()
        client = data.get("client")
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            if await client.is_user_authorized():
                await finish_account_setup(client, phone, message, state)
            else:
                status = await get_status_text()
                await message.answer(
                    f"{status}\n\n{escape_markdown_v2('❌ Не удалось авторизоваться')}",
                    reply_markup=await get_main_inline_keyboard(),
                    parse_mode="MarkdownV2"
                )
                await state.clear()
        except SessionPasswordNeededError:
            await message.answer(
                escape_markdown_v2("🔐 Введите пароль 2FA:"),
                reply_markup=None,
                parse_mode="MarkdownV2"
            )
            await state.set_state(AddAccount.WaitingPassword)
        except Exception as e:
            status = await get_status_text()
            await message.answer(
                f"{status}\n\n{escape_markdown_v2(f'❌ Ошибка: {str(e)}')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            await state.clear()

    @dp.message(AddAccount.WaitingPassword)
    async def process_password(message: types.Message, state: FSMContext):
        data = await state.get_data()
        phone = data["phone"]
        password = message.text.strip()
        client = data.get("client")
        try:
            await client.sign_in(phone=phone, password=password)
            if await client.is_user_authorized():
                await finish_account_setup(client, phone, message, state)
            else:
                await message.answer(
                    escape_markdown_v2("❌ Неверный пароль. Введите снова:"),
                    reply_markup=None,
                    parse_mode="MarkdownV2"
                )
                await state.set_state(AddAccount.WaitingPassword)
        except Exception as e:
            status = await get_status_text()
            await message.answer(
                f"{status}\n\n{escape_markdown_v2(f'❌ Ошибка: {str(e)}')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            await state.clear()

    async def finish_account_setup(client: TelegramClient, phone: str, message: types.Message, state: FSMContext):
        global active_sessions, available_groups
        available_groups[phone] = []
        try:
            async for dialog in client.iter_dialogs():
                if dialog.is_group or dialog.is_channel:
                    available_groups[phone].append((dialog.name or str(dialog.id), dialog.entity))
            save_available_groups(phone, available_groups[phone])
            me = await client.get_me()
            username = me.username or phone
            update_account_username(phone, username)
            if phone not in active_sessions:
                active_sessions.append(phone)
                if not save_active_sessions():
                    raise Exception("Не удалось сохранить активные сессии в базе данных")
            load_selected_groups(phone)
            load_messages(phone)
            status = await get_status_text()
            success_message = escape_markdown_v2(f"✅ Аккаунт {phone} добавлен") + "\n" + escape_markdown_v2(f"👥 Групп: {len(available_groups[phone])}")
            await message.answer(
                f"{status}\n\n{success_message}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            status = await get_status_text()
            error_message = escape_markdown_v2(f"❌ Ошибка: {str(e)}")
            await message.answer(
                f"{status}\n\n{error_message}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
        finally:
            await client.disconnect()
            await state.clear()

    # Показать аккаунты
    @dp.callback_query(lambda c: c.data == "show_accounts")
    async def show_accounts(callback: types.CallbackQuery):
        if not active_sessions:
            status = await get_status_text()
            await callback.message.edit_text(
                f"{status}\n\n{escape_markdown_v2('❌ Нет аккаунтов')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            return
        accounts_text = "\n".join([escape_markdown_v2(f"📱 {phone}") for phone in active_sessions])
        await callback.message.edit_text(
            f"*Активные аккаунты*:\n{accounts_text}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="manage_accounts")]
            ]),
            parse_mode="MarkdownV2"
        )

    # Удаление аккаунта
    @dp.callback_query(lambda c: c.data == "delete_account")
    async def delete_account(callback: types.CallbackQuery):
        if not active_sessions:
            status = await get_status_text()
            await callback.message.edit_text(
                f"{status}\n\n{escape_markdown_v2('❌ Нет аккаунтов')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            return
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=phone, callback_data=f"delete_{phone}")]
            for phone in active_sessions
        ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="manage_accounts")]])
        await callback.message.edit_text(
            escape_markdown_v2("🗑 Выберите аккаунт для удаления:"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data.startswith("delete_"))
    async def process_delete_account(callback: types.CallbackQuery):
        phone = callback.data.replace("delete_", "")
        global active_sessions, available_groups, selected_groups, message_lists
        if phone in active_sessions:
            active_sessions.remove(phone)
            available_groups.pop(phone, None)
            selected_groups.pop(phone, None)
            if phone in message_lists:
                for msg in message_lists[phone]:
                    if msg["photo"]:
                        try:
                            os.remove(msg["photo"])
                            logger.info(f"Удалён файл фото: {msg['photo']}")
                        except FileNotFoundError:
                            logger.warning(f"Файл фото {msg['photo']} не найден при удалении")
                message_lists.pop(phone, None)
            session_file = os.path.join(SESSION_DIR, f"{phone}.session")
            try:
                os.remove(session_file)
                logger.info(f"Удалён файл сессии для {phone}")
            except FileNotFoundError:
                logger.warning(f"Файл сессии {session_file} не найден при удалении")
            if not delete_account_data(phone):
                logger.error(f"Не удалось удалить данные для {phone} из базы данных")
            save_active_sessions()
            logger.info(f"Аккаунт {phone} удалён")
        status = await get_status_text()
        success_message = escape_markdown_v2(f"✅ Аккаунт {phone} удалён")
        await callback.message.edit_text(
            f"{status}\n\n{success_message}",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )

    # Управление сообщениями
    @dp.callback_query(lambda c: c.data == "manage_messages")
    async def manage_messages(callback: types.CallbackQuery):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить", callback_data="add_message")],
            [InlineKeyboardButton(text="📋 Просмотр", callback_data="view_messages")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
        ])
        await callback.message.edit_text(
            "*Управление сообщениями*",
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data == "add_message")
    async def add_message(callback: types.CallbackQuery, state: FSMContext):
        if not active_sessions:
            status = await get_status_text()
            await callback.message.edit_text(
                f"{status}\n\n{escape_markdown_v2('❌ Добавьте аккаунт')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            return
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=phone, callback_data=f"add_msg_{phone}")]
            for phone in active_sessions
        ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="manage_messages")]])
        await callback.message.edit_text(
            escape_markdown_v2("📱 Выберите аккаунт:"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data.startswith("add_msg_"))
    async def process_add_message_account(callback: types.CallbackQuery, state: FSMContext):
        phone = callback.data.replace("add_msg_", "")
        await state.update_data(phone=phone)
        await callback.message.edit_text(
            escape_markdown_v2("📝 Отправьте сообщение (текст с фото или только текст):"),
            reply_markup=None,
            parse_mode="MarkdownV2"
        )
        await state.set_state(AddMessage.WaitingText)

    @dp.message(AddMessage.WaitingText)
    async def process_message_text(message: types.Message, state: FSMContext):
        data = await state.get_data()
        phone = data["phone"]
        text = message.text  # Используем обычный текст, не html_text
        photo_path = None
        custom_emojis = []
        entities_data = []

        if message.entities:
            for entity in message.entities:
                entity_info = {
                    "type": entity.type,
                    "offset": entity.offset,
                    "length": entity.length
                }
                # Сохраняем кастомные эмодзи с их ID
                if entity.type == "custom_emoji" and hasattr(entity, "custom_emoji_id"):
                    entity_info["custom_emoji_id"] = int(entity.custom_emoji_id)
                    custom_emojis.append(entity_info)
                entities_data.append(entity_info)

        if message.photo:
            photo = message.photo[-1]
            file = await bot.get_file(photo.file_id)
            photo_path = os.path.join(PHOTO_DIR, f"{phone}_{photo.file_id}.jpg")
            await bot.download_file(file.file_path, photo_path)
            try:
                os.chmod(photo_path, 0o644)
                logger.info(f"Сохранено фото для {phone}: {photo_path}")
            except Exception as e:
                logger.error(f"Не удалось установить права для {photo_path}: {e}")

        if not text and not photo_path:
            await message.answer(
                escape_markdown_v2("❌ Отправьте текст, фото или и то, и другое!"),
                reply_markup=None,
                parse_mode="MarkdownV2"
            )
            return

        if phone not in message_lists:
            message_lists[phone] = []
        message_lists[phone].append({
            "text": text,
            "photo": photo_path,
            "entities": entities_data,
            "custom_emojis": custom_emojis
        })
        save_messages(phone)
        status = await get_status_text()
        await message.answer(
            f"{status}\n\n{escape_markdown_v2('✅ Сообщение добавлено')}",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )
        await state.clear()

    # Просмотр сообщений
    @dp.callback_query(lambda c: c.data == "view_messages")
    async def view_messages(callback: types.CallbackQuery):
        if not message_lists or all(len(messages) == 0 for messages in message_lists.values()):
            status = await get_status_text()
            await callback.message.edit_text(
                f"{status}\n\n{escape_markdown_v2('❌ Нет сообщений')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            return
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=phone, callback_data=f"view_msgs_{phone}")]
            for phone in message_lists
        ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="manage_messages")]])
        await callback.message.edit_text(
            escape_markdown_v2("📱 Выберите аккаунт:"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data.startswith("view_msgs_"))
    async def process_view_messages(callback: types.CallbackQuery):
        phone = callback.data.replace("view_msgs_", "")
        if phone not in message_lists or not message_lists[phone]:
            status = await get_status_text()
            await callback.message.edit_text(
                f"{status}\n\n{escape_markdown_v2(f'❌ Нет сообщений для {phone}')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            return
        
        messages_text = []
        for index, msg in enumerate(message_lists[phone]):
            text = msg.get("text", "")
            has_photo = bool(msg.get("photo"))
            message_info = f"✉️ Сообщение {index + 1}: {text if text else '<пусто>'}" + (" (с фото)" if has_photo else "")
            messages_text.append(escape_markdown_v2(message_info))
        
        messages_operation = "\n".join(messages_text)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🗑 Удалить сообщение {index + 1}", callback_data=f"del_msg_{phone}_{index}")]
            for index in range(len(message_lists[phone]))
        ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="manage_messages")])
        
        await callback.message.edit_text(
            f"*Сообщения для {escape_markdown_v2(phone)}*:\n{messages_operation}",
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    # Обработчик удаления сообщения
    @dp.callback_query(lambda c: c.data.startswith("del_msg_"))
    async def process_delete_message(callback: types.CallbackQuery):
        parts = callback.data.split("_")
        phone = parts[2]
        index = int(parts[3])
        if phone in message_lists and index < len(message_lists[phone]):
            msg = message_lists[phone].pop(index)
            if msg["photo"]:
                try:
                    os.remove(msg["photo"])
                    logger.info(f"Удалён файл фото: {msg['photo']}")
                except FileNotFoundError:
                    logger.warning(f"Файл фото {msg['photo']} не найден при удалении")
            save_messages(phone)
            logger.info(f"Сообщение удалено для {phone}")
        status = await get_status_text()
        await callback.message.edit_text(
            f"{status}\n\n{escape_markdown_v2('✅ Сообщение удалено')}",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )

    # Выбор групп
    @dp.callback_query(lambda c: c.data == "select_groups")
    async def select_groups(callback: types.CallbackQuery):
        if not active_sessions:
            status = await get_status_text()
            await callback.message.edit_text(
                f"{status}\n\n{escape_markdown_v2('❌ Добавьте аккаунт')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            return
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=phone, callback_data=f"sel_groups_{phone}_0")]
            for phone in active_sessions
        ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]])
        await callback.message.edit_text(
            escape_markdown_v2("📱 Выберите аккаунт:"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data.startswith("sel_groups_"))
    async def process_select_groups_account(callback: types.CallbackQuery):
        parts = callback.data.split("_")
        phone = parts[2]
        page = int(parts[3])
        
        load_available_groups(phone)
        
        if phone not in available_groups or not available_groups[phone]:
            status = await get_status_text()
            await callback.message.edit_text(
                f"{status}\n\n{escape_markdown_v2(f'❌ Нет групп для {phone}')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            return
        
        total_groups = len(available_groups[phone])
        total_pages = (total_groups + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE
        
        page = max(0, min(page, total_pages - 1))
        
        start_idx = page * GROUPS_PER_PAGE
        end_idx = min(start_idx + GROUPS_PER_PAGE, total_groups)
        current_groups = available_groups[phone][start_idx:end_idx]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        selected = selected_groups.get(phone, [])
        for name, entity in current_groups:
            is_selected = any(g["id"] == entity.id for g in selected)
            emoji = "✅" if is_selected else "⬜"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{emoji} {escape_markdown_v2(name)}",
                    callback_data=f"toggle_group_{phone}_{entity.id}_{page}"
                )
            ])
        
        # Кнопки "Выбрать все" и "Снять все"
        select_buttons = [
            InlineKeyboardButton(text="✅ Выбрать все", callback_data=f"select_all_page_{phone}_{page}"),
            InlineKeyboardButton(text="❌ Снять все", callback_data=f"deselect_all_page_{phone}_{page}")
        ]
        keyboard.inline_keyboard.append(select_buttons)
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sel_groups_{phone}_{page-1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"sel_groups_{phone}_{page+1}"))
        if nav_buttons:
            keyboard.inline_keyboard.append(nav_buttons)
        
        # Кнопка копирования выбора
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="📋 Скопировать выбор", callback_data=f"copy_selection_{phone}")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ К списку аккаунтов", callback_data="select_groups")])
        
        await callback.message.edit_text(
            escape_markdown_v2(f"👥 Группы для {phone} (Страница {page + 1} из {total_pages}):"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data.startswith("toggle_group_"))
    async def toggle_group(callback: types.CallbackQuery):
        parts = callback.data.split("_")
        phone = parts[2]
        group_id = int(parts[3])
        page = int(parts[4])
        
        if phone not in selected_groups:
            selected_groups[phone] = []
        group = next((g for g in available_groups[phone] if g[1].id == group_id), None)
        if not group:
            logger.warning(f"Группа с ID {group_id} не найдена для {phone}")
            return
        group_name = group[0]
        selected = selected_groups[phone]
        if any(g["id"] == group_id for g in selected):
            selected_groups[phone] = [g for g in selected if g["id"] != group_id]
        else:
            selected_groups[phone].append({"id": group_id, "title": group_name})
        save_selected_groups(phone)
        logger.info(f"Обновлен выбор групп для {phone}")
        
        total_groups = len(available_groups[phone])
        total_pages = (total_groups + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE
        page = max(0, min(page, total_pages - 1))
        start_idx = page * GROUPS_PER_PAGE
        end_idx = min(start_idx + GROUPS_PER_PAGE, total_groups)
        current_groups = available_groups[phone][start_idx:end_idx]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for name, entity in current_groups:
            is_selected = any(g["id"] == entity.id for g in selected_groups[phone])
            emoji = "✅" if is_selected else "⬜"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{emoji} {escape_markdown_v2(name)}",
                    callback_data=f"toggle_group_{phone}_{entity.id}_{page}"
                )
            ])
        
        # Кнопки "Выбрать все" и "Снять все"
        select_buttons = [
            InlineKeyboardButton(text="✅ Выбрать все", callback_data=f"select_all_page_{phone}_{page}"),
            InlineKeyboardButton(text="❌ Снять все", callback_data=f"deselect_all_page_{phone}_{page}")
        ]
        keyboard.inline_keyboard.append(select_buttons)
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sel_groups_{phone}_{page-1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"sel_groups_{phone}_{page+1}"))
        if nav_buttons:
            keyboard.inline_keyboard.append(nav_buttons)
        
        # Кнопка копирования выбора
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="📋 Скопировать выбор", callback_data=f"copy_selection_{phone}")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ К списку аккаунтов", callback_data="select_groups")])
        
        await callback.message.edit_text(
            escape_markdown_v2(f"👥 Группы для {phone} (Страница {page + 1} из {total_pages}):"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    # Выбрать все группы на странице
    @dp.callback_query(lambda c: c.data.startswith("select_all_page_"))
    async def select_all_page(callback: types.CallbackQuery):
        parts = callback.data.split("_")
        phone = parts[3]
        page = int(parts[4])
        
        count = select_all_page_groups(selected_groups, available_groups, phone, page, GROUPS_PER_PAGE)
        save_selected_groups(phone)
        logger.info(f"Выбрано {count} групп на странице {page} для {phone}")
        
        await callback.answer(f"✅ Выбрано {count} групп!", show_alert=False)
        
        # Перезагружаем страницу
        total_groups = len(available_groups[phone])
        total_pages = (total_groups + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE
        
        start_idx = page * GROUPS_PER_PAGE
        end_idx = min(start_idx + GROUPS_PER_PAGE, total_groups)
        current_groups = available_groups[phone][start_idx:end_idx]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for name, entity in current_groups:
            is_selected = any(g["id"] == entity.id for g in selected_groups[phone])
            emoji = "✅" if is_selected else "⬜"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{emoji} {escape_markdown_v2(name)}",
                    callback_data=f"toggle_group_{phone}_{entity.id}_{page}"
                )
            ])
        
        select_buttons = [
            InlineKeyboardButton(text="✅ Выбрать все", callback_data=f"select_all_page_{phone}_{page}"),
            InlineKeyboardButton(text="❌ Снять все", callback_data=f"deselect_all_page_{phone}_{page}")
        ]
        keyboard.inline_keyboard.append(select_buttons)
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sel_groups_{phone}_{page-1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"sel_groups_{phone}_{page+1}"))
        if nav_buttons:
            keyboard.inline_keyboard.append(nav_buttons)
        
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="📋 Скопировать выбор", callback_data=f"copy_selection_{phone}")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ К списку аккаунтов", callback_data="select_groups")])
        
        await callback.message.edit_text(
            escape_markdown_v2(f"👥 Группы для {phone} (Страница {page + 1} из {total_pages}):"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    # Снять выбор со всех групп на странице
    @dp.callback_query(lambda c: c.data.startswith("deselect_all_page_"))
    async def deselect_all_page(callback: types.CallbackQuery):
        parts = callback.data.split("_")
        phone = parts[3]
        page = int(parts[4])
        
        count = deselect_all_page_groups(selected_groups, available_groups, phone, page, GROUPS_PER_PAGE)
        save_selected_groups(phone)
        logger.info(f"Снято {count} групп на странице {page} для {phone}")
        
        await callback.answer(f"❌ Снято {count} групп!", show_alert=False)
        
        # Перезагружаем страницу (аналогично select_all_page)
        total_groups = len(available_groups[phone])
        total_pages = (total_groups + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE
        
        start_idx = page * GROUPS_PER_PAGE
        end_idx = min(start_idx + GROUPS_PER_PAGE, total_groups)
        current_groups = available_groups[phone][start_idx:end_idx]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for name, entity in current_groups:
            is_selected = any(g["id"] == entity.id for g in selected_groups[phone])
            emoji = "✅" if is_selected else "⬜"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{emoji} {escape_markdown_v2(name)}",
                    callback_data=f"toggle_group_{phone}_{entity.id}_{page}"
                )
            ])
        
        select_buttons = [
            InlineKeyboardButton(text="✅ Выбрать все", callback_data=f"select_all_page_{phone}_{page}"),
            InlineKeyboardButton(text="❌ Снять все", callback_data=f"deselect_all_page_{phone}_{page}")
        ]
        keyboard.inline_keyboard.append(select_buttons)
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sel_groups_{phone}_{page-1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"sel_groups_{phone}_{page+1}"))
        if nav_buttons:
            keyboard.inline_keyboard.append(nav_buttons)
        
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="📋 Скопировать выбор", callback_data=f"copy_selection_{phone}")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ К списку аккаунтов", callback_data="select_groups")])
        
        await callback.message.edit_text(
            escape_markdown_v2(f"👥 Группы для {phone} (Страница {page + 1} из {total_pages}):"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    # Меню копирования выбора
    @dp.callback_query(lambda c: c.data.startswith("copy_selection_"))
    async def copy_selection_menu(callback: types.CallbackQuery):
        from_phone = callback.data.split("_")[2]
        
        if from_phone not in selected_groups or not selected_groups[from_phone]:
            await callback.answer("❌ У этого аккаунта нет выбранных групп", show_alert=True)
            return
        
        similar_accounts = get_accounts_with_similar_groups(
            from_phone, selected_groups, available_groups, active_sessions
        )
        
        if not similar_accounts:
            await callback.answer("❌ Нет других аккаунтов с похожими группами", show_alert=True)
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for phone, similarity, common, total in similar_accounts:
            button_text = f"{phone} ({similarity}% совпадений: {common}/{total})"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"do_copy_selection_{from_phone}_{phone}"
                )
            ])
        
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sel_groups_{from_phone}_0")])
        
        await callback.message.edit_text(
            escape_markdown_v2(f"📋 Скопировать выбор из {from_phone} на:"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    # Выполнить копирование выбора
    @dp.callback_query(lambda c: c.data.startswith("do_copy_selection_"))
    async def do_copy_selection(callback: types.CallbackQuery):
        parts = callback.data.split("_")
        from_phone = parts[3]
        to_phone = parts[4]
        
        copied, skipped = copy_selection_to_account(
            from_phone, to_phone, selected_groups, available_groups
        )
        
        save_selected_groups(to_phone)
        message = f"✅ Скопировано {copied} групп"
        if skipped > 0:
            message += f"\n⚠️ Пропущено {skipped} групп (нет у этого аккаунта)"
        
        await callback.answer(message, show_alert=True)
        logger.info(f"Скопировано {copied} групп с {from_phone} на {to_phone}")
        
        # Вернуться в меню выбора групп исходного аккаунта
        await callback.message.edit_text(
            escape_markdown_v2(f"📋 ✅ Выбор успешно скопирован! Выбрано {copied} групп"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад к выбору", callback_data=f"sel_groups_{from_phone}_0")],
                [InlineKeyboardButton(text="К аккаунтам", callback_data="select_groups")]
            ]),
            parse_mode="MarkdownV2"
        )

    # Показать отчёты
    @dp.callback_query(lambda c: c.data == "show_report")
    async def show_report(callback: types.CallbackQuery):
        reports = load_reports(limit=10)
        if not reports:
            status = await get_status_text()
            await callback.message.edit_text(
                f"{status}\n\n{escape_markdown_v2('📊 Нет отчётов. Попробуйте запустить рассылку.')}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
                ]),
                parse_mode="MarkdownV2"
            )
            logger.info("Отчёты не найдены в базе данных")
            return
        report_text = []
        for ts, phone, group, has_photo in reports:
            report_line = f"🕒 {ts}: {phone} → {group}" + (" (фото)" if has_photo else "")
            report_text.append(escape_markdown_v2(report_line))
        report_output = "\n".join(report_text)
        await callback.message.edit_text(
            f"*Последние отправки*:\n{report_output}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Очистить отчёты", callback_data="clear_reports_from_menu")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
            ]),
            parse_mode="MarkdownV2"
        )
        logger.info(f"Отображено {len(reports)} отчётов")

    # Очистка отчётов из меню отчётов
    @dp.callback_query(lambda c: c.data == "clear_reports_from_menu")
    async def clear_reports_from_menu(callback: types.CallbackQuery):
        clear_reports()
        logger.info("Все отчёты удалены из базы данных через меню отчётов")
        status = await get_status_text()
        await callback.message.edit_text(
            f"{status}\n\n{escape_markdown_v2('✅ Отчёты очищены')}",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )

    # Запуск рассылки
    @dp.callback_query(lambda c: c.data == "start_spamming")
    async def start_spamming_selection(callback: types.CallbackQuery, state: FSMContext):
        global is_spamming
        if is_spamming:
            await callback.message.edit_text(
                escape_markdown_v2("❌ Рассылка уже запущена. Остановите текущую рассылку."),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🛑 Остановить", callback_data="stop_spamming")],
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
                ]),
                parse_mode="MarkdownV2"
            )
            return
        if not active_sessions:
            status = await get_status_text()
            await callback.message.edit_text(
                f"{status}\n\n{escape_markdown_v2('❌ Добавьте аккаунт')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            return
        await state.update_data(selected_accounts=[])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for phone in active_sessions:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"📱 {phone}",
                    callback_data=f"toggle_spam_account_{phone}"
                )
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🚀 Запустить", callback_data="spam_accounts_done")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_to_main")])
        await callback.message.edit_text(
            escape_markdown_v2("📱 Выберите аккаунты для рассылки:"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )
        await state.set_state(StartSpamming.SelectingAccounts)

    @dp.callback_query(lambda c: c.data.startswith("toggle_spam_account_"), StartSpamming.SelectingAccounts)
    async def toggle_spam_account(callback: types.CallbackQuery, state: FSMContext):
        phone = callback.data.replace("toggle_spam_account_", "")
        data = await state.get_data()
        selected_accounts = data.get("selected_accounts", [])
        if phone in selected_accounts:
            selected_accounts.remove(phone)
        else:
            selected_accounts.append(phone)
        await state.update_data(selected_accounts=selected_accounts)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for p in active_sessions:
            emoji = "✅" if p in selected_accounts else "⬜"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{emoji} {p}",
                    callback_data=f"toggle_spam_account_{p}"
                )
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🚀 Запустить", callback_data="spam_accounts_done")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_to_main")])
        await callback.message.edit_text(
            escape_markdown_v2("📱 Выберите аккаунты для рассылки:"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data == "spam_accounts_done", StartSpamming.SelectingAccounts)
    async def spam_accounts_done(callback: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        selected_accounts = data.get("selected_accounts", [])
        if not selected_accounts:
            status = await get_status_text()
            await callback.message.edit_text(
                f"{status}\n\n{escape_markdown_v2('❌ Выберите хотя бы один аккаунт')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            await state.clear()
            return
        await state.update_data(selected_accounts=selected_accounts)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Сразу во все группы", callback_data="spam_mode_simultaneous")],
            [InlineKeyboardButton(text="🔄 По очереди в одну группу", callback_data="spam_mode_sequential")],
            [InlineKeyboardButton(text="🎲 Случайный выбор с приоритетом", callback_data="spam_mode_random")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_to_select_accounts")]
        ])
        await callback.message.edit_text(
            escape_markdown_v2("📤 Выберите режим рассылки:"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )
        await state.set_state(StartSpamming.SelectingMode)

    @dp.callback_query(lambda c: c.data.startswith("spam_mode_"), StartSpamming.SelectingMode)
    async def spam_mode_selected(callback: types.CallbackQuery, state: FSMContext):
        mode = callback.data.replace("spam_mode_", "")
        await state.update_data(mode=mode)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_to_select_mode")]
        ])
        await callback.message.edit_text(
            escape_markdown_v2("⏱ Введите интервал (сек):"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )
        await state.set_state(StartSpamming.SettingInterval)

    @dp.message(StartSpamming.SettingInterval)
    async def process_spam_interval(message: types.Message, state: FSMContext):
        global spamming_accounts, spamming_mode, spamming_interval, spamming_task, is_spamming
        if not message.text.isdigit():
            status = await get_status_text()
            await message.answer(
                f"{status}\n\n{escape_markdown_v2('❌ Введите число')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            await state.clear()
            return
        interval = int(message.text)
        if interval < 1:
            status = await get_status_text()
            await message.answer(
                f"{status}\n\n{escape_markdown_v2('❌ Интервал должен быть больше 0')}",
                reply_markup=await get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
            await state.clear()
            return
        data = await state.get_data()
        spamming_accounts = data["selected_accounts"]
        spamming_mode = data["mode"]
        spamming_interval = interval
        is_spamming = True
        await setup_clients()
        spamming_task = asyncio.create_task(start_spamming())
        status = await get_status_text()
        await message.answer(
            f"{status}\n\n{escape_markdown_v2('✅ Рассылка запущена')}",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )
        await state.clear()

    @dp.callback_query(lambda c: c.data == "stop_spamming")
    async def stop_spamming(callback: types.CallbackQuery):
        global spamming_task, spamming_accounts, spamming_mode, spamming_interval, is_spamming
        if is_spamming:
            is_spamming = False
            if spamming_task:
                await spamming_task
            spamming_accounts = []
            spamming_mode = None
            spamming_interval = None
            spamming_task = None
            logger.info("Рассылка остановлена")
        status = await get_status_text()
        await callback.message.edit_text(
            f"{status}\n\n{escape_markdown_v2('🛑 Рассылка остановлена')}",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )

    # Обработчики кнопки "Отмена"
    @dp.callback_query(lambda c: c.data == "cancel_to_main")
    async def cancel_to_main(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        status = await get_status_text()
        await callback.message.edit_text(
            f"{status}\n\n*Главное меню*",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data == "cancel_to_select_accounts", StartSpamming.SelectingMode)
    async def cancel_to_select_accounts(callback: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        selected_accounts = data.get("selected_accounts", [])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for p in active_sessions:
            emoji = "✅" if p in selected_accounts else "⬜"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{emoji} {p}",
                    callback_data=f"toggle_spam_account_{p}"
                )
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🚀 Запустить", callback_data="spam_accounts_done")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_to_main")])
        await callback.message.edit_text(
            escape_markdown_v2("📱 Выберите аккаунты для рассылки:"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )
        await state.set_state(StartSpamming.SelectingAccounts)

    @dp.callback_query(lambda c: c.data == "cancel_to_select_mode", StartSpamming.SettingInterval)
    async def cancel_to_select_mode(callback: types.CallbackQuery, state: FSMContext):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Сразу во все группы", callback_data="spam_mode_simultaneous")],
            [InlineKeyboardButton(text="🔄 По очереди в одну группу", callback_data="spam_mode_sequential")],
            [InlineKeyboardButton(text="🎲 Случайный выбор с приоритетом", callback_data="spam_mode_random")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_to_select_accounts")]
        ])
        await callback.message.edit_text(
            escape_markdown_v2("📤 Выберите режим рассылки:"),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )
        await state.set_state(StartSpamming.SelectingMode)

    # Очистка данных
    @dp.callback_query(lambda c: c.data == "clear_data")
    async def clear_data(callback: types.CallbackQuery):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Аккаунты", callback_data="clear_accounts")],
            [InlineKeyboardButton(text="🗑 Сообщения", callback_data="clear_messages")],
            [InlineKeyboardButton(text="🗑 Группы", callback_data="clear_groups")],
            [InlineKeyboardButton(text="🗑 Отчёты", callback_data="clear_reports")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
        ])
        await callback.message.edit_text(
            "*Очистка данных*",
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data == "clear_accounts")
    async def clear_accounts(callback: types.CallbackQuery):
        global active_sessions, available_groups, selected_groups, message_lists
        for phone in active_sessions[:]:
            session_file = os.path.join(SESSION_DIR, f"{phone}.session")
            try:
                os.remove(session_file)
                logger.info(f"Удалён файл сессии для {phone}")
            except FileNotFoundError:
                logger.warning(f"Файл сессии {session_file} не найден при удалении")
            if phone in message_lists:
                for msg in message_lists[phone]:
                    if msg["photo"]:
                        try:
                            os.remove(msg["photo"])
                            logger.info(f"Удалён файл фото: {msg['photo']}")
                        except FileNotFoundError:
                            logger.warning(f"Файл фото {msg['photo']} не найден при удалении")
            delete_account_data(phone)
        active_sessions.clear()
        available_groups.clear()
        selected_groups.clear()
        message_lists.clear()
        save_active_sessions()
        logger.info("Все аккаунты удалены из базы данных")
        status = await get_status_text()
        await callback.message.edit_text(
            f"{status}\n\n{escape_markdown_v2('✅ Аккаунты очищены')}",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data == "clear_messages")
    async def clear_messages(callback: types.CallbackQuery):
        global message_lists
        for phone in list(message_lists.keys()):
            for msg in message_lists[phone]:
                if msg["photo"]:
                    try:
                        os.remove(msg["photo"])
                        logger.info(f"Удалён файл фото: {msg['photo']}")
                    except FileNotFoundError:
                        logger.warning(f"Файл фото {msg['photo']} не найден при удалении")
            save_messages(phone)
        message_lists.clear()
        logger.info("Все сообщения удалены из базы данных")
        status = await get_status_text()
        await callback.message.edit_text(
            f"{status}\n\n{escape_markdown_v2('✅ Сообщения очищены')}",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data == "clear_groups")
    async def clear_groups(callback: types.CallbackQuery):
        global selected_groups
        selected_groups.clear()
        for phone in active_sessions:
            save_selected_groups(phone)
        logger.info("Все группы удалены из базы данных")
        status = await get_status_text()
        await callback.message.edit_text(
            f"{status}\n\n{escape_markdown_v2('✅ Группы очищены')}",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data == "clear_reports")
    async def clear_reports_handler(callback: types.CallbackQuery):
        clear_reports()
        logger.info("Все отчёты удалены из базы данных")
        status = await get_status_text()
        await callback.message.edit_text(
            f"{status}\n\n{escape_markdown_v2('✅ Отчёты очищены')}",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )

    @dp.callback_query(lambda c: c.data == "back_to_main")
    async def back_to_main(callback: types.CallbackQuery):
        status = await get_status_text()
        await callback.message.edit_text(
            f"{status}\n\n*Главное меню*",
            reply_markup=await get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )