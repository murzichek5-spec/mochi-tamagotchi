import hashlib
import json
import random
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from catalog import DROP_ONLY_ITEMS, RARITY_INFO, SHOP_ITEMS

DB_PATH = Path(__file__).resolve().with_name("mochi.db")
DB_BUSY_TIMEOUT_MS = 5000


@asynccontextmanager
async def db_connection():
    """Open SQLite with the same safety settings on every connection."""
    db = await aiosqlite.connect(DB_PATH)
    try:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
        yield db
    finally:
        await db.close()

# ---- Базовые конфиги игры ----
DAILY_TASKS_PER_DAY = 4
DAILY_COMPLETION_BONUS_COINS = 45
DAILY_COMPLETION_BONUS_XP = 50
RANDOM_EVENT_MIN_MINUTES = 240
RANDOM_EVENT_MAX_MINUTES = 480
MINIGAME_COOLDOWN_MINUTES = 20
MINIGAME_SESSION_MINUTES = 5
HOURLY_HUNGER_DECAY = 4
HOURLY_HAPPINESS_DECAY = 2
HOURLY_ENERGY_DECAY = 1
JOINT_STREAK_ACTIONS = frozenset({'joint'})
SHOP_ROTATION_COUNTS = {'common': 8, 'uncommon': 4, 'rare': 3}
SHOP_EPIC_CHANCE = 0.55
SHOP_LEGENDARY_CHANCE = 0.14

# Случайные события небольшие и в основном позитивные: они оживляют питомца,
# но не должны наказывать человека за то, что он не был онлайн в конкретную минуту.
RANDOM_EVENTS = [
    {'key': 'coin_under_rug', 'text': '🪙 Mochi нашёл блестящую монетку под ковриком.', 'coins': 18, 'weight': 16},
    {'key': 'sunbeam', 'text': '☀️ Mochi нашёл идеальное солнечное пятнышко и немного отдохнул.', 'happiness': 6, 'energy': 10, 'weight': 15},
    {'key': 'secret_snack', 'text': '🍪 Mochi каким-то чудом нашёл маленький вкусный перекус.', 'hunger': 12, 'happiness': 3, 'weight': 14},
    {'key': 'zoomies', 'text': '💨 У Mochi внезапно случились зумисы по всей комнате!', 'happiness': 10, 'energy': -5, 'hunger': -2, 'weight': 14},
    {'key': 'soft_blanket', 'text': '🧸 Mochi уютно устроился и набрался сил.', 'energy': 15, 'love': 4, 'weight': 13},
    {'key': 'thinking_of_you', 'text': '💗 Mochi вспомнил о хозяевах и довольно замурчал.', 'love': 8, 'happiness': 5, 'weight': 13},
    {'key': 'lucky_find', 'text': '✨ Сегодня удачный день: Mochi нашёл небольшой клад!', 'coins': 30, 'happiness': 4, 'weight': 7},
    {'key': 'window_birds', 'text': '🐦 За окном появились птицы. Mochi долго и очень серьёзно за ними наблюдал.', 'happiness': 8, 'weight': 8},
    {'key': 'mystery_box', 'text': '🎁 Mochi нашёл старую коробку. Кажется, внутри что-то редкое!', 'drop_mode': 'drop_only', 'weight': 3},
    {'key': 'sparkly_stash', 'text': '✨ Mochi заметил странный блеск в углу комнаты и раскопал тайник.', 'drop_mode': 'rare_plus', 'weight': 4},
]

# Большой пул ежедневок. Вчерашние ключи мы исключаем, поэтому день реально ощущается новым,
# чтобы набор не повторялся изо дня в день.
DAILY_POOL = [
    ('care3', 'Позаботьтесь о питомце 3 раза', 'any', 3, 18, 20),
    ('care4', 'Сделайте 4 любых действия', 'any', 4, 22, 24),
    ('care5', 'Уделите питомцу внимание 5 раз', 'any', 5, 26, 28),
    ('care6', 'Сделайте 6 действий за день', 'any', 6, 30, 34),
    ('care8', 'Большой день заботы: 8 действий', 'any', 8, 40, 45),
    ('feed1', 'Покормите питомца', 'feed', 1, 10, 12),
    ('feed2', 'Покормите питомца 2 раза', 'feed', 2, 15, 17),
    ('feed3', 'Устройте пир: 3 кормления', 'feed', 3, 22, 25),
    ('play1', 'Поиграйте с питомцем', 'play', 1, 11, 13),
    ('play2', 'Поиграйте 2 раза', 'play', 2, 16, 19),
    ('play3', 'Игровой марафон: 3 игры', 'play', 3, 24, 27),
    ('love1', 'Погладьте питомца', 'love', 1, 10, 12),
    ('love2', 'Подарите ласку 2 раза', 'love', 2, 15, 17),
    ('love3', 'Три порции любви', 'love', 3, 22, 25),
    ('sleep1', 'Уложите питомца спать', 'sleep', 1, 12, 15),
    ('sleep2', 'Дважды позаботьтесь о сне', 'sleep', 2, 20, 23),
    ('joint1', 'Сделайте одно совместное действие', 'joint', 1, 28, 32),
    ('joint2', 'Сделайте 2 совместных действия', 'joint', 2, 45, 50),
    ('food_love3', 'Еда или ласка — 3 действия', 'feed|love', 3, 21, 24),
    ('food_love5', 'Сытый и любимый: 5 кормлений/ласк', 'feed|love', 5, 32, 36),
    ('play_love3', 'Играйте или гладьте 3 раза', 'play|love', 3, 22, 25),
    ('play_love5', 'Веселье и любовь: 5 действий', 'play|love', 5, 34, 38),
    ('feed_play3', 'Кормите или играйте 3 раза', 'feed|play', 3, 22, 25),
    ('feed_play5', 'Активный день: 5 кормлений/игр', 'feed|play', 5, 34, 38),
    ('calm3', 'Ласка или сон — 3 спокойных действия', 'love|sleep', 3, 23, 26),
    ('active4', 'Игра или совместное действие — 4 раза', 'play|joint', 4, 35, 40),
    ('minigame1', 'Сыграйте в мини-игру', 'minigame', 1, 14, 16),
    ('minigame2', 'Сыграйте в мини-игры 2 раза', 'minigame', 2, 22, 26),
]

ACHIEVEMENTS = {
    'first_care': ('Первый уход', 'Сделайте первое действие с питомцем', 10, 15),
    'actions10': ('Разогрев', 'Совершите 10 действий', 15, 20),
    'actions25': ('Заботливые руки', 'Совершите 25 действий', 25, 30),
    'actions50': ('Заботливые хозяева', 'Совершите 50 действий', 45, 55),
    'actions100': ('Сотня заботы', 'Совершите 100 действий', 80, 95),
    'actions250': ('Не разлей вода', 'Совершите 250 действий', 140, 160),
    'actions500': ('Легенды ухода', 'Совершите 500 действий', 250, 300),

    'feed10': ('Шеф-повар', 'Покормите питомца 10 раз', 20, 25),
    'feed25': ('Полная миска', 'Покормите питомца 25 раз', 35, 40),
    'feed50': ('Гурман', 'Покормите питомца 50 раз', 60, 70),
    'feed100': ('Мишлен для Mochi', 'Покормите питомца 100 раз', 110, 125),

    'play10': ('Игрун', 'Поиграйте 10 раз', 20, 25),
    'play25': ('Неугомонные', 'Поиграйте 25 раз', 35, 40),
    'play50': ('Игровой автомат', 'Поиграйте 50 раз', 65, 75),

    'love10': ('Почеши за ушком', 'Погладьте питомца 10 раз', 20, 25),
    'love25': ('Море нежности', 'Погладьте питомца 25 раз', 35, 40),
    'love50': ('Профессиональные обнимашки', 'Погладьте питомца 50 раз', 65, 75),
    'love100': ('Любовь до краёв', 'Доведите показатель любви до 100', 35, 40),

    'sleep10': ('Сладких снов', 'Уложите питомца спать 10 раз', 20, 25),
    'sleep25': ('Хранители сна', 'Уложите питомца спать 25 раз', 40, 45),
    'sleep50': ('Мастер колыбельных', 'Уложите питомца спать 50 раз', 70, 80),

    'joint1': ('Вместе веселее', 'Завершите первое совместное действие', 20, 25),
    'joint5': ('Командная работа', 'Завершите 5 совместных действий', 50, 60),
    'joint10': ('Настоящий дуэт', 'Завершите 10 совместных действий', 80, 95),
    'joint25': ('Два сердца — один Mochi', 'Завершите 25 совместных действий', 160, 190),

    'level2': ('Первые шаги', 'Достигните 2 уровня', 15, 20),
    'level5': ('Подрос!', 'Достигните 5 уровня', 40, 50),
    'level10': ('Опытный питомец', 'Достигните 10 уровня', 90, 105),
    'level20': ('Большой Mochi', 'Достигните 20 уровня', 180, 220),

    'coins100': ('Первые сбережения', 'Накопите 100 монет одновременно', 20, 20),
    'coins500': ('Копилка', 'Накопите 500 монет одновременно', 50, 50),
    'coins1000': ('Мешок монет', 'Накопите 1000 монет одновременно', 100, 100),

    'dailies1': ('Первый план выполнен', 'Закройте первое ежедневное задание', 15, 20),
    'dailies5': ('Входим в ритм', 'Закройте 5 ежедневных заданий', 30, 35),
    'dailies10': ('Режим заботы', 'Закройте 10 ежедневных заданий', 60, 70),
    'dailies25': ('Стабильность', 'Закройте 25 ежедневных заданий', 110, 130),
    'dailies50': ('Ежедневный ритуал', 'Закройте 50 ежедневных заданий', 190, 220),
    'dailies100': ('Ни дня без Mochi', 'Закройте 100 ежедневных заданий', 350, 400),
    'dailyclear1': ('Идеальный день', 'Закройте все ежедневные задания за один день', 30, 35),
    'dailyclear7': ('Неделя по плану', 'Полностью закройте ежедневки 7 раз', 100, 120),
    'dailyclear30': ('Железная дисциплина', 'Полностью закройте ежедневки 30 раз', 280, 330),

    'streak3': ('Три дня вместе', 'Завершайте совместное действие 3 дня подряд', 25, 30),
    'streak7': ('Неделя вдвоём', 'Завершайте совместное действие 7 дней подряд', 65, 80),
    'streak14': ('Две недели вместе', 'Завершайте совместное действие 14 дней подряд', 120, 150),
    'streak30': ('Месяц вдвоём', 'Завершайте совместное действие 30 дней подряд', 260, 320),
    'streak60': ('Два месяца командой', 'Завершайте совместное действие 60 дней подряд', 450, 550),
    'streak100': ('Сто дней вместе', 'Завершайте совместное действие 100 дней подряд', 800, 1000),

    'events5': ('Любопытная жизнь', 'Переживите 5 случайных событий', 35, 40),
    'events20': ('Всегда что-то происходит', 'Переживите 20 случайных событий', 90, 110),
    'events50': ('Главный герой', 'Переживите 50 случайных событий', 180, 220),

    'games10': ('Игровая разминка', 'Завершите 10 мини-игр', 35, 45),
    'games50': ('Аркадный Mochi', 'Завершите 50 мини-игр', 100, 130),
    'games100': ('Чемпион мини-игр', 'Завершите 100 мини-игр', 220, 280),

    'collector3': ('Первые покупки', 'Соберите 3 предмета комнаты', 25, 30),
    'collector7': ('Коллекционер', 'Соберите 7 предметов комнаты', 55, 65),
    'collector12': ('Почти весь каталог', 'Соберите 12 предметов комнаты', 100, 120),
    'rare_item1': ('Редкая находка', 'Получите редкий предмет или лучше', 30, 35),
    'epic_item1': ('Фиолетовый блеск', 'Получите эпический предмет или лучше', 60, 75),
    'legendary_item1': ('Легенда комнаты', 'Получите легендарный предмет', 120, 150),
    'decorator4': ('Уют начинается', 'Заполните 4 слота комнаты', 35, 45),
    'decorator8': ('Дом мечты', 'Заполните все 8 слотов комнаты', 100, 130),

    'happy100': ('Улыбка до ушей', 'Доведите настроение до 100', 25, 30),
    'energy100': ('Полон сил', 'Доведите энергию до 100', 20, 25),
    'hunger100': ('Сытый кот — добрый кот', 'Доведите сытость до 100', 20, 25),
    'week_old': ('Неделя вместе', 'Растите этого питомца 7 дней', 60, 75),
    'month_old': ('Месяц вместе', 'Растите этого питомца 30 дней', 180, 220),
}


def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def today_key():
    return now_utc().date().isoformat()


def _next_random_event_at(base=None):
    base = base or now_utc()
    span = max(1, RANDOM_EVENT_MAX_MINUTES - RANDOM_EVENT_MIN_MINUTES + 1)
    minutes = RANDOM_EVENT_MIN_MINUTES + secrets.randbelow(span)
    return (base + timedelta(minutes=minutes)).isoformat()


def _choose_random_event():
    total = sum(max(1, int(event.get('weight', 1))) for event in RANDOM_EVENTS)
    pick = secrets.randbelow(total)
    for event in RANDOM_EVENTS:
        weight = max(1, int(event.get('weight', 1)))
        if pick < weight:
            return event
        pick -= weight
    return RANDOM_EVENTS[-1]


def _stage_key_for_level(level: int | None) -> str:
    level = max(1, int(level or 1))
    if level >= 20:
        return 'adult'
    if level >= 12:
        return 'teen'
    if level >= 5:
        return 'kitten'
    return 'baby'


def _stage_title(stage_key: str) -> str:
    return {'baby': 'Малыш', 'kitten': 'Котёнок', 'teen': 'Подросток', 'adult': 'Взрослый Mochi'}.get(stage_key, stage_key)


def _history_item_text(item_key: str) -> str:
    item = SHOP_ITEMS.get(item_key, {})
    return f"{item.get('rarity_emoji', '⚪')} {item.get('emoji', '🎁')} {item.get('name', item_key)}"


async def _add_history_in_tx(db, pet_id: int, kind: str, title: str, details: str = '', *, created_at: str | None = None, dedupe_key: str | None = None):
    await db.execute(
        'INSERT OR IGNORE INTO pet_history(pet_id,kind,title,details,created_at,dedupe_key) VALUES(?,?,?,?,?,?)',
        (pet_id, kind, title, details, created_at or now_iso(), dedupe_key),
    )


def _shop_rotation_keys(pet_id: int, day: str) -> list[str]:
    available = {
        rarity: sorted(key for key, item in SHOP_ITEMS.items() if item.get('rarity') == rarity and key not in DROP_ONLY_ITEMS)
        for rarity in RARITY_INFO
    }
    seed = int(hashlib.sha256(f'mochi-shop-v53:{pet_id}:{day}'.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    result: list[str] = []
    for rarity, count in SHOP_ROTATION_COUNTS.items():
        pool = available.get(rarity, [])
        result.extend(rng.sample(pool, min(count, len(pool))))
    epic = available.get('epic', [])
    legendary = available.get('legendary', [])
    if epic and rng.random() < SHOP_EPIC_CHANCE:
        result.append(rng.choice(epic))
    if legendary and rng.random() < SHOP_LEGENDARY_CHANCE:
        result.append(rng.choice(legendary))
    return result

def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


async def _ensure_column(db, table, column, definition):
    cur = await db.execute(f'PRAGMA table_info({table})')
    cols = {r[1] for r in await cur.fetchall()}
    if column not in cols:
        await db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


async def init_db():
    async with db_connection() as db:
        await db.execute('PRAGMA foreign_keys = ON')
        await db.execute('PRAGMA journal_mode = WAL')
        await db.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS couples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER,
            invite_code TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS pets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            couple_id INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT 'Mochi',
            hunger INTEGER NOT NULL DEFAULT 70,
            happiness INTEGER NOT NULL DEFAULT 70,
            energy INTEGER NOT NULL DEFAULT 70,
            love INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_decay_at TEXT,
            FOREIGN KEY(couple_id) REFERENCES couples(id) ON DELETE CASCADE
        )''')

        # Старую базу сохраняем и добавляем новые поля миграциями.
        await _ensure_column(db, 'users', 'last_active_at', 'TEXT')
        await _ensure_column(db, 'users', 'screen_chat_id', 'INTEGER')
        await _ensure_column(db, 'users', 'screen_message_id', 'INTEGER')
        await _ensure_column(db, 'users', 'screen_state', 'TEXT')
        await _ensure_column(db, 'users', 'active_couple_id', 'INTEGER')
        await _ensure_column(db, 'pets', 'last_decay_at', 'TEXT')
        await _ensure_column(db, 'pets', 'xp', 'INTEGER NOT NULL DEFAULT 0')
        await _ensure_column(db, 'pets', 'level', 'INTEGER NOT NULL DEFAULT 1')
        await _ensure_column(db, 'pets', 'coins', 'INTEGER NOT NULL DEFAULT 50')

        await db.execute('''CREATE TABLE IF NOT EXISTS pet_metrics (
            pet_id INTEGER PRIMARY KEY,
            actions_total INTEGER NOT NULL DEFAULT 0,
            feed_total INTEGER NOT NULL DEFAULT 0,
            play_total INTEGER NOT NULL DEFAULT 0,
            love_total INTEGER NOT NULL DEFAULT 0,
            sleep_total INTEGER NOT NULL DEFAULT 0,
            joint_total INTEGER NOT NULL DEFAULT 0,
            dailies_completed INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
        )''')
        await _ensure_column(db, 'pet_metrics', 'current_streak', 'INTEGER NOT NULL DEFAULT 0')
        await _ensure_column(db, 'pet_metrics', 'best_streak', 'INTEGER NOT NULL DEFAULT 0')
        await _ensure_column(db, 'pet_metrics', 'last_checkin_day', 'TEXT')
        await _ensure_column(db, 'pet_metrics', 'total_checkins', 'INTEGER NOT NULL DEFAULT 0')
        await _ensure_column(db, 'pet_metrics', 'daily_full_clears', 'INTEGER NOT NULL DEFAULT 0')
        await _ensure_column(db, 'pet_metrics', 'random_events_total', 'INTEGER NOT NULL DEFAULT 0')
        await _ensure_column(db, 'pet_metrics', 'next_random_event_at', 'TEXT')
        await _ensure_column(db, 'pet_metrics', 'minigames_total', 'INTEGER NOT NULL DEFAULT 0')
        await _ensure_column(db, 'pet_metrics', 'joint_streak_current', 'INTEGER NOT NULL DEFAULT 0')
        await _ensure_column(db, 'pet_metrics', 'joint_streak_best', 'INTEGER NOT NULL DEFAULT 0')
        await _ensure_column(db, 'pet_metrics', 'last_joint_streak_day', 'TEXT')
        await _ensure_column(db, 'pet_metrics', 'joint_streak_days_total', 'INTEGER NOT NULL DEFAULT 0')
        await db.execute('''CREATE TABLE IF NOT EXISTS daily_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pet_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            task_key TEXT NOT NULL,
            title TEXT NOT NULL,
            action_key TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            target INTEGER NOT NULL,
            reward_coins INTEGER NOT NULL,
            reward_xp INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(pet_id, day, task_key),
            FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS daily_bonus_claims (
            pet_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            reward_coins INTEGER NOT NULL,
            reward_xp INTEGER NOT NULL,
            claimed_at TEXT NOT NULL,
            PRIMARY KEY(pet_id, day),
            FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS random_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pet_id INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            happened_at TEXT NOT NULL,
            coins_delta INTEGER NOT NULL DEFAULT 0,
            hunger_delta INTEGER NOT NULL DEFAULT 0,
            happiness_delta INTEGER NOT NULL DEFAULT 0,
            energy_delta INTEGER NOT NULL DEFAULT 0,
            love_delta INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
        )''')
        await _ensure_column(db, 'random_event_log', 'item_key', 'TEXT')
        await db.execute('''CREATE TABLE IF NOT EXISTS pet_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pet_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            dedupe_key TEXT,
            FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE,
            UNIQUE(pet_id, dedupe_key)
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS minigame_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            pet_id INTEGER NOT NULL,
            game_key TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
            completed_at TEXT,
            result TEXT,
            reward_coins INTEGER NOT NULL DEFAULT 0,
            reward_xp INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS achievements (
            pet_id INTEGER NOT NULL,
            achievement_key TEXT NOT NULL,
            unlocked_at TEXT NOT NULL,
            PRIMARY KEY(pet_id, achievement_key),
            FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS inventory (
            pet_id INTEGER NOT NULL,
            item_key TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(pet_id, item_key),
            FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS room_equipment (
            pet_id INTEGER NOT NULL,
            slot TEXT NOT NULL,
            item_key TEXT NOT NULL,
            PRIMARY KEY(pet_id, slot),
            FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS joint_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pet_id INTEGER NOT NULL,
            action_key TEXT NOT NULL,
            starter_user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            completed_by INTEGER,
            completed_at TEXT,
            FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS ephemeral_messages (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(user_id, chat_id, message_id)
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY COLLATE NOCASE,
            reward_coins INTEGER NOT NULL CHECK(reward_coins > 0),
            max_uses INTEGER CHECK(max_uses IS NULL OR max_uses > 0),
            used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            disabled_at TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS promo_redemptions (
            code TEXT NOT NULL COLLATE NOCASE,
            user_id INTEGER NOT NULL,
            pet_id INTEGER NOT NULL,
            reward_coins INTEGER NOT NULL CHECK(reward_coins > 0),
            redeemed_at TEXT NOT NULL,
            PRIMARY KEY(code, user_id),
            FOREIGN KEY(code) REFERENCES promo_codes(code)
        )''')

        await _ensure_column(db, 'ephemeral_messages', 'cleanup_on_next', 'INTEGER NOT NULL DEFAULT 1')
        await _ensure_column(db, 'ephemeral_messages', 'expires_at', 'TEXT')

        await db.execute('UPDATE users SET last_active_at = COALESCE(last_active_at, created_at)')
        await db.execute('UPDATE pets SET last_decay_at = COALESCE(last_decay_at, created_at)')
        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) SELECT id FROM pets')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_couples_u1 ON couples(user1_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_couples_u2 ON couples(user2_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_pets_couple ON pets(couple_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_daily_pet_day ON daily_tasks(pet_id, day)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_daily_bonus_day ON daily_bonus_claims(day, pet_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_random_event_pet_time ON random_event_log(pet_id, happened_at)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_history_pet_time ON pet_history(pet_id, created_at DESC, id DESC)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_minigame_user_pet_time ON minigame_sessions(user_id, pet_id, completed_at)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_joint_pet_status ON joint_actions(pet_id, status)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_ephemeral_user ON ephemeral_messages(user_id, created_at)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_promo_enabled ON promo_codes(enabled, created_at)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_promo_redemptions_user ON promo_redemptions(user_id, redeemed_at)')
        # Счётчик — кэш для быстрого лимита, журнал активаций — источник истины.
        await db.execute('''UPDATE promo_codes
            SET used_count=(SELECT COUNT(*) FROM promo_redemptions r WHERE r.code=promo_codes.code)''')

        # Нормализуем данные старых/ручных сборок без потери игрового прогресса.
        await db.execute("UPDATE pets SET hunger=MIN(100, MAX(0, hunger)), happiness=MIN(100, MAX(0, happiness)), energy=MIN(100, MAX(0, energy)), love=MIN(100, MAX(0, love)), xp=MAX(0, xp), level=MAX(1, level), coins=MAX(0, coins)")
        await db.execute('UPDATE inventory SET quantity=MAX(0, quantity)')
        # Если старая версия записала экипировку без строки инвентаря, восстанавливаем владение.
        await db.execute("INSERT OR IGNORE INTO inventory(pet_id,item_key,quantity) SELECT pet_id,item_key,1 FROM room_equipment")
        # Активная связь должна принадлежать пользователю; битые ссылки сбрасываем.
        await db.execute("UPDATE users SET active_couple_id=NULL WHERE active_couple_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM couples c WHERE c.id=users.active_couple_id AND (c.user1_id=users.id OR c.user2_id=users.id))")
        # Старые сборки могли оставить несколько pending-экшенов на одного питомца.
        # Перед уникальным partial-index оставляем только самый свежий, остальные протухают.
        await db.execute("""UPDATE joint_actions SET status='expired'
            WHERE status='pending' AND id NOT IN (
                SELECT MAX(id) FROM joint_actions WHERE status='pending' GROUP BY pet_id
            )""")
        await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_joint_pending_pet
            ON joint_actions(pet_id) WHERE status='pending'""")

        # История начинается с рождения. Для старых баз восстанавливаем также уже полученные достижения.
        cur = await db.execute('SELECT id,name,created_at FROM pets')
        for pet_row in await cur.fetchall():
            await _add_history_in_tx(
                db, int(pet_row[0]), 'birth', '🐣 Mochi появился',
                f'{pet_row[1]} начал свою историю.', created_at=pet_row[2], dedupe_key='birth',
            )
        cur = await db.execute('SELECT pet_id,achievement_key,unlocked_at FROM achievements')
        for ach_row in await cur.fetchall():
            meta = ACHIEVEMENTS.get(ach_row[1])
            if meta:
                await _add_history_in_tx(
                    db, int(ach_row[0]), 'achievement', '🏆 Получено достижение', meta[0],
                    created_at=ach_row[2], dedupe_key=f'achievement:{ach_row[1]}',
                )

        # Старым питомцам назначаем первый момент случайного события только при миграции.
        # Это предотвращает лавину событий сразу после первого запуска новой версии.
        cur = await db.execute('SELECT pet_id FROM pet_metrics WHERE next_random_event_at IS NULL')
        for row in await cur.fetchall():
            await db.execute(
                'UPDATE pet_metrics SET next_random_event_at=? WHERE pet_id=?',
                (_next_random_event_at(), row[0]),
            )
        await db.commit()


async def save_ephemeral_message(
    user_id: int,
    chat_id: int,
    message_id: int,
    *,
    cleanup_on_next: bool = True,
    expires_in_seconds: float | None = None,
):
    expires_at = None
    if expires_in_seconds is not None:
        expires_at = (now_utc() + timedelta(seconds=max(0.0, float(expires_in_seconds)))).isoformat()
    async with db_connection() as db:
        await db.execute(
            """INSERT INTO ephemeral_messages(
                user_id, chat_id, message_id, created_at, cleanup_on_next, expires_at
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id,chat_id,message_id) DO UPDATE SET
                cleanup_on_next=excluded.cleanup_on_next,
                expires_at=excluded.expires_at""",
            (user_id, chat_id, message_id, now_iso(), int(cleanup_on_next), expires_at),
        )
        await db.commit()


async def get_ephemeral_messages(user_id: int):
    """Return messages that are safe to remove on this interaction."""
    async with db_connection() as db:
        cur = await db.execute(
            """SELECT chat_id, message_id FROM ephemeral_messages
               WHERE user_id=? AND (
                   cleanup_on_next=1 OR (expires_at IS NOT NULL AND expires_at<=?)
               ) ORDER BY created_at""",
            (user_id, now_iso()),
        )
        return await cur.fetchall()


async def delete_ephemeral_message(user_id: int, chat_id: int, message_id: int):
    async with db_connection() as db:
        await db.execute(
            'DELETE FROM ephemeral_messages WHERE user_id=? AND chat_id=? AND message_id=?',
            (user_id, chat_id, message_id),
        )
        await db.commit()


async def save_user(user):
    async with db_connection() as db:
        await db.execute('''INSERT INTO users(id, username, first_name, created_at, last_active_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_active_at=excluded.last_active_at''',
            (user.id, user.username, user.first_name, now_iso(), now_iso()))
        await db.commit()


async def touch_user(user_id):
    async with db_connection() as db:
        await db.execute('UPDATE users SET last_active_at=? WHERE id=?', (now_iso(), user_id))
        await db.commit()


async def get_user(user_id):
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT * FROM users WHERE id=?', (user_id,))
        return await cur.fetchone()


async def get_broadcast_user_ids():
    """Return registered players that still have at least one Mochi/couple."""
    async with db_connection() as db:
        cur = await db.execute(
            """SELECT u.id
               FROM users AS u
               WHERE EXISTS (
                   SELECT 1 FROM couples AS c
                   WHERE c.user1_id=u.id OR c.user2_id=u.id
               )
               ORDER BY u.id"""
        )
        return [int(row[0]) for row in await cur.fetchall()]


PROMO_CODE_PATTERN = re.compile(r'^[A-Z0-9_-]{3,32}$')
PROMO_MAX_REWARD_COINS = 10_000_000
PROMO_MAX_USES = 1_000_000


def normalize_promo_code(code: str | None) -> str | None:
    """Return canonical promo code or None for invalid input."""
    normalized = (code or '').strip().upper()
    if not PROMO_CODE_PATTERN.fullmatch(normalized):
        return None
    return normalized


async def create_promo_code(code: str, reward_coins: int, max_uses: int | None, created_by: int):
    """Create a promo without ever reusing an old code/redemption history."""
    code = normalize_promo_code(code)
    if not code:
        return False, 'invalid_code'
    try:
        reward_coins = int(reward_coins)
    except (TypeError, ValueError):
        return False, 'invalid_reward'
    if reward_coins < 1 or reward_coins > PROMO_MAX_REWARD_COINS:
        return False, 'invalid_reward'
    if max_uses is not None:
        try:
            max_uses = int(max_uses)
        except (TypeError, ValueError):
            return False, 'invalid_limit'
        if max_uses < 1 or max_uses > PROMO_MAX_USES:
            return False, 'invalid_limit'

    async with db_connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT 1 FROM promo_codes WHERE code=?', (code,))
        if await cur.fetchone():
            await db.rollback()
            return False, 'exists'
        await db.execute(
            '''INSERT INTO promo_codes(code,reward_coins,max_uses,used_count,enabled,created_by,created_at)
               VALUES(?,?,?,0,1,?,?)''',
            (code, reward_coins, max_uses, int(created_by), now_iso()),
        )
        await db.commit()
    return True, code


async def set_promo_enabled(code: str, enabled: bool):
    code = normalize_promo_code(code)
    if not code:
        return False, 'invalid_code'
    async with db_connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT enabled FROM promo_codes WHERE code=?', (code,))
        row = await cur.fetchone()
        if not row:
            await db.rollback()
            return False, 'not_found'
        value = 1 if enabled else 0
        disabled_at = None if enabled else now_iso()
        await db.execute(
            'UPDATE promo_codes SET enabled=?, disabled_at=? WHERE code=?',
            (value, disabled_at, code),
        )
        await db.commit()
    return True, code


async def get_promo_codes(limit: int = 50):
    limit = max(1, min(int(limit), 200))
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            '''SELECT code,reward_coins,max_uses,used_count,enabled,created_by,created_at,disabled_at
               FROM promo_codes ORDER BY created_at DESC LIMIT ?''',
            (limit,),
        )
        return await cur.fetchall()


async def redeem_promo_code(user_id: int, code: str):
    """Atomically redeem one promo for the user's currently active Mochi."""
    code = normalize_promo_code(code)
    if not code:
        return False, {'reason': 'invalid_code'}

    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')

        cur = await db.execute('SELECT * FROM promo_codes WHERE code=?', (code,))
        promo = await cur.fetchone()
        if not promo:
            await db.rollback()
            return False, {'reason': 'not_found'}
        if not promo['enabled']:
            await db.rollback()
            return False, {'reason': 'disabled'}
        if promo['max_uses'] is not None and promo['used_count'] >= promo['max_uses']:
            await db.rollback()
            return False, {'reason': 'exhausted'}

        cur = await db.execute(
            'SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?',
            (code, int(user_id)),
        )
        if await cur.fetchone():
            await db.rollback()
            return False, {'reason': 'already_used'}

        cur = await db.execute(
            '''SELECT c.id
               FROM users u
               JOIN couples c ON c.id=u.active_couple_id
               WHERE u.id=? AND (c.user1_id=? OR c.user2_id=?)
               LIMIT 1''',
            (int(user_id), int(user_id), int(user_id)),
        )
        couple = await cur.fetchone()
        if couple:
            couple_id = int(couple['id'])
        else:
            cur = await db.execute(
                '''SELECT id FROM couples
                   WHERE user1_id=? OR user2_id=?
                   ORDER BY (user2_id IS NOT NULL) DESC, id ASC LIMIT 1''',
                (int(user_id), int(user_id)),
            )
            couple = await cur.fetchone()
            if not couple:
                await db.rollback()
                return False, {'reason': 'no_pet'}
            couple_id = int(couple['id'])
            await db.execute('UPDATE users SET active_couple_id=? WHERE id=?', (couple_id, int(user_id)))

        cur = await db.execute('SELECT id,coins FROM pets WHERE couple_id=? ORDER BY id LIMIT 1', (couple_id,))
        pet = await cur.fetchone()
        if not pet:
            await db.rollback()
            return False, {'reason': 'no_pet'}

        reward = int(promo['reward_coins'])
        pet_id = int(pet['id'])
        await db.execute('UPDATE pets SET coins=coins+? WHERE id=?', (reward, pet_id))
        await db.execute('UPDATE promo_codes SET used_count=used_count+1 WHERE code=?', (code,))
        await db.execute(
            '''INSERT INTO promo_redemptions(code,user_id,pet_id,reward_coins,redeemed_at)
               VALUES(?,?,?,?,?)''',
            (code, int(user_id), pet_id, reward, now_iso()),
        )
        cur = await db.execute('SELECT coins FROM pets WHERE id=?', (pet_id,))
        new_balance = int((await cur.fetchone())['coins'])
        cur = await db.execute('SELECT used_count,max_uses FROM promo_codes WHERE code=?', (code,))
        usage = await cur.fetchone()
        await db.commit()

    return True, {
        'reason': 'ok',
        'code': code,
        'pet_id': pet_id,
        'reward_coins': reward,
        'balance': new_balance,
        'used_count': int(usage['used_count']),
        'max_uses': usage['max_uses'],
    }


async def get_promo_redemption_count(code: str):
    code = normalize_promo_code(code)
    if not code:
        return 0
    async with db_connection() as db:
        cur = await db.execute('SELECT COUNT(*) FROM promo_redemptions WHERE code=?', (code,))
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def set_screen(user_id, chat_id, message_id, screen_state=None):
    """Bind a Telegram message as the canonical Tamagotchi screen.

    screen_state is optional on purpose: callbacks can re-bind an existing screen without
    lying about which animation is currently visible. Media edits pass the actual state.
    """
    async with db_connection() as db:
        if screen_state is None:
            await db.execute(
                'UPDATE users SET screen_chat_id=?, screen_message_id=? WHERE id=?',
                (chat_id, message_id, user_id),
            )
        else:
            await db.execute(
                'UPDATE users SET screen_chat_id=?, screen_message_id=?, screen_state=? WHERE id=?',
                (chat_id, message_id, screen_state, user_id),
            )
        await db.commit()


async def set_screen_state(user_id, screen_state):
    async with db_connection() as db:
        await db.execute('UPDATE users SET screen_state=? WHERE id=?', (screen_state, user_id))
        await db.commit()


async def clear_screen(user_id):
    async with db_connection() as db:
        await db.execute(
            'UPDATE users SET screen_chat_id=NULL, screen_message_id=NULL, screen_state=NULL WHERE id=?',
            (user_id,),
        )
        await db.commit()


async def get_screen(user_id):
    u = await get_user(user_id)
    if not u or not u['screen_chat_id'] or not u['screen_message_id']:
        return None
    return u['screen_chat_id'], u['screen_message_id']


async def get_screen_state(user_id):
    u = await get_user(user_id)
    if not u or not u['screen_chat_id'] or not u['screen_message_id']:
        return None
    return u['screen_state'] if 'screen_state' in u.keys() else None


async def get_couples_by_user(user_id, include_pending=True):
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        sql = 'SELECT * FROM couples WHERE (user1_id=? OR user2_id=?)'
        args = [user_id, user_id]
        if not include_pending:
            sql += ' AND user2_id IS NOT NULL'
        sql += ' ORDER BY id'
        cur = await db.execute(sql, args)
        return await cur.fetchall()


async def get_couple_by_id(couple_id):
    if not couple_id:
        return None
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT * FROM couples WHERE id=?', (couple_id,))
        return await cur.fetchone()


async def get_active_couple(user_id):
    u = await get_user(user_id)
    couples = await get_couples_by_user(user_id)
    if not couples:
        return None
    if u and u['active_couple_id']:
        for c in couples:
            if c['id'] == u['active_couple_id']:
                return c
    chosen = next((c for c in couples if c['user2_id'] is not None), couples[0])
    await set_active_couple(user_id, chosen['id'])
    return chosen


async def set_active_couple(user_id, couple_id):
    couple = await get_couple_by_id(couple_id)
    if not couple or user_id not in (couple['user1_id'], couple['user2_id']):
        return None
    async with db_connection() as db:
        await db.execute('UPDATE users SET active_couple_id=? WHERE id=?', (couple_id, user_id))
        await db.commit()
    return couple


async def create_couple(user_id):
    now = now_iso()
    async with db_connection() as db:
        # token_urlsafe(8) почти наверняка уникален, но UNIQUE должен иметь корректный retry,
        # а не случайный IntegrityError в проде.
        cid = None
        for _ in range(5):
            code = secrets.token_urlsafe(8)
            try:
                cur = await db.execute(
                    'INSERT INTO couples(user1_id, invite_code, created_at) VALUES(?,?,?)',
                    (user_id, code, now),
                )
            except sqlite3.IntegrityError:
                continue
            cid = cur.lastrowid
            break
        if cid is None:
            await db.rollback()
            raise RuntimeError('Не удалось создать уникальную invite-ссылку после нескольких попыток.')

        pcur = await db.execute(
            '''INSERT INTO pets(couple_id, name, created_at, last_decay_at, xp, level, coins)
               VALUES(?,?,?,?,0,1,50)''',
            (cid, 'Mochi', now, now),
        )
        pid = pcur.lastrowid
        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) VALUES(?)', (pid,))
        await _add_history_in_tx(db, pid, 'birth', '🐣 Mochi появился', 'Mochi начал свою историю.', created_at=now, dedupe_key='birth')
        await db.execute(
            'UPDATE pet_metrics SET next_random_event_at=COALESCE(next_random_event_at, ?) WHERE pet_id=?',
            (_next_random_event_at(), pid),
        )
        await db.execute('UPDATE users SET active_couple_id=? WHERE id=?', (cid, user_id))
        await db.commit()
    return await get_couple_by_id(cid)


async def join_couple(user_id, invite_code):
    invite_code = (invite_code or '').strip()
    if not invite_code:
        return False, 'Такого приглашения нет.'

    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        # SERIALIZE join: два человека не должны одновременно занять одно приглашение.
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT * FROM couples WHERE invite_code=?', (invite_code,))
        c = await cur.fetchone()
        if not c:
            await db.rollback()
            return False, 'Такого приглашения нет.'
        if c['user1_id'] == user_id:
            await db.rollback()
            return False, 'Это твоя собственная ссылка 😼'
        if c['user2_id'] is not None:
            await db.rollback()
            return False, 'Эта связь уже занята.'

        cur = await db.execute(
            '''SELECT id FROM couples WHERE user2_id IS NOT NULL AND
               ((user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)) LIMIT 1''',
            (c['user1_id'], user_id, user_id, c['user1_id']),
        )
        if await cur.fetchone():
            await db.rollback()
            return False, 'У вас уже есть общий Mochi 💞'

        cur = await db.execute(
            'UPDATE couples SET user2_id=? WHERE id=? AND user2_id IS NULL',
            (user_id, c['id']),
        )
        if cur.rowcount != 1:
            await db.rollback()
            return False, 'Эта связь уже занята.'

        # Вступивший пользователь открывает нового Mochi сразу. Создателя ссылки не
        # переключаем насильно, если он уже выбрал другого активного питомца.
        await db.execute('UPDATE users SET active_couple_id=? WHERE id=?', (c['id'], user_id))
        await db.execute(
            'UPDATE users SET active_couple_id=COALESCE(active_couple_id, ?) WHERE id=?',
            (c['id'], c['user1_id']),
        )
        await db.commit()
        return True, c['id']


async def get_pet_by_couple(couple_id, apply_decay=True):
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT * FROM pets WHERE couple_id=? ORDER BY id LIMIT 1', (couple_id,))
        pet = await cur.fetchone()
    if pet and apply_decay:
        return await apply_pet_decay(pet['id'])
    return pet


async def get_pet_by_user(user_id, apply_decay=True):
    c = await get_active_couple(user_id)
    if not c:
        return None
    return await get_pet_by_couple(c['id'], apply_decay)


async def get_pet_by_id(pet_id):
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT * FROM pets WHERE id=?', (pet_id,))
        return await cur.fetchone()


async def rename_pet(pet_id, name):
    name = ' '.join((name or '').strip().split())[:24]
    if not name:
        return False
    async with db_connection() as db:
        cur = await db.execute('SELECT name FROM pets WHERE id=?', (pet_id,))
        row = await cur.fetchone()
        if not row:
            return False
        old_name = str(row[0] or 'Mochi')
        cur = await db.execute('UPDATE pets SET name=? WHERE id=?', (name, pet_id))
        if cur.rowcount == 1 and old_name != name:
            await _add_history_in_tx(db, pet_id, 'rename', '✏️ Новое имя', f'{old_name} → {name}')
        await db.commit()
    return cur.rowcount == 1


async def get_partner_id(user_id, couple_id=None):
    c = await get_couple_by_id(couple_id) if couple_id else await get_active_couple(user_id)
    if not c or user_id not in (c['user1_id'], c['user2_id']):
        return None
    return c['user2_id'] if c['user1_id'] == user_id else c['user1_id']


async def get_couple_user_ids_by_pet(pet_id):
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT c.user1_id,c.user2_id FROM couples c JOIN pets p ON p.couple_id=c.id WHERE p.id=?', (pet_id,))
        r = await cur.fetchone()
        return [] if not r else [x for x in (r['user1_id'], r['user2_id']) if x is not None]


async def get_couple_id_by_pet(pet_id):
    async with db_connection() as db:
        cur = await db.execute('SELECT couple_id FROM pets WHERE id=?', (pet_id,))
        r = await cur.fetchone()
        return r[0] if r else None


async def is_couple_active_for_user(user_id, couple_id):
    u = await get_user(user_id)
    return bool(u and u['active_couple_id'] == couple_id)


async def apply_pet_decay(pet_id):
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT * FROM pets WHERE id=?', (pet_id,))
        pet = await cur.fetchone()
        if not pet:
            await db.rollback()
            return None
        last = parse_dt(pet['last_decay_at']) or parse_dt(pet['created_at']) or now_utc()
        now = now_utc()
        # Будущая метка (битые часы/ручное редактирование БД) раньше замораживала
        # деградацию на часы или дни. Нормализуем её к текущему времени.
        if last > now:
            last = now
            await db.execute('UPDATE pets SET last_decay_at=? WHERE id=?', (now.isoformat(), pet_id))
        hours = int((now - last).total_seconds() // 3600)
        if hours > 0:
            hunger = max(0, pet['hunger'] - HOURLY_HUNGER_DECAY * hours)
            happiness = max(0, pet['happiness'] - HOURLY_HAPPINESS_DECAY * hours)
            energy = max(0, pet['energy'] - HOURLY_ENERGY_DECAY * hours)
            new_ts = datetime.fromtimestamp(last.timestamp() + hours * 3600, tz=timezone.utc).isoformat()
            await db.execute(
                'UPDATE pets SET hunger=?, happiness=?, energy=?, last_decay_at=? WHERE id=?',
                (hunger, happiness, energy, new_ts, pet_id),
            )
        await db.commit()
    return await get_pet_by_id(pet_id)


async def update_pet(pet_id, hunger_delta=0, happiness_delta=0, energy_delta=0, love_delta=0):
    await apply_pet_decay(pet_id)
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT * FROM pets WHERE id=?', (pet_id,))
        p = await cur.fetchone()
        if not p:
            await db.rollback()
            return None
        vals = (
            max(0, min(100, p['hunger'] + hunger_delta)),
            max(0, min(100, p['happiness'] + happiness_delta)),
            max(0, min(100, p['energy'] + energy_delta)),
            max(0, min(100, p['love'] + love_delta)),
            pet_id,
        )
        await db.execute('UPDATE pets SET hunger=?,happiness=?,energy=?,love=? WHERE id=?', vals)
        await db.commit()
    return await get_pet_by_id(pet_id)


async def apply_care_action(
    pet_id, *, cost=0, hunger_delta=0, happiness_delta=0, energy_delta=0, love_delta=0
):
    """Atomically charge an action and apply its stat changes.

    Returns ``(pet, reason)`` where reason is None on success, ``no_pet`` or
    ``insufficient_coins`` on failure. Decay is applied before the transaction
    so the action always works from current stats.
    """
    cost = max(0, int(cost or 0))
    await apply_pet_decay(pet_id)
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT * FROM pets WHERE id=?', (pet_id,))
        p = await cur.fetchone()
        if not p:
            await db.rollback()
            return None, 'no_pet'
        if int(p['coins'] or 0) < cost:
            await db.rollback()
            return None, 'insufficient_coins'

        hunger = max(0, min(100, int(p['hunger']) + int(hunger_delta)))
        happiness = max(0, min(100, int(p['happiness']) + int(happiness_delta)))
        energy = max(0, min(100, int(p['energy']) + int(energy_delta)))
        love = max(0, min(100, int(p['love']) + int(love_delta)))
        coins = int(p['coins'] or 0) - cost
        await db.execute(
            'UPDATE pets SET hunger=?,happiness=?,energy=?,love=?,coins=? WHERE id=?',
            (hunger, happiness, energy, love, coins, pet_id),
        )
        await db.commit()
    return await get_pet_by_id(pet_id), None


async def _apply_progress_in_tx(db, pet_id, xp=0, coins=0):
    """Update XP/level/coins inside an already-open transaction."""
    cur = await db.execute('SELECT xp, level, coins FROM pets WHERE id=?', (pet_id,))
    p = await cur.fetchone()
    if not p:
        return None, 0

    level = max(1, int(p['level'] or 1))
    old_stage = _stage_key_for_level(level)
    new_xp = max(0, int(p['xp'] or 0) + int(xp))
    new_coins = max(0, int(p['coins'] or 0) + int(coins))
    gained = 0
    while new_xp >= 100 * level:
        new_xp -= 100 * level
        level += 1
        gained += 1

    await db.execute(
        'UPDATE pets SET xp=?, level=?, coins=? WHERE id=?',
        (new_xp, level, new_coins, pet_id),
    )
    new_stage = _stage_key_for_level(level)
    if new_stage != old_stage:
        await _add_history_in_tx(
            db, pet_id, 'growth', '🌱 Mochi вырос',
            f'Новый этап: {_stage_title(new_stage)}.', dedupe_key=f'stage:{new_stage}',
        )
    return (new_xp, level, new_coins), gained


async def add_progress(pet_id, xp=0, coins=0):
    """Добавляет XP/монеты атомарно и возвращает (pet, levels_gained)."""
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        result, gained = await _apply_progress_in_tx(db, pet_id, xp=xp, coins=coins)
        if result is None:
            await db.rollback()
            return None, 0
        await db.commit()
    return await get_pet_by_id(pet_id), gained



async def spend_coins(pet_id, amount):
    amount = max(0, int(amount))
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT coins FROM pets WHERE id=?', (pet_id,))
        row = await cur.fetchone()
        if not row or row['coins'] < amount:
            await db.rollback()
            return False
        await db.execute('UPDATE pets SET coins=coins-? WHERE id=?', (amount, pet_id))
        await db.commit()
        return True


async def _register_joint_streak_in_tx(db, pet_id: int, action_key: str):
    """Advance the shared streak only after a completed joint action, at most once per UTC day."""
    if action_key not in JOINT_STREAK_ACTIONS:
        return None

    day = today_key()
    yesterday = (now_utc().date() - timedelta(days=1)).isoformat()
    cur = await db.execute(
        'SELECT joint_streak_current, joint_streak_best, last_joint_streak_day, joint_streak_days_total '
        'FROM pet_metrics WHERE pet_id=?',
        (pet_id,),
    )
    metrics = await cur.fetchone()
    if not metrics:
        return None

    if metrics['last_joint_streak_day'] == day:
        return {
            'new_day': False,
            'streak': int(metrics['joint_streak_current'] or 0),
            'best_streak': int(metrics['joint_streak_best'] or 0),
            'joint_streak_days_total': int(metrics['joint_streak_days_total'] or 0),
        }

    previous = int(metrics['joint_streak_current'] or 0)
    streak = previous + 1 if metrics['last_joint_streak_day'] == yesterday else 1
    best = max(int(metrics['joint_streak_best'] or 0), streak)
    total_days = int(metrics['joint_streak_days_total'] or 0) + 1
    await db.execute(
        '''UPDATE pet_metrics
           SET joint_streak_current=?, joint_streak_best=?, last_joint_streak_day=?, joint_streak_days_total=?
           WHERE pet_id=?''',
        (streak, best, day, total_days, pet_id),
    )
    return {
        'new_day': True,
        'streak': streak,
        'best_streak': best,
        'joint_streak_days_total': total_days,
    }


async def record_action(pet_id, action_key):
    # Ежедневки должны существовать до начала транзакции действия.
    await ensure_daily_tasks(pet_id)
    field = {
        'feed': 'feed_total', 'play': 'play_total', 'love': 'love_total',
        'sleep': 'sleep_total', 'joint': 'joint_total'
    }.get(action_key)

    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) VALUES(?)', (pet_id,))
        if field:
            await db.execute(
                f'UPDATE pet_metrics SET actions_total=actions_total+1, {field}={field}+1 WHERE pet_id=?',
                (pet_id,),
            )
        else:
            await db.execute(
                'UPDATE pet_metrics SET actions_total=actions_total+1 WHERE pet_id=?',
                (pet_id,),
            )
        joint_streak = await _register_joint_streak_in_tx(db, pet_id, action_key)
        completed = await _progress_daily_tasks_in_tx(db, pet_id, action_key, today_key())
        await db.commit()
    return completed, joint_streak


async def ensure_daily_tasks(pet_id):
    day = today_key()
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT COUNT(*) AS c FROM daily_tasks WHERE pet_id=? AND day=?', (pet_id, day))
        count = (await cur.fetchone())['c']
        if count < DAILY_TASKS_PER_DAY:
            cur = await db.execute(
                'SELECT task_key FROM daily_tasks WHERE pet_id=? AND day=?',
                (pet_id, day),
            )
            current_keys = {r['task_key'] for r in await cur.fetchall()}

            # Берём последний предыдущий набор и выкидываем его из пула.
            # Так набор ежедневок действительно меняется между днями.
            cur = await db.execute(
                'SELECT day FROM daily_tasks WHERE pet_id=? AND day<? ORDER BY day DESC LIMIT 1',
                (pet_id, day),
            )
            prev = await cur.fetchone()
            previous_keys = set()
            if prev:
                cur = await db.execute(
                    'SELECT task_key FROM daily_tasks WHERE pet_id=? AND day=?',
                    (pet_id, prev['day']),
                )
                previous_keys = {r['task_key'] for r in await cur.fetchall()}

            seed = int(hashlib.sha256(f'{pet_id}:{day}:daily-v20'.encode()).hexdigest()[:16], 16)
            pool = [
                task for task in DAILY_POOL
                if task[0] not in previous_keys and task[0] not in current_keys
            ]
            needed = DAILY_TASKS_PER_DAY - count
            if len(pool) < needed:
                pool = [task for task in DAILY_POOL if task[0] not in current_keys]

            chosen = []
            while pool and len(chosen) < needed:
                idx = seed % len(pool)
                chosen.append(pool.pop(idx))
                seed = ((seed >> 3) ^ (seed * 1103515245 + 12345)) & ((1 << 63) - 1)

            for key, title, action, target, coins, xp in chosen:
                await db.execute(
                    '''INSERT OR IGNORE INTO daily_tasks
                    (pet_id,day,task_key,title,action_key,target,reward_coins,reward_xp)
                    VALUES(?,?,?,?,?,?,?,?)''',
                    (pet_id, day, key, title, action, target, coins, xp),
                )
            await db.commit()

        cur = await db.execute('SELECT * FROM daily_tasks WHERE pet_id=? AND day=? ORDER BY id', (pet_id, day))
        return await cur.fetchall()


async def _progress_daily_tasks_in_tx(db, pet_id, action_key, day):
    completed_now = []
    cur = await db.execute(
        'SELECT * FROM daily_tasks WHERE pet_id=? AND day=? AND completed=0',
        (pet_id, day),
    )
    rows = await cur.fetchall()
    for t in rows:
        allowed = t['action_key'].split('|')
        if t['action_key'] != 'any' and action_key not in allowed:
            continue
        new_progress = min(t['target'], t['progress'] + 1)
        done = int(new_progress >= t['target'])
        await db.execute(
            'UPDATE daily_tasks SET progress=?, completed=? WHERE id=?',
            (new_progress, done, t['id']),
        )
        if done:
            completed_now.append(dict(t) | {'progress': new_progress, 'completed': 1})
            await db.execute(
                'UPDATE pet_metrics SET dailies_completed=dailies_completed+1 WHERE pet_id=?',
                (pet_id,),
            )
            # Coins + XP начисляются в той же транзакции, поэтому рестарт между
            # completed=1 и reward больше не может съесть часть награды.
            await _apply_progress_in_tx(
                db,
                pet_id,
                xp=t['reward_xp'],
                coins=t['reward_coins'],
            )
    return completed_now


async def claim_daily_completion_bonus(pet_id):
    """Grant the once-per-day bonus for completing every generated daily quest."""
    await ensure_daily_tasks(pet_id)
    day = today_key()
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute(
            'SELECT COUNT(*) AS total, SUM(completed) AS done FROM daily_tasks WHERE pet_id=? AND day=?',
            (pet_id, day),
        )
        counts = await cur.fetchone()
        total = int(counts['total'] or 0)
        done = int(counts['done'] or 0)
        if total < DAILY_TASKS_PER_DAY or done < total:
            await db.rollback()
            return None

        cur = await db.execute(
            '''INSERT OR IGNORE INTO daily_bonus_claims(pet_id,day,reward_coins,reward_xp,claimed_at)
               VALUES(?,?,?,?,?)''',
            (pet_id, day, DAILY_COMPLETION_BONUS_COINS, DAILY_COMPLETION_BONUS_XP, now_iso()),
        )
        if cur.rowcount != 1:
            await db.rollback()
            return None

        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) VALUES(?)', (pet_id,))
        await db.execute(
            'UPDATE pet_metrics SET daily_full_clears=daily_full_clears+1 WHERE pet_id=?',
            (pet_id,),
        )
        await _apply_progress_in_tx(
            db,
            pet_id,
            xp=DAILY_COMPLETION_BONUS_XP,
            coins=DAILY_COMPLETION_BONUS_COINS,
        )
        await _add_history_in_tx(
            db, pet_id, 'daily', '📅 Идеальный день',
            f'Закрыты все {DAILY_TASKS_PER_DAY} ежедневных задания.', dedupe_key=f'dailyclear:{day}',
        )
        await db.commit()
    return {
        'coins': DAILY_COMPLETION_BONUS_COINS,
        'xp': DAILY_COMPLETION_BONUS_XP,
        'day': day,
    }


async def get_daily_overview(pet_id):
    tasks = await ensure_daily_tasks(pet_id)
    day = today_key()
    yesterday = (now_utc().date() - timedelta(days=1)).isoformat()
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) VALUES(?)', (pet_id,))
        await db.commit()
        cur = await db.execute('SELECT * FROM pet_metrics WHERE pet_id=?', (pet_id,))
        metrics = await cur.fetchone()
        cur = await db.execute('SELECT 1 FROM daily_bonus_claims WHERE pet_id=? AND day=?', (pet_id, day))
        bonus_claimed = bool(await cur.fetchone())
    visible_streak = int(metrics['joint_streak_current'] or 0)
    if metrics['last_joint_streak_day'] not in {day, yesterday}:
        visible_streak = 0
    return {
        'tasks': tasks,
        'completed': sum(1 for t in tasks if t['completed']),
        'total': len(tasks),
        'bonus_claimed': bonus_claimed,
        'bonus_coins': DAILY_COMPLETION_BONUS_COINS,
        'bonus_xp': DAILY_COMPLETION_BONUS_XP,
        'streak': visible_streak,
        'best_streak': int(metrics['joint_streak_best'] or 0),
        'joint_streak_days_total': int(metrics['joint_streak_days_total'] or 0),
        'daily_full_clears': int(metrics['daily_full_clears'] or 0),
    }


async def get_due_random_event_pet_ids(limit=50):
    now = now_utc()
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            '''SELECT pm.pet_id, pm.next_random_event_at
               FROM pet_metrics pm
               JOIN pets p ON p.id=pm.pet_id
               JOIN couples c ON c.id=p.couple_id
               WHERE c.user2_id IS NOT NULL
               ORDER BY pm.pet_id'''
        )
        rows = await cur.fetchall()
    due = []
    for row in rows:
        scheduled = parse_dt(row['next_random_event_at'])
        if scheduled is not None and scheduled <= now:
            due.append(int(row['pet_id']))
            if len(due) >= max(1, int(limit)):
                break
    return due


async def trigger_random_event(pet_id):
    """Apply one due random event atomically, optionally drop an item, and schedule the next one."""
    now = now_utc()
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) VALUES(?)', (pet_id,))
        cur = await db.execute('SELECT next_random_event_at FROM pet_metrics WHERE pet_id=?', (pet_id,))
        metrics = await cur.fetchone()
        scheduled = parse_dt(metrics['next_random_event_at']) if metrics else None
        if scheduled is None:
            await db.execute('UPDATE pet_metrics SET next_random_event_at=? WHERE pet_id=?', (_next_random_event_at(now), pet_id))
            await db.commit()
            return None
        if scheduled > now:
            await db.rollback()
            return None

        cur = await db.execute('SELECT * FROM pets WHERE id=?', (pet_id,))
        pet = await cur.fetchone()
        if not pet:
            await db.rollback()
            return None

        event = _choose_random_event()
        target_hunger = max(0, min(100, int(pet['hunger']) + int(event.get('hunger', 0))))
        target_happiness = max(0, min(100, int(pet['happiness']) + int(event.get('happiness', 0))))
        target_energy = max(0, min(100, int(pet['energy']) + int(event.get('energy', 0))))
        target_love = max(0, min(100, int(pet['love']) + int(event.get('love', 0))))
        actual = {
            'hunger': target_hunger - int(pet['hunger']),
            'happiness': target_happiness - int(pet['happiness']),
            'energy': target_energy - int(pet['energy']),
            'love': target_love - int(pet['love']),
            'coins': max(0, int(event.get('coins', 0))),
        }

        dropped_item = None
        drop_mode = event.get('drop_mode')
        if drop_mode:
            cur = await db.execute('SELECT item_key FROM inventory WHERE pet_id=? AND quantity>0', (pet_id,))
            owned = {row['item_key'] for row in await cur.fetchall()}
            if drop_mode == 'drop_only':
                candidates = [key for key in sorted(DROP_ONLY_ITEMS) if key in SHOP_ITEMS and key not in owned]
            else:
                candidates = [key for key, item in SHOP_ITEMS.items() if int(item.get('rarity_rank', 0)) >= 2 and key not in owned]
            if candidates:
                dropped_item = secrets.choice(candidates)
                await db.execute('INSERT INTO inventory(pet_id,item_key,quantity) VALUES(?,?,1) ON CONFLICT(pet_id,item_key) DO UPDATE SET quantity=MAX(1, quantity)', (pet_id, dropped_item))
                await _add_history_in_tx(db, pet_id, 'drop', '🎁 Редкая находка', _history_item_text(dropped_item), dedupe_key=f'item:{dropped_item}')
            else:
                actual['coins'] += 40

        await db.execute('UPDATE pets SET hunger=?,happiness=?,energy=?,love=?,coins=coins+? WHERE id=?', (target_hunger, target_happiness, target_energy, target_love, actual['coins'], pet_id))
        next_at = _next_random_event_at(now)
        await db.execute('UPDATE pet_metrics SET random_events_total=random_events_total+1, next_random_event_at=? WHERE pet_id=?', (next_at, pet_id))
        await db.execute('INSERT INTO random_event_log(pet_id,event_key,happened_at,coins_delta,hunger_delta,happiness_delta,energy_delta,love_delta,item_key) VALUES(?,?,?,?,?,?,?,?,?)', (pet_id, event['key'], now.isoformat(), actual['coins'], actual['hunger'], actual['happiness'], actual['energy'], actual['love'], dropped_item))
        await db.commit()

    return {
        'pet_id': pet_id,
        'key': event['key'],
        'text': event['text'],
        'effects': actual,
        'item_key': dropped_item,
        'next_event_at': next_at,
    }

async def progress_daily_tasks(pet_id, action_key):
    await ensure_daily_tasks(pet_id)
    day = today_key()
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) VALUES(?)', (pet_id,))
        completed_now = await _progress_daily_tasks_in_tx(db, pet_id, action_key, day)
        await db.commit()
    return completed_now


async def get_daily_tasks(pet_id):
    return await ensure_daily_tasks(pet_id)


async def get_minigame_cooldown(user_id: int, pet_id: int):
    '''Return remaining cooldown seconds for rewarded mini-games.'''
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            '''SELECT completed_at FROM minigame_sessions
               WHERE user_id=? AND pet_id=? AND completed=1 AND completed_at IS NOT NULL
               ORDER BY completed_at DESC LIMIT 1''',
            (int(user_id), int(pet_id)),
        )
        row = await cur.fetchone()
    if not row:
        return 0
    completed_at = parse_dt(row['completed_at'])
    if not completed_at:
        return 0
    ready_at = completed_at + timedelta(minutes=MINIGAME_COOLDOWN_MINUTES)
    return max(0, int((ready_at - now_utc()).total_seconds()))


async def create_minigame_session(user_id: int, pet_id: int, game_key: str):
    '''Create one short-lived mini-game session after checking the reward cooldown.'''
    game_key = str(game_key or '').strip().lower()
    if game_key not in {'boxes', 'rps', 'higher'}:
        return False, {'reason': 'unknown_game'}

    cooldown = await get_minigame_cooldown(user_id, pet_id)
    if cooldown > 0:
        return False, {'reason': 'cooldown', 'seconds': cooldown}

    if game_key == 'boxes':
        payload = {'target': 1 + secrets.randbelow(3)}
    elif game_key == 'rps':
        payload = {'bot': ('rock', 'paper', 'scissors')[secrets.randbelow(3)]}
    else:
        current = 2 + secrets.randbelow(7)
        nxt = 1 + secrets.randbelow(9)
        payload = {'current': current, 'next': nxt}

    token = secrets.token_urlsafe(6)
    created = now_utc()
    expires = created + timedelta(minutes=MINIGAME_SESSION_MINUTES)
    async with db_connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        await db.execute(
            '''UPDATE minigame_sessions SET result='expired'
               WHERE user_id=? AND pet_id=? AND completed=0 AND expires_at<?''',
            (int(user_id), int(pet_id), created.isoformat()),
        )
        await db.execute(
            '''INSERT INTO minigame_sessions(token,user_id,pet_id,game_key,payload,created_at,expires_at)
               VALUES(?,?,?,?,?,?,?)''',
            (token, int(user_id), int(pet_id), game_key, json.dumps(payload), created.isoformat(), expires.isoformat()),
        )
        await db.commit()
    return True, {'token': token, 'game_key': game_key, 'payload': payload, 'expires_at': expires.isoformat()}


def _resolve_minigame_outcome(game_key: str, payload: dict, answer: str):
    answer = str(answer or '').strip().lower()
    if game_key == 'boxes':
        try:
            picked = int(answer)
        except ValueError:
            return None
        if picked not in {1, 2, 3}:
            return None
        won = picked == int(payload['target'])
        return {
            'result': 'win' if won else 'loss',
            'coins': 24 if won else 3,
            'xp': 12 if won else 3,
            'happiness': 4 if won else 1,
            'details': {'picked': picked, 'target': int(payload['target'])},
        }

    if game_key == 'rps':
        if answer not in {'rock', 'paper', 'scissors'}:
            return None
        bot = str(payload['bot'])
        wins = {('rock', 'scissors'), ('paper', 'rock'), ('scissors', 'paper')}
        if answer == bot:
            result, coins, xp, happiness = 'draw', 7, 5, 2
        elif (answer, bot) in wins:
            result, coins, xp, happiness = 'win', 18, 10, 4
        else:
            result, coins, xp, happiness = 'loss', 3, 3, 1
        return {
            'result': result, 'coins': coins, 'xp': xp, 'happiness': happiness,
            'details': {'player': answer, 'bot': bot},
        }

    if game_key == 'higher':
        if answer not in {'higher', 'lower'}:
            return None
        current = int(payload['current'])
        nxt = int(payload['next'])
        if nxt == current:
            return {
                'result': 'draw', 'coins': 6, 'xp': 4, 'happiness': 2,
                'details': {'current': current, 'next': nxt, 'answer': answer},
            }
        correct = 'higher' if nxt > current else 'lower'
        won = answer == correct
        return {
            'result': 'win' if won else 'loss',
            'coins': 16 if won else 3,
            'xp': 9 if won else 3,
            'happiness': 3 if won else 1,
            'details': {'current': current, 'next': nxt, 'answer': answer, 'correct': correct},
        }
    return None


async def resolve_minigame_session(user_id: int, token: str, answer: str):
    '''Resolve exactly once and grant rewards atomically. Cooldown starts on completion.'''
    token = str(token or '').strip()
    if not token:
        return False, {'reason': 'invalid'}

    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT * FROM minigame_sessions WHERE token=?', (token,))
        row = await cur.fetchone()
        if not row or int(row['user_id']) != int(user_id):
            await db.rollback()
            return False, {'reason': 'not_found'}
        if row['completed']:
            await db.rollback()
            return False, {'reason': 'already_done'}
        expires = parse_dt(row['expires_at'])
        now = now_utc()
        if not expires or expires < now:
            await db.execute("UPDATE minigame_sessions SET result='expired' WHERE token=?", (token,))
            await db.commit()
            return False, {'reason': 'expired'}

        cur = await db.execute(
            '''SELECT completed_at FROM minigame_sessions
               WHERE user_id=? AND pet_id=? AND completed=1 AND completed_at IS NOT NULL
               ORDER BY completed_at DESC LIMIT 1''',
            (int(user_id), int(row['pet_id'])),
        )
        last = await cur.fetchone()
        if last:
            last_dt = parse_dt(last['completed_at'])
            if last_dt:
                remaining = int(((last_dt + timedelta(minutes=MINIGAME_COOLDOWN_MINUTES)) - now).total_seconds())
                if remaining > 0:
                    await db.rollback()
                    return False, {'reason': 'cooldown', 'seconds': remaining}

        try:
            payload = json.loads(row['payload'])
        except (TypeError, ValueError, json.JSONDecodeError):
            await db.rollback()
            return False, {'reason': 'invalid'}
        outcome = _resolve_minigame_outcome(row['game_key'], payload, answer)
        if not outcome:
            await db.rollback()
            return False, {'reason': 'invalid_answer'}

        pet_id = int(row['pet_id'])
        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) VALUES(?)', (pet_id,))
        await db.execute(
            '''UPDATE pet_metrics SET actions_total=actions_total+1,
               minigames_total=minigames_total+1 WHERE pet_id=?''',
            (pet_id,),
        )
        cur = await db.execute('SELECT happiness,energy FROM pets WHERE id=?', (pet_id,))
        pet = await cur.fetchone()
        if not pet:
            await db.rollback()
            return False, {'reason': 'no_pet'}
        happiness = max(0, min(100, int(pet['happiness']) + int(outcome['happiness'])))
        energy = max(0, min(100, int(pet['energy']) - 2))
        await db.execute('UPDATE pets SET happiness=?,energy=? WHERE id=?', (happiness, energy, pet_id))
        await _apply_progress_in_tx(db, pet_id, xp=int(outcome['xp']), coins=int(outcome['coins']))
        completed_tasks = await _progress_daily_tasks_in_tx(db, pet_id, 'minigame', today_key())
        await db.execute(
            '''UPDATE minigame_sessions SET completed=1,completed_at=?,result=?,reward_coins=?,reward_xp=?
               WHERE token=? AND completed=0''',
            (now.isoformat(), outcome['result'], int(outcome['coins']), int(outcome['xp']), token),
        )
        await db.commit()

    return True, {
        'reason': 'ok', 'pet_id': pet_id, 'game_key': row['game_key'],
        'result': outcome['result'], 'coins': int(outcome['coins']), 'xp': int(outcome['xp']),
        'happiness': int(outcome['happiness']), 'energy': -2,
        'details': outcome['details'], 'completed_tasks': completed_tasks,
        'cooldown_minutes': MINIGAME_COOLDOWN_MINUTES,
    }


async def get_metrics(pet_id):
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) VALUES(?)', (pet_id,))
        await db.commit()
        cur = await db.execute('SELECT * FROM pet_metrics WHERE pet_id=?', (pet_id,))
        return await cur.fetchone()


async def check_achievements(pet_id):
    """Unlock all currently reachable achievements and grant rewards atomically.

    Achievement rewards can themselves cross a level or coin threshold, so conditions
    are re-evaluated until no additional achievement can be unlocked.
    """
    unlocked_now = []
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) VALUES(?)', (pet_id,))

        for _ in range(len(ACHIEVEMENTS) + 1):
            cur = await db.execute('SELECT * FROM pets WHERE id=?', (pet_id,))
            p = await cur.fetchone()
            cur = await db.execute('SELECT * FROM pet_metrics WHERE pet_id=?', (pet_id,))
            m = await cur.fetchone()
            if not p or not m:
                await db.rollback()
                return []

            cur = await db.execute('SELECT item_key FROM inventory WHERE pet_id=? AND quantity>0', (pet_id,))
            inventory_keys = [row['item_key'] for row in await cur.fetchall()]
            inventory_count = len(inventory_keys)
            rarity_ranks = [int(SHOP_ITEMS.get(key, {}).get('rarity_rank', 0)) for key in inventory_keys]
            rare_count = sum(rank >= 2 for rank in rarity_ranks)
            epic_count = sum(rank >= 3 for rank in rarity_ranks)
            legendary_count = sum(rank >= 4 for rank in rarity_ranks)
            cur = await db.execute('SELECT COUNT(*) FROM room_equipment WHERE pet_id=?', (pet_id,))
            equipped_count = (await cur.fetchone())[0]
            created = parse_dt(p['created_at'])
            age_days = max(0, (now_utc() - created).days) if created else 0

            conditions = {
                'first_care': m['actions_total'] >= 1,
                'actions10': m['actions_total'] >= 10,
                'actions25': m['actions_total'] >= 25,
                'actions50': m['actions_total'] >= 50,
                'actions100': m['actions_total'] >= 100,
                'actions250': m['actions_total'] >= 250,
                'actions500': m['actions_total'] >= 500,
                'feed10': m['feed_total'] >= 10,
                'feed25': m['feed_total'] >= 25,
                'feed50': m['feed_total'] >= 50,
                'feed100': m['feed_total'] >= 100,
                'play10': m['play_total'] >= 10,
                'play25': m['play_total'] >= 25,
                'play50': m['play_total'] >= 50,
                'love10': m['love_total'] >= 10,
                'love25': m['love_total'] >= 25,
                'love50': m['love_total'] >= 50,
                'sleep10': m['sleep_total'] >= 10,
                'sleep25': m['sleep_total'] >= 25,
                'sleep50': m['sleep_total'] >= 50,
                'joint1': m['joint_total'] >= 1,
                'joint5': m['joint_total'] >= 5,
                'joint10': m['joint_total'] >= 10,
                'joint25': m['joint_total'] >= 25,
                'level2': p['level'] >= 2,
                'level5': p['level'] >= 5,
                'level10': p['level'] >= 10,
                'level20': p['level'] >= 20,
                'coins100': p['coins'] >= 100,
                'coins500': p['coins'] >= 500,
                'coins1000': p['coins'] >= 1000,
                'dailies1': m['dailies_completed'] >= 1,
                'dailies5': m['dailies_completed'] >= 5,
                'dailies10': m['dailies_completed'] >= 10,
                'dailies25': m['dailies_completed'] >= 25,
                'dailies50': m['dailies_completed'] >= 50,
                'dailies100': m['dailies_completed'] >= 100,
                'dailyclear1': m['daily_full_clears'] >= 1,
                'dailyclear7': m['daily_full_clears'] >= 7,
                'dailyclear30': m['daily_full_clears'] >= 30,
                'streak3': m['joint_streak_best'] >= 3,
                'streak7': m['joint_streak_best'] >= 7,
                'streak14': m['joint_streak_best'] >= 14,
                'streak30': m['joint_streak_best'] >= 30,
                'streak60': m['joint_streak_best'] >= 60,
                'streak100': m['joint_streak_best'] >= 100,
                'events5': m['random_events_total'] >= 5,
                'events20': m['random_events_total'] >= 20,
                'events50': m['random_events_total'] >= 50,
                'games10': m['minigames_total'] >= 10,
                'games50': m['minigames_total'] >= 50,
                'games100': m['minigames_total'] >= 100,
                'collector3': inventory_count >= 3,
                'collector7': inventory_count >= 7,
                'collector12': inventory_count >= 12,
                'rare_item1': rare_count >= 1,
                'epic_item1': epic_count >= 1,
                'legendary_item1': legendary_count >= 1,
                'decorator4': equipped_count >= 4,
                'decorator8': equipped_count >= 8,
                'love100': p['love'] >= 100,
                'happy100': p['happiness'] >= 100,
                'energy100': p['energy'] >= 100,
                'hunger100': p['hunger'] >= 100,
                'week_old': age_days >= 7,
                'month_old': age_days >= 30,
            }

            added_this_pass = 0
            for key, ok in conditions.items():
                if not ok:
                    continue
                cur = await db.execute(
                    'INSERT OR IGNORE INTO achievements(pet_id,achievement_key,unlocked_at) VALUES(?,?,?)',
                    (pet_id, key, now_iso()),
                )
                if cur.rowcount != 1:
                    continue
                unlocked_now.append(key)
                added_this_pass += 1
                title, _, coins, xp = ACHIEVEMENTS[key]
                await _add_history_in_tx(
                    db, pet_id, 'achievement', '🏆 Получено достижение', title,
                    dedupe_key=f'achievement:{key}',
                )
                await _apply_progress_in_tx(db, pet_id, xp=xp, coins=coins)

            if added_this_pass == 0:
                break
        else:
            await db.rollback()
            raise RuntimeError('Achievement evaluation did not converge.')

        await db.commit()
    return unlocked_now


async def get_pet_profile(pet_id):
    """Return a compact production-safe profile snapshot for one Mochi."""
    day = today_key()
    yesterday = (now_utc().date() - timedelta(days=1)).isoformat()
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('INSERT OR IGNORE INTO pet_metrics(pet_id) VALUES(?)', (pet_id,))
        await db.commit()

        cur = await db.execute('SELECT * FROM pets WHERE id=?', (pet_id,))
        pet = await cur.fetchone()
        cur = await db.execute('SELECT * FROM pet_metrics WHERE pet_id=?', (pet_id,))
        metrics = await cur.fetchone()
        if not pet or not metrics:
            return None

        cur = await db.execute('SELECT item_key FROM inventory WHERE pet_id=? AND quantity>0', (pet_id,))
        inventory_keys = [row['item_key'] for row in await cur.fetchall()]
        rarity_counts = {'common': 0, 'uncommon': 0, 'rare': 0, 'epic': 0, 'legendary': 0}
        rarity_totals = {key: 0 for key in rarity_counts}
        for item in SHOP_ITEMS.values():
            rarity = str(item.get('rarity', 'common'))
            if rarity in rarity_totals:
                rarity_totals[rarity] += 1
        for key in inventory_keys:
            rarity = str(SHOP_ITEMS.get(key, {}).get('rarity', 'common'))
            if rarity in rarity_counts:
                rarity_counts[rarity] += 1

        cur = await db.execute('SELECT COUNT(*) AS c FROM achievements WHERE pet_id=?', (pet_id,))
        achievements_count = int((await cur.fetchone())['c'])
        cur = await db.execute('SELECT COUNT(*) AS c FROM room_equipment WHERE pet_id=?', (pet_id,))
        equipped_count = int((await cur.fetchone())['c'])

    created = parse_dt(pet['created_at'])
    age_days = max(0, (now_utc() - created).days) if created else 0
    joint_streak = int(metrics['joint_streak_current'] or 0)
    if metrics['last_joint_streak_day'] not in {day, yesterday}:
        joint_streak = 0

    return {
        'pet': dict(pet),
        'age_days': age_days,
        'metrics': dict(metrics),
        'joint_streak': joint_streak,
        'joint_streak_best': int(metrics['joint_streak_best'] or 0),
        'joint_streak_days_total': int(metrics['joint_streak_days_total'] or 0),
        'inventory_total': len(inventory_keys),
        'catalog_total': len(SHOP_ITEMS),
        'rarity_counts': rarity_counts,
        'rarity_totals': rarity_totals,
        'drop_only_owned': sum(1 for key in inventory_keys if key in DROP_ONLY_ITEMS),
        'drop_only_total': len(DROP_ONLY_ITEMS),
        'equipped_count': equipped_count,
        'achievements_count': achievements_count,
        'achievements_total': len(ACHIEVEMENTS),
    }


async def get_achievements(pet_id):
    async with db_connection() as db:
        cur = await db.execute('SELECT achievement_key FROM achievements WHERE pet_id=?', (pet_id,))
        return {r[0] for r in await cur.fetchall()}


async def get_shop_rotation(pet_id: int, day: str | None = None):
    day = day or today_key()
    keys = _shop_rotation_keys(int(pet_id), day)
    return {key: SHOP_ITEMS[key] for key in keys if key in SHOP_ITEMS}


async def get_pet_history(pet_id: int, limit: int = 10, offset: int = 0):
    limit = max(1, min(20, int(limit)))
    offset = max(0, int(offset))
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT COUNT(*) AS c FROM pet_history WHERE pet_id=?', (pet_id,))
        total = int((await cur.fetchone())['c'])
        cur = await db.execute(
            'SELECT id,kind,title,details,created_at FROM pet_history WHERE pet_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
            (pet_id, limit, offset),
        )
        rows = [dict(row) for row in await cur.fetchall()]
    return rows, total


async def purchase_item(pet_id, item_key, price=None):
    """Atomically buy one catalog item using the server-side catalog price."""
    item = SHOP_ITEMS.get(item_key)
    if not item:
        return False, 'unknown_item'
    if item_key in DROP_ONLY_ITEMS or item_key not in _shop_rotation_keys(int(pet_id), today_key()):
        return False, 'not_in_rotation'
    # price остаётся необязательным аргументом только для совместимости со старыми вызовами.
    # Источником истины всегда является каталог, поэтому подмена цены не влияет на списание.
    price = max(0, int(item['price']))
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT quantity FROM inventory WHERE pet_id=? AND item_key=?', (pet_id, item_key))
        owned = await cur.fetchone()
        if owned and owned['quantity'] > 0:
            await db.rollback()
            return False, 'owned'
        cur = await db.execute('SELECT coins FROM pets WHERE id=?', (pet_id,))
        pet = await cur.fetchone()
        if not pet:
            await db.rollback()
            return False, 'missing_pet'
        if pet['coins'] < price:
            await db.rollback()
            return False, 'not_enough_coins'
        await db.execute('UPDATE pets SET coins=coins-? WHERE id=?', (price, pet_id))
        await db.execute('''INSERT INTO inventory(pet_id,item_key,quantity) VALUES(?,?,1)
            ON CONFLICT(pet_id,item_key) DO UPDATE SET quantity=1''', (pet_id, item_key))
        await _add_history_in_tx(
            db, pet_id, 'purchase', '🛍 Новый предмет', _history_item_text(item_key),
            dedupe_key=f'item:{item_key}',
        )
        await db.commit()
        return True, 'ok'


async def has_item(pet_id, item_key):
    async with db_connection() as db:
        cur = await db.execute('SELECT quantity FROM inventory WHERE pet_id=? AND item_key=?', (pet_id, item_key))
        r = await cur.fetchone()
        return bool(r and r[0] > 0)


async def add_item(pet_id, item_key):
    if item_key not in SHOP_ITEMS:
        return False
    async with db_connection() as db:
        await db.execute('''INSERT INTO inventory(pet_id,item_key,quantity) VALUES(?,?,1)
            ON CONFLICT(pet_id,item_key) DO UPDATE SET quantity=quantity+1''', (pet_id, item_key))
        await db.commit()
    return True


async def get_inventory(pet_id):
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT * FROM inventory WHERE pet_id=? AND quantity>0 ORDER BY item_key', (pet_id,))
        return await cur.fetchall()


async def equip_item(pet_id, slot, item_key):
    item = SHOP_ITEMS.get(item_key)
    if not item or item['slot'] != slot:
        return False
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute(
            'SELECT quantity FROM inventory WHERE pet_id=? AND item_key=?',
            (pet_id, item_key),
        )
        owned = await cur.fetchone()
        if not owned or owned['quantity'] <= 0:
            await db.rollback()
            return False
        await db.execute('''INSERT INTO room_equipment(pet_id,slot,item_key) VALUES(?,?,?)
            ON CONFLICT(pet_id,slot) DO UPDATE SET item_key=excluded.item_key''', (pet_id, slot, item_key))
        await db.commit()
    return True


async def get_room_equipment(pet_id):
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT * FROM room_equipment WHERE pet_id=?', (pet_id,))
        return {r['slot']: r['item_key'] for r in await cur.fetchall()}


async def create_joint_action(pet_id, starter_user_id, action_key='cuddle'):
    # Один незавершённый совместный экшен на питомца.
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute(
            '''SELECT c.user1_id,c.user2_id FROM couples c
               JOIN pets p ON p.couple_id=c.id WHERE p.id=?''',
            (pet_id,),
        )
        owners = await cur.fetchone()
        if not owners or starter_user_id not in (owners['user1_id'], owners['user2_id']):
            await db.rollback()
            return None, 'Это не ваш Mochi.'
        if owners['user2_id'] is None:
            await db.rollback()
            return None, 'Нужен второй хозяин 💞'

        cur = await db.execute(
            "SELECT * FROM joint_actions WHERE pet_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
            (pet_id,),
        )
        pending = await cur.fetchone()
        if pending:
            created = parse_dt(pending['created_at'])
            if created and (now_utc() - created).total_seconds() < 600:
                await db.rollback()
                return None, 'У этого Mochi уже есть совместное действие — второй хозяин должен его завершить 💞'
            await db.execute("UPDATE joint_actions SET status='expired' WHERE id=?", (pending['id'],))

        try:
            cur = await db.execute(
                'INSERT INTO joint_actions(pet_id,action_key,starter_user_id,created_at) VALUES(?,?,?,?)',
                (pet_id, action_key, starter_user_id, now_iso()),
            )
        except sqlite3.IntegrityError:
            await db.rollback()
            return None, 'У этого Mochi уже есть совместное действие — второй хозяин должен его завершить 💞'
        jid = cur.lastrowid
        await db.commit()
    return jid, None


async def complete_joint_action(joint_id, user_id):
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT * FROM joint_actions WHERE id=?', (joint_id,))
        row = await cur.fetchone()
        if not row or row['status'] != 'pending':
            await db.rollback()
            return False, 'Это совместное действие уже закрыто.'
        if row['starter_user_id'] == user_id:
            await db.rollback()
            return False, 'Нужен второй хозяин 😼'
        created = parse_dt(row['created_at'])
        if not created or (now_utc() - created).total_seconds() >= 600:
            await db.execute("UPDATE joint_actions SET status='expired' WHERE id=?", (joint_id,))
            await db.commit()
            return False, 'Вы не успели — совместное действие протухло 🥲'
        cur = await db.execute('SELECT c.user1_id,c.user2_id FROM couples c JOIN pets p ON p.couple_id=c.id WHERE p.id=?', (row['pet_id'],))
        cp = await cur.fetchone()
        if not cp or user_id not in (cp['user1_id'], cp['user2_id']):
            await db.rollback()
            return False, 'Это не ваш Mochi.'
        cur = await db.execute(
            "UPDATE joint_actions SET status='done',completed_by=?,completed_at=? WHERE id=? AND status='pending'",
            (user_id, now_iso(), joint_id),
        )
        if cur.rowcount != 1:
            await db.rollback()
            return False, 'Это совместное действие уже закрыто.'
        await db.commit()
    return True, row['pet_id']


async def both_inactive_for_hours_for_couple(couple_id, hours=24):
    c = await get_couple_by_id(couple_id)
    if not c or not c['user2_id']:
        return False
    ids = [c['user1_id'], c['user2_id']]
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT id,last_active_at FROM users WHERE id IN (?,?)', ids)
        rows = await cur.fetchall()
    if len(rows) < 2:
        return False
    now = now_utc()
    timestamps = [parse_dt(r['last_active_at']) for r in rows]
    return all(ts is not None and (now - ts).total_seconds() >= hours * 3600 for ts in timestamps)


async def apply_decay_to_all_pets():
    """Apply due hourly decay and return pets whose visible stats changed.

    Timestamp parsing is intentionally done in Python instead of SQLite date functions. Old
    databases can contain timezone offsets, fractional seconds or legacy naive timestamps;
    Python's parser is the same one used by apply_pet_decay(), so selection and mutation can
    no longer disagree about whether a pet is due.
    """
    now = now_utc()
    async with db_connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            'SELECT id,hunger,happiness,energy,last_decay_at,created_at FROM pets ORDER BY id'
        )
        rows = await cur.fetchall()

    changed = []
    for row in rows:
        last = parse_dt(row['last_decay_at']) or parse_dt(row['created_at'])
        due = last is None or last > now or (now - last).total_seconds() >= 3600
        if not due:
            continue
        before = (row['hunger'], row['happiness'], row['energy'])
        pet = await apply_pet_decay(row['id'])
        if pet and (pet['hunger'], pet['happiness'], pet['energy']) != before:
            changed.append(row['id'])
    return changed


async def get_active_pet_ids():
    async with db_connection() as db:
        cur = await db.execute('''SELECT MIN(p.id) FROM pets p
            JOIN users u ON u.active_couple_id=p.couple_id GROUP BY p.couple_id''')
        return [r[0] for r in await cur.fetchall() if r[0]]
