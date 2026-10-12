"""Shared game catalog. Keep item metadata in one place for UI and DB validation."""

SHOP_ITEMS = {'bed_cloud': {'name': 'Облачная лежанка', 'emoji': '🛏', 'slot': 'bed', 'price': 220},
 'bed_pink': {'name': 'Розовая лежанка', 'emoji': '🩷', 'slot': 'bed', 'price': 200},
 'bed_mint': {'name': 'Мятная лежанка', 'emoji': '💚', 'slot': 'bed', 'price': 200},
 'bed_bear': {'name': 'Лежанка-медвежонок', 'emoji': '🧸', 'slot': 'bed', 'price': 240},
 'bed_canopy': {'name': 'Лежанка-балдахин', 'emoji': '🎀', 'slot': 'bed', 'price': 260},
 'bed_strawberry': {'name': 'Клубничная лежанка', 'emoji': '🍓', 'slot': 'bed', 'price': 230},
 'bed_bunny': {'name': 'Лавандовая лежанка-зайка', 'emoji': '🐰', 'slot': 'bed', 'price': 235},
 'bed_flower': {'name': 'Лежанка-цветок', 'emoji': '🌸', 'slot': 'bed', 'price': 225},
 'rug_paw': {'name': 'Ковёр-лапка', 'emoji': '🐾', 'slot': 'rug', 'price': 170},
 'rug_heart': {'name': 'Ковёр-сердце', 'emoji': '💗', 'slot': 'rug', 'price': 170},
 'rug_cloud': {'name': 'Ковёр-облачко', 'emoji': '☁️', 'slot': 'rug', 'price': 180},
 'rug_round': {'name': 'Круглый кремовый ковёр', 'emoji': '🤍', 'slot': 'rug', 'price': 160},
 'rug_check': {'name': 'Шахматный ковёр', 'emoji': '🟩', 'slot': 'rug', 'price': 175},
 'rug_flower': {'name': 'Ковёр-цветок', 'emoji': '🌼', 'slot': 'rug', 'price': 180},
 'rug_star': {'name': 'Звёздный ковёр', 'emoji': '⭐', 'slot': 'rug', 'price': 180},
 'lamp_bow_pink': {'name': 'Лампа с бантом', 'emoji': '💡', 'slot': 'lamp', 'price': 210},
 'lamp_heart_beige': {'name': 'Лампа с сердечком', 'emoji': '💡', 'slot': 'lamp', 'price': 215},
 'lamp_bow_cream': {'name': 'Кремовая лампа с бантом', 'emoji': '💡', 'slot': 'lamp', 'price': 220},
 'lamp_bow_tall': {'name': 'Высокая лампа с бантом', 'emoji': '💡', 'slot': 'lamp', 'price': 225},
 'lamp_hearts': {'name': 'Лампа с сердечками', 'emoji': '💡', 'slot': 'lamp', 'price': 210},
 'lamp_soft_pink': {'name': 'Нежно-розовая лампа', 'emoji': '💡', 'slot': 'lamp', 'price': 205},
 'lamp_flowers': {'name': 'Лампа с цветами', 'emoji': '💡', 'slot': 'lamp', 'price': 230},
 'toy_mouse': {'name': 'Игрушечная мышка', 'emoji': '🐭', 'slot': 'toy', 'price': 95},
 'toy_yarn': {'name': 'Клубок пряжи', 'emoji': '🧶', 'slot': 'toy', 'price': 90},
 'toy_fish': {'name': 'Плюшевая рыбка', 'emoji': '🐟', 'slot': 'toy', 'price': 100},
 'toy_bunny': {'name': 'Плюшевый кролик', 'emoji': '🐰', 'slot': 'toy', 'price': 110},
 'toy_star': {'name': 'Плюшевая звёздочка', 'emoji': '⭐', 'slot': 'toy', 'price': 100},
 'toy_wand': {'name': 'Удочка-дразнилка', 'emoji': '🎣', 'slot': 'toy', 'price': 115},
 'bowl_beige_food': {'name': 'Кремовая миска с кормом', 'emoji': '🥣', 'slot': 'bowl', 'price': 90},
 'bowl_paw_food': {'name': 'Миска с лапкой', 'emoji': '🥣', 'slot': 'bowl', 'price': 95},
 'bowl_round_food': {'name': 'Круглая миска с кормом', 'emoji': '🥣', 'slot': 'bowl', 'price': 90},
 'bowl_water': {'name': 'Миска с водой', 'emoji': '💧', 'slot': 'bowl', 'price': 85},
 'bowl_blue': {'name': 'Голубая миска', 'emoji': '💙', 'slot': 'bowl', 'price': 85},
 'plant_monstera': {'name': 'Монстера', 'emoji': '🪴', 'slot': 'plant', 'price': 140},
 'plant_sansevieria': {'name': 'Сансевиерия', 'emoji': '🪴', 'slot': 'plant', 'price': 130},
 'plant_ficus': {'name': 'Фикус', 'emoji': '🪴', 'slot': 'plant', 'price': 135},
 'plant_cactus': {'name': 'Кактус с цветами', 'emoji': '🌵', 'slot': 'plant', 'price': 120},
 'plant_blossom': {'name': 'Цветущий горшок', 'emoji': '🌸', 'slot': 'plant', 'price': 145},
 'plant_aloe': {'name': 'Алоэ в сердце', 'emoji': '💚', 'slot': 'plant', 'price': 125},
 'plant_calathea': {'name': 'Калатея', 'emoji': '🪴', 'slot': 'plant', 'price': 145},
 'plant_bonsai': {'name': 'Бонсай', 'emoji': '🌳', 'slot': 'plant', 'price': 150},
 'decor_moon_frame': {'name': 'Рамка с луной', 'emoji': '🖼', 'slot': 'decor', 'price': 160},
 'decor_kitty_frame': {'name': 'Рамка с котёнком', 'emoji': '🖼', 'slot': 'decor', 'price': 165},
 'decor_cat_clock': {'name': 'Часы-котик', 'emoji': '⏰', 'slot': 'decor', 'price': 180},
 'decor_star_garland': {'name': 'Гирлянда со звёздами', 'emoji': '✨', 'slot': 'decor', 'price': 170},
 'decor_photo_garland': {'name': 'Фотогирлянда', 'emoji': '📸', 'slot': 'decor', 'price': 170},
 'decor_macrame_plant': {'name': 'Макраме с растением', 'emoji': '🪢', 'slot': 'decor', 'price': 175},
 'window_day': {'name': 'Окно: дневной сад', 'emoji': '🪟', 'slot': 'window', 'price': 120},
 'window_sunset': {'name': 'Окно: закат', 'emoji': '🌇', 'slot': 'window', 'price': 130},
 'window_night': {'name': 'Окно: лунная ночь', 'emoji': '🌙', 'slot': 'window', 'price': 130},
 'window_rain': {'name': 'Окно: дождь', 'emoji': '🌧', 'slot': 'window', 'price': 130},
 'window_winter': {'name': 'Окно: зима', 'emoji': '❄️', 'slot': 'window', 'price': 130},
 'window_spring': {'name': 'Окно: весна', 'emoji': '🌸', 'slot': 'window', 'price': 130},
 'window_autumn': {'name': 'Окно: осень', 'emoji': '🍁', 'slot': 'window', 'price': 130},
 'window_city_night': {'name': 'Окно: ночной город', 'emoji': '🌃', 'slot': 'window', 'price': 135}}

SLOT_NAMES = {'window': '🪟 Окно',
 'decor': '🖼 Декор',
 'plant': '🪴 Растение',
 'lamp': '💡 Лампа',
 'rug': '🧺 Ковёр',
 'bed': '🛏 Лежанка',
 'bowl': '🥣 Миска',
 'toy': '🧸 Игрушка'}

ITEM_SLOTS = {key: item["slot"] for key, item in SHOP_ITEMS.items()}


RARITY_INFO = {
    'common': {'name': 'Обычный', 'emoji': '⚪', 'rank': 0},
    'uncommon': {'name': 'Необычный', 'emoji': '🟢', 'rank': 1},
    'rare': {'name': 'Редкий', 'emoji': '🔵', 'rank': 2},
    'epic': {'name': 'Эпический', 'emoji': '🟣', 'rank': 3},
    'legendary': {'name': 'Легендарный', 'emoji': '🟡', 'rank': 4},
}

# Редкость — постоянное свойство конкретного предмета. Уже купленные предметы
# не меняются и не требуют миграции БД: inventory хранит только item_key.
ITEM_RARITY = {
    # beds
    'bed_pink': 'common', 'bed_mint': 'common', 'bed_cloud': 'uncommon',
    'bed_bear': 'uncommon', 'bed_strawberry': 'rare', 'bed_flower': 'rare',
    'bed_bunny': 'epic', 'bed_canopy': 'legendary',
    # rugs
    'rug_round': 'common', 'rug_paw': 'common', 'rug_check': 'uncommon',
    'rug_cloud': 'uncommon', 'rug_heart': 'rare', 'rug_flower': 'rare',
    'rug_star': 'epic',
    # lamps
    'lamp_soft_pink': 'common', 'lamp_heart_beige': 'common',
    'lamp_bow_pink': 'uncommon', 'lamp_bow_cream': 'uncommon',
    'lamp_bow_tall': 'rare', 'lamp_hearts': 'rare', 'lamp_flowers': 'epic',
    # toys
    'toy_yarn': 'common', 'toy_mouse': 'common', 'toy_fish': 'uncommon',
    'toy_star': 'uncommon', 'toy_bunny': 'rare', 'toy_wand': 'epic',
    # bowls
    'bowl_water': 'common', 'bowl_blue': 'common', 'bowl_round_food': 'uncommon',
    'bowl_beige_food': 'uncommon', 'bowl_paw_food': 'rare',
    # plants
    'plant_cactus': 'common', 'plant_aloe': 'common', 'plant_sansevieria': 'uncommon',
    'plant_ficus': 'uncommon', 'plant_monstera': 'rare', 'plant_calathea': 'rare',
    'plant_blossom': 'epic', 'plant_bonsai': 'legendary',
    # decor
    'decor_moon_frame': 'common', 'decor_kitty_frame': 'uncommon',
    'decor_star_garland': 'uncommon', 'decor_photo_garland': 'rare',
    'decor_macrame_plant': 'rare', 'decor_cat_clock': 'epic',
    # windows
    'window_day': 'common', 'window_spring': 'common', 'window_autumn': 'uncommon',
    'window_rain': 'uncommon', 'window_sunset': 'rare', 'window_winter': 'rare',
    'window_night': 'epic', 'window_city_night': 'legendary',
}



# Предметы, которые нельзя купить в магазине: они выпадают только из редких событий.
DROP_ONLY_ITEMS = frozenset({
    'rug_star',
    'toy_wand',
    'plant_blossom',
    'decor_cat_clock',
    'window_city_night',
})

for _key, _item in SHOP_ITEMS.items():
    _rarity = ITEM_RARITY.get(_key, 'common')
    _meta = RARITY_INFO[_rarity]
    _item['rarity'] = _rarity
    _item['rarity_name'] = _meta['name']
    _item['rarity_emoji'] = _meta['emoji']
    _item['rarity_rank'] = _meta['rank']
    _item['drop_only'] = _key in DROP_ONLY_ITEMS
