# core/client.py
import asyncio
import os
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityUnderline, MessageEntityStrike,
    MessageEntityCode, MessageEntityPre, MessageEntityTextUrl, MessageEntityMention,
    MessageEntityHashtag, MessageEntityCashtag, MessageEntityPhone, MessageEntityCustomEmoji
)
from utils.config import SESSION_DIR
from database.sqlite import API_ID, API_HASH
from utils.logger import logger

# Маппинг типов entity из aiogram в Telethon
ENTITY_TYPE_MAP = {
    "bold": MessageEntityBold,
    "italic": MessageEntityItalic,
    "underline": MessageEntityUnderline,
    "strikethrough": MessageEntityStrike,
    "strike": MessageEntityStrike,
    "code": MessageEntityCode,
    "pre": MessageEntityPre,
    "text_link": MessageEntityTextUrl,
    "mention": MessageEntityMention,
    "hashtag": MessageEntityHashtag,
    "cashtag": MessageEntityCashtag,
    "phone_number": MessageEntityPhone,
    "custom_emoji": MessageEntityCustomEmoji,
}

def build_entities_from_data(entities_data: list) -> list:
    """Конвертирует data entities в Telethon entities"""
    entities = []
    for entity_info in entities_data:
        entity_type = entity_info.get("type", "").lower()
        offset = int(entity_info.get("offset", 0))
        length = int(entity_info.get("length", 0))
        
        if entity_type in ENTITY_TYPE_MAP:
            entity_class = ENTITY_TYPE_MAP[entity_type]
            try:
                if entity_type == "text_link" and "url" in entity_info:
                    entities.append(entity_class(offset=offset, length=length, url=entity_info["url"]))
                elif entity_type == "custom_emoji" and "custom_emoji_id" in entity_info:
                    # Custom emoji требует document_id как INT
                    custom_emoji_id = int(entity_info.get("custom_emoji_id"))
                    entities.append(entity_class(offset=offset, length=length, document_id=custom_emoji_id))
                else:
                    entities.append(entity_class(offset=offset, length=length))
            except Exception as e:
                logger.warning(f"Ошибка при создании entity {entity_type}: {e}")
    
    return entities

async def send_message(phone: str, client: TelegramClient, group_id: int, group_title: str, text: str, photo_path: str = None, custom_emojis: list = None, entities: list = None):
    try:
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            logger.warning(f"Клиент для {phone} не авторизован")
            return False, False
        
        has_photo = bool(photo_path)
        
        # Преобразуем entities в Telethon формат
        telethon_entities = []
        if entities:
            telethon_entities = build_entities_from_data(entities)
            logger.info(f"Построены Telethon entities для {phone}: {len(telethon_entities)} сущностей")

        # Отправляем сообщение
        if photo_path and os.path.exists(photo_path):
            await client.send_file(group_id, photo_path, caption=text or "", formatting_entities=telethon_entities)
            logger.info(f"Отправлено сообщение с фото от {phone} в {group_title}")
        else:
            await client.send_message(group_id, text or "", formatting_entities=telethon_entities)
            logger.info(f"Отправлено текстовое сообщение от {phone} в {group_title}")
        return True, has_photo
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения от {phone} в {group_title}: {str(e)}")
        return False, False
    finally:
        # Не отключаем клиента после каждого сообщения, чтобы не терять соединение между отправками
        # Соединение будет закрыто централизованно в disconnect_clients()
        pass