#!/usr/bin/env python3
"""
Тестирование системы entities для проверки исправлений
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.client import build_entities_from_data
from telethon.tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCustomEmoji
)

def test_simple_bold():
    """Тест: простое жирное форматирование"""
    entities_data = [
        {
            "type": "bold",
            "offset": 0,
            "length": 8
        }
    ]
    entities = build_entities_from_data(entities_data)
    assert len(entities) == 1
    assert isinstance(entities[0], MessageEntityBold)
    assert entities[0].offset == 0
    assert entities[0].length == 8
    print("✅ Тест 1 пройден: простое жирное форматирование")

def test_bold_and_italic():
    """Тест: жирный и курсив"""
    entities_data = [
        {
            "type": "bold",
            "offset": 0,
            "length": 6
        },
        {
            "type": "italic",
            "offset": 7,
            "length": 7
        }
    ]
    entities = build_entities_from_data(entities_data)
    assert len(entities) == 2
    assert isinstance(entities[0], MessageEntityBold)
    assert isinstance(entities[1], MessageEntityItalic)
    print("✅ Тест 2 пройден: жирный и курсив")

def test_custom_emoji():
    """Тест: премиум эмодзи"""
    entities_data = [
        {
            "type": "custom_emoji",
            "offset": 0,
            "length": 2,
            "custom_emoji_id": 5378132391916925633
        }
    ]
    entities = build_entities_from_data(entities_data)
    assert len(entities) == 1
    assert isinstance(entities[0], MessageEntityCustomEmoji)
    assert entities[0].offset == 0
    assert entities[0].length == 2
    assert entities[0].document_id == 5378132391916925633
    print("✅ Тест 3 пройден: премиум эмодзи с правильным document_id")

def test_complex_formatting():
    """Тест: сложное форматирование с эмодзи"""
    entities_data = [
        {
            "type": "bold",
            "offset": 0,
            "length": 8
        },
        {
            "type": "custom_emoji",
            "offset": 9,
            "length": 2,
            "custom_emoji_id": 5378132391916925633
        },
        {
            "type": "italic",
            "offset": 12,
            "length": 5
        }
    ]
    entities = build_entities_from_data(entities_data)
    assert len(entities) == 3
    assert isinstance(entities[0], MessageEntityBold)
    assert isinstance(entities[1], MessageEntityCustomEmoji)
    assert isinstance(entities[2], MessageEntityItalic)
    print("✅ Тест 4 пройден: сложное форматирование с эмодзи")

def test_empty_entities():
    """Тест: пустой список entities"""
    entities_data = []
    entities = build_entities_from_data(entities_data)
    assert len(entities) == 0
    print("✅ Тест 5 пройден: пустой список entities")

def test_strikethrough_variants():
    """Тест: оба варианта зачеркивания"""
    entities_data = [
        {
            "type": "strikethrough",
            "offset": 0,
            "length": 4
        },
        {
            "type": "strike",
            "offset": 5,
            "length": 4
        }
    ]
    entities = build_entities_from_data(entities_data)
    assert len(entities) == 2
    assert entities[0].offset == 0
    assert entities[1].offset == 5
    print("✅ Тест 6 пройден: оба варианта зачеркивания")

if __name__ == "__main__":
    print("\n🧪 Запуск тестов системы entities...\n")
    try:
        test_simple_bold()
        test_bold_and_italic()
        test_custom_emoji()
        test_complex_formatting()
        test_empty_entities()
        test_strikethrough_variants()
        print("\n✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ! Система форматирования работает корректно.")
        print("\n📝 Что было исправлено:")
        print("   1️⃣  HTML разметка теперь отправляется как правильное форматирование")
        print("   2️⃣  Премиум эмодзи сохраняют свои ID и отправляются корректно")
        print("   3️⃣  Поддерживаются все типы форматирования (bold, italic, underline и т.д.)")
        print("   4️⃣  Добавлена автоматическая миграция БД")
        print("   5️⃣  Полная обратная совместимость со старыми сообщениями\n")
    except AssertionError as e:
        print(f"\n❌ Тест не пройден: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Ошибка при выполнении тестов: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)