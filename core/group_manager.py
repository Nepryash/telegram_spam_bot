# core/group_manager.py
"""
Оптимизированный менеджер групп:
- Кэширование групп в памяти
- Функция "Выбрать все" на странице
- Копирование выбора между аккаунтами
"""

# Глобальный кэш групп (не перезагружаем каждый раз)
_groups_cache = {}

def cache_groups(phone, groups):
    """Сохранить группы в кэш"""
    _groups_cache[phone] = groups

def get_cached_groups(phone):
    """Получить группы из кэша"""
    return _groups_cache.get(phone)

def clear_cache(phone=None):
    """Очистить кэш"""
    global _groups_cache
    if phone:
        _groups_cache.pop(phone, None)
    else:
        _groups_cache.clear()

def select_all_page_groups(selected_groups_dict, available_groups_dict, phone, page, groups_per_page):
    """
    Выбрать все группы на текущей странице
    
    Args:
        selected_groups_dict: Словарь выбранных групп
        available_groups_dict: Словарь доступных групп
        phone: Номер аккаунта
        page: Номер страницы
        groups_per_page: Групп на странице
    
    Returns:
        Количество выбранных групп на странице
    """
    if phone not in available_groups_dict or not available_groups_dict[phone]:
        return 0
    
    total_groups = len(available_groups_dict[phone])
    start_idx = page * groups_per_page
    end_idx = min(start_idx + groups_per_page, total_groups)
    current_page_groups = available_groups_dict[phone][start_idx:end_idx]
    
    if phone not in selected_groups_dict:
        selected_groups_dict[phone] = []
    
    selected_ids = {g["id"] for g in selected_groups_dict[phone]}
    
    for name, entity in current_page_groups:
        if entity.id not in selected_ids:
            selected_groups_dict[phone].append({"id": entity.id, "title": name})
    
    return len(current_page_groups)

def deselect_all_page_groups(selected_groups_dict, available_groups_dict, phone, page, groups_per_page):
    """
    Снять выбор со всех групп на текущей странице
    
    Args:
        selected_groups_dict: Словарь выбранных групп
        available_groups_dict: Словарь доступных групп
        phone: Номер аккаунта
        page: Номер страницы
        groups_per_page: Групп на странице
    
    Returns:
        Количество снятых групп на странице
    """
    if phone not in available_groups_dict or not available_groups_dict[phone]:
        return 0
    
    total_groups = len(available_groups_dict[phone])
    start_idx = page * groups_per_page
    end_idx = min(start_idx + groups_per_page, total_groups)
    current_page_groups = available_groups_dict[phone][start_idx:end_idx]
    
    if phone not in selected_groups_dict:
        return 0
    
    page_group_ids = {entity.id for name, entity in current_page_groups}
    
    deselected_count = 0
    new_selected = []
    for g in selected_groups_dict[phone]:
        if g["id"] not in page_group_ids:
            new_selected.append(g)
        else:
            deselected_count += 1
    
    selected_groups_dict[phone] = new_selected
    return deselected_count

def copy_selection_to_account(from_phone, to_phone, selected_groups_dict, available_groups_dict):
    """
    Скопировать выбор групп с одного аккаунта на другой
    Копирует только группы которые есть у обоих аккаунтов
    
    Args:
        from_phone: Аккаунт источник
        to_phone: Аккаунт назначение
        selected_groups_dict: Словарь выбранных групп
        available_groups_dict: Словарь доступных групп
    
    Returns:
        Кортеж (скопировано, пропущено) - количества групп
    """
    if from_phone not in selected_groups_dict or from_phone not in available_groups_dict:
        return 0, 0
    
    if to_phone not in available_groups_dict:
        return 0, 0
    
    # Получаем доступные группы у целевого аккаунта
    to_account_group_ids = {entity.id for name, entity in available_groups_dict[to_phone]}
    
    if to_phone not in selected_groups_dict:
        selected_groups_dict[to_phone] = []
    
    selected_ids_to = {g["id"] for g in selected_groups_dict[to_phone]}
    copied = 0
    skipped = 0
    
    # Копируем группы которые есть у обоих
    for group in selected_groups_dict[from_phone]:
        if group["id"] in to_account_group_ids:
            if group["id"] not in selected_ids_to:
                selected_groups_dict[to_phone].append(group)
                copied += 1
        else:
            skipped += 1
    
    return copied, skipped

def get_accounts_with_similar_groups(phone, selected_groups_dict, available_groups_dict, active_sessions):
    """
    Получить список аккаунтов которые имеют похожие группы
    
    Args:
        phone: Исходный аккаунт
        selected_groups_dict: Словарь выбранных групп
        available_groups_dict: Словарь доступных групп
        active_sessions: Список активных сессий
    
    Returns:
        Список (аккаунт, похожесть_в_процентах)
    """
    if phone not in available_groups_dict:
        return []
    
    source_group_ids = {entity.id for name, entity in available_groups_dict[phone]}
    similar_accounts = []
    
    for other_phone in active_sessions:
        if other_phone == phone or other_phone not in available_groups_dict:
            continue
        
        other_group_ids = {entity.id for name, entity in available_groups_dict[other_phone]}
        
        # Вычисляем похожесть как процент пересекающихся групп
        common = len(source_group_ids & other_group_ids)
        total = len(source_group_ids | other_group_ids)
        
        if total > 0:
            similarity = (common / total) * 100
            if similarity > 0:
                similar_accounts.append((other_phone, int(similarity), common, total))
    
    # Сортируем по похожести (больше похожих - выше в списке)
    similar_accounts.sort(key=lambda x: x[1], reverse=True)
    return similar_accounts