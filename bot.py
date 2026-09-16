import asyncio
import secrets
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InputMediaAnimation, Message

from config import ADMIN_IDS, BOT_TOKEN, BOT_USERNAME
from catalog import SHOP_ITEMS, SLOT_NAMES
from db import (
    ACHIEVEMENTS,
    add_progress,
    apply_decay_to_all_pets,
    both_inactive_for_hours_for_couple,
    check_achievements,
    clear_screen,
    complete_joint_action,
    create_couple,
    create_joint_action,
    create_promo_code,
    equip_item,
    get_achievements,
    get_broadcast_user_ids,
    get_active_couple,
    get_active_pet_ids,
    get_couple_by_id,
    get_couple_id_by_pet,
    get_couple_user_ids_by_pet,
    get_couples_by_user,
    get_daily_tasks,
    get_ephemeral_messages,
    get_inventory,
    get_partner_id,
    get_promo_codes,
    get_pet_by_couple,
    get_pet_by_id,
    get_pet_by_user,
    get_room_equipment,
    get_screen,
    get_screen_state,
    get_user,
    init_db,
    is_couple_active_for_user,
    join_couple,
    record_action,
    purchase_item,
    redeem_promo_code,
    save_ephemeral_message,
    delete_ephemeral_message,
    rename_pet,
    save_user,
    set_active_couple,
    set_promo_enabled,
    set_screen,
    set_screen_state,
    touch_user,
    update_pet,
)
from room_renderer import GROWTH_STAGES, growth_stage, render_room_gif

from keyboards import (
    achievements_keyboard,
    broadcast_confirm_keyboard,
    back_keyboard,
    bonds_keyboard,
    equip_keyboard,
    joint_finish_keyboard,
    main_keyboard,
    mochi_mail_keyboard,
    room_keyboard,
    shop_keyboard,
)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# Media главного экрана меняем только через общий rate-limit/lock, чтобы не ловить Flood Control.
ACTION_MESSAGE_SECONDS = 4
TEMP_NOTIFICATION_SECONDS = 8
PET_ACTION_COOLDOWN_SECONDS = 5.0
SCREEN_EDIT_MIN_INTERVAL = 1.05
GLOBAL_SCREEN_EDIT_MIN_INTERVAL = 0.85
MOCHI_MAIL_COOLDOWN_SECONDS = 6
DECAY_REFRESH_INTERVAL_SECONDS = 60
BROADCAST_MAX_LENGTH = 4096
BROADCAST_SEND_INTERVAL_SECONDS = 1 / 15
BROADCAST_CONFIRM_TTL_SECONDS = 15 * 60
BROADCAST_LOCK = asyncio.Lock()

PET_ACTION_LAST_AT = {}
SCREEN_EDIT_LOCKS = {}
SCREEN_EDIT_LAST_AT = {}
SCREEN_BLOCKED_UNTIL = {}
SCREEN_LAST_CAPTION = {}
SCREEN_LAST_STATE = {}
GLOBAL_SCREEN_EDIT_LOCK = asyncio.Lock()
GLOBAL_SCREEN_EDIT_LAST_AT = 0.0
ANIMATION_FILE_IDS = {}
MOCHI_MAIL_LAST_AT = {}
PET_ACTION_LOCKS = {}
ACTION_RESTORE_TASKS = {}
SCREEN_ACTION_UNTIL = {}

# Все побочные сообщения бота складываем сюды. Главный экран сюда НИКОГДА не пишем,
# иначе следующим кликом сами же удалим главный экран.
EPHEMERAL_MESSAGES = {}


async def remember_ephemeral(
    user_id: int,
    chat_id: int,
    message_id: int,
    *,
    cleanup_on_next: bool = True,
    expires_in_seconds: float | None = None,
):
    if cleanup_on_next:
        EPHEMERAL_MESSAGES.setdefault(user_id, set()).add((chat_id, message_id))
    # Дублируем в SQLite, чтобы после рестарта бот не забыл временные сообщения.
    # Некоторые сообщения (например, совместный invite) должны жить до таймаута,
    # а не исчезать от любого следующего клика пользователя.
    await save_ephemeral_message(
        user_id,
        chat_id,
        message_id,
        cleanup_on_next=cleanup_on_next,
        expires_in_seconds=expires_in_seconds,
    )


async def forget_ephemeral(user_id: int, chat_id: int, message_id: int):
    bucket = EPHEMERAL_MESSAGES.get(user_id)
    if bucket:
        bucket.discard((chat_id, message_id))
        if not bucket:
            EPHEMERAL_MESSAGES.pop(user_id, None)
    await delete_ephemeral_message(user_id, chat_id, message_id)


async def try_delete_message(chat_id: int, message_id: int, *, retry_limit: float = 15.0) -> bool:
    """Delete a Telegram message. False means it should stay tracked and be retried later."""
    for attempt in range(2):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            return True
        except TelegramRetryAfter as exc:
            retry = float(getattr(exc, 'retry_after', 3))
            if attempt == 0 and retry <= retry_limit:
                await asyncio.sleep(retry + 0.25)
                continue
            return False
        except (TelegramBadRequest, TelegramForbiddenError):
            # Сообщение уже удалено / недоступно: хранить его идентификатор дальше бессмысленно.
            return True
    return False


async def cleanup_ephemeral(user_id: int):
    # Берём и память, и SQLite: так чистка переживает даже перезапуск процесса.
    messages = set(EPHEMERAL_MESSAGES.pop(user_id, set()))
    messages.update(tuple(row) for row in await get_ephemeral_messages(user_id))
    failed = set()
    for chat_id, message_id in messages:
        if await try_delete_message(chat_id, message_id):
            await delete_ephemeral_message(user_id, chat_id, message_id)
        else:
            failed.add((chat_id, message_id))
    if failed:
        EPHEMERAL_MESSAGES[user_id] = failed


class CleanupEphemeralMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, 'from_user', None)
        if user:
            await cleanup_ephemeral(user.id)
        return await handler(event, data)


# Чистим прошлые сообщения на любой новой команде, тексте или нажатии кнопки.
# Так чат не превращается в склад старых меню и уведомлений.
dp.message.outer_middleware(CleanupEphemeralMiddleware())
dp.callback_query.outer_middleware(CleanupEphemeralMiddleware())

# Магазин. Это пока просто каталог в коде — потом можно спокойно вынести в БД/JSON.



class RenamePet(StatesGroup):
    waiting_name = State()


class MochiMail(StatesGroup):
    waiting_custom_text = State()


class BroadcastMessage(StatesGroup):
    waiting_text = State()
    waiting_confirm = State()


class PromoRedeem(StatesGroup):
    waiting_code = State()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# Фразы специально храним на сервере, а в callback гоняем короткие ключи —
# Telegram ограничивает длину callback_data, поэтому используем короткие ключи.
MOCHI_MAIL_PHRASES = {
    'miss': '🥺 Я скучаю по тебе',
    'love': '❤️ Я люблю тебя',
    'hug': '🫂 Обними меня при встрече',
    'luck': '🍀 Удачи тебе сегодня!',
    'morning': '🌞 Доброе утро! Хорошего тебе дня',
    'night': '🌙 Спокойной ночи и сладких снов',
    'important': '💗 Ты мне очень важен(а)',
    'thanks': '🙏 Спасибо, что ты есть',
}


async def room_animation_source(pet_id: int, state: str):
    # Комната собирается на лету из купленных слоёв; кэш не даёт повторять одинаковый рендер.
    equipment = await get_room_equipment(pet_id)
    pet = await get_pet_by_id(pet_id)
    level = pet["level"] if pet else 1
    path, render_key = render_room_gif(
        equipment, state, pet_level=level
    )
    cache_key = f"room:{render_key}:{state}"
    return ANIMATION_FILE_IDS.get(cache_key) or FSInputFile(path), cache_key


def progress(value):
    filled = max(0, min(10, round(value / 10)))
    return '▰' * filled + '▱' * (10 - filled)


async def safe_callback_answer(callback, *args, **kwargs):
    try:
        await callback.answer(*args, **kwargs)
    except TelegramRetryAfter:
        return
    except TelegramBadRequest as exc:
        text = str(exc).lower()
        if 'query is too old' in text or 'query id is invalid' in text:
            return
        raise


def _message_is_gone(exc: TelegramBadRequest) -> bool:
    """Only forget a stored screen when Telegram says that exact message is gone."""
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            'message to edit not found',
            'message_id_invalid',
            'message identifier is not specified',
            'chat not found',
        )
    )


def screen_action_is_active(user_id: int) -> bool:
    return SCREEN_ACTION_UNTIL.get(user_id, 0.0) > asyncio.get_running_loop().time()


def cancel_screen_action(user_id: int):
    task = ACTION_RESTORE_TASKS.pop(user_id, None)
    if task and not task.done():
        task.cancel()
    SCREEN_ACTION_UNTIL.pop(user_id, None)


async def bind_screen_to_callback(callback: CallbackQuery):
    """Treat the message whose main keyboard was pressed as the canonical screen."""
    message = getattr(callback, 'message', None)
    if not message:
        return
    await set_screen(callback.from_user.id, message.chat.id, message.message_id)


async def partner_label(user_id, couple):
    if not couple['user2_id']:
        return 'Ждёт второго хозяина'
    pid = couple['user2_id'] if couple['user1_id'] == user_id else couple['user1_id']
    p = await get_user(pid)
    return p['first_name'] if p and p['first_name'] else 'Партнёр'




def current_growth_stage(level: int | None) -> dict:
    return growth_stage(level or 1)


def next_growth_stage(level: int | None) -> dict | None:
    level = max(1, int(level or 1))
    for stage in GROWTH_STAGES:
        if stage["min_level"] > level:
            return stage
    return None

def item_label(item_key):
    item = SHOP_ITEMS.get(item_key)
    return f'{item["emoji"]} {item["name"]}' if item else item_key


async def room_short(pet_id):
    eq = await get_room_equipment(pet_id)
    if not eq:
        return 'базовая'
    bits = []
    for slot in ('window', 'bed', 'rug', 'plant', 'toy'):
        if slot in eq and eq[slot] in SHOP_ITEMS:
            bits.append(SHOP_ITEMS[eq[slot]]['emoji'])
    return ' '.join(bits) if bits else 'кастомная'


async def pet_caption(user_id, action_text=None):
    couple = await get_active_couple(user_id)
    pet = await get_pet_by_user(user_id)
    if not couple or not pet:
        return 'У тебя пока нет Mochi 🥲'

    inactive = await both_inactive_for_hours_for_couple(couple['id'], 24)
    partner = await partner_label(user_id, couple)
    pet_name = escape(str(pet['name']))
    partner_name = escape(str(partner))
    if action_text:
        status = escape(str(action_text))
    elif not couple['user2_id']:
        status = '👀 Питомец пока ждёт второго хозяина.'
    elif inactive:
        status = f'🥺 {pet_name} скучает — вы оба давно не заходили.'
    else:
        visual_state = pet_visual_state(pet, inactive=False, has_partner=True)
        if visual_state == 'sick':
            status = f'🤒 {pet_name} плохо себя чувствует — ему срочно нужна забота.'
        elif visual_state == 'hungry':
            status = f'🍽 {pet_name} очень голоден.'
        elif visual_state == 'sleep':
            status = f'😴 {pet_name} совсем вымотался и хочет отдохнуть.'
        elif visual_state == 'sad':
            status = f'🌧 {pet_name} немного грустит.'
        elif visual_state == 'happy':
            status = f'😸 {pet_name} в отличном настроении!'
        else:
            status = f'✨ {pet_name} чувствует себя хорошо.'

    bonds = await get_couples_by_user(user_id, include_pending=False)
    need_xp = 100 * pet['level']
    room = await room_short(pet['id'])
    stage = current_growth_stage(pet['level'])
    upcoming = next_growth_stage(pet['level'])
    growth_line = f'🌱 Этап роста <b>{stage["title"]}</b>'
    if upcoming:
        growth_line += f' · следующий на <b>{upcoming["min_level"]}</b> ур.'
    return (
        f'🐱 <b>{pet_name}</b> · вместе с <b>{partner_name}</b>\n'
        f'{status}\n\n'
        f'⭐ Уровень <b>{pet["level"]}</b> · XP <b>{pet["xp"]}/{need_xp}</b>\n'
        f'{growth_line}\n'
        f'🪙 Монеты <b>{pet["coins"]}</b> · 🏠 Комната: <b>{room}</b>\n\n'
        f'🍓 Сытость  <b>{pet["hunger"]}/100</b>\n{progress(pet["hunger"])}\n'
        f'✨ Настроение  <b>{pet["happiness"]}/100</b>\n{progress(pet["happiness"])}\n'
        f'⚡ Энергия  <b>{pet["energy"]}/100</b>\n{progress(pet["energy"])}\n'
        f'❤️ Любовь  <b>{pet["love"]}/100</b>\n{progress(pet["love"])}\n\n'
        f'🐾 Твоих общих питомцев с разными людьми: <b>{len(bonds)}</b>'
    )

def pet_visual_state(pet, *, inactive: bool = False, has_partner: bool = True) -> str:
    """Map existing pet stats to the full supplied animation set."""
    if not pet or not has_partner:
        return 'idle'
    if inactive:
        return 'sad'
    if pet['hunger'] <= 10 or pet['happiness'] <= 10 or pet['energy'] <= 10:
        return 'sick'
    if pet['hunger'] <= 30:
        return 'hungry'
    if pet['energy'] <= 20:
        return 'sleep'
    if pet['happiness'] <= 30:
        return 'sad'
    if pet['happiness'] >= 75 and pet['hunger'] >= 60 and pet['energy'] >= 40 and pet['love'] >= 50:
        return 'happy'
    return 'idle'


async def screen_default_state(user_id: int, pet=None) -> str:
    pet = pet or await get_pet_by_user(user_id)
    couple = await get_active_couple(user_id)
    if not pet or not couple:
        return 'idle'
    has_partner = bool(couple['user2_id'])
    inactive = has_partner and await both_inactive_for_hours_for_couple(couple['id'], 24)
    return pet_visual_state(pet, inactive=inactive, has_partner=has_partner)


async def create_screen(user_id, chat_id):
    caption = await pet_caption(user_id)
    pet = await get_pet_by_user(user_id)
    if not pet:
        return
    visual_state = await screen_default_state(user_id, pet)
    source, cache_key = await room_animation_source(pet['id'], visual_state)
    sent = await bot.send_animation(
        chat_id=chat_id, animation=source, caption=caption,
        parse_mode='HTML', reply_markup=main_keyboard(),
        width=512, height=512,
    )
    if sent.animation:
        ANIMATION_FILE_IDS[cache_key] = sent.animation.file_id
    await set_screen(user_id, chat_id, sent.message_id, visual_state)
    SCREEN_LAST_CAPTION[user_id] = caption
    SCREEN_LAST_STATE[user_id] = visual_state


async def safe_edit_caption(user_id, chat_id, message_id, caption, reply_markup=None):
    global GLOBAL_SCREEN_EDIT_LAST_AT
    if SCREEN_LAST_CAPTION.get(user_id) == caption and reply_markup is None:
        return True

    user_lock = SCREEN_EDIT_LOCKS.setdefault(user_id, asyncio.Lock())
    async with user_lock:
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < SCREEN_BLOCKED_UNTIL.get(user_id, 0):
            return False

        user_wait = SCREEN_EDIT_MIN_INTERVAL - (now - SCREEN_EDIT_LAST_AT.get(user_id, 0))
        if user_wait > 0:
            await asyncio.sleep(user_wait)

        async with GLOBAL_SCREEN_EDIT_LOCK:
            global_wait = GLOBAL_SCREEN_EDIT_MIN_INTERVAL - (loop.time() - GLOBAL_SCREEN_EDIT_LAST_AT)
            if global_wait > 0:
                await asyncio.sleep(global_wait)

            for attempt in range(2):
                try:
                    await bot.edit_message_caption(
                        chat_id=chat_id, message_id=message_id, caption=caption,
                        parse_mode='HTML', reply_markup=reply_markup or main_keyboard(),
                    )
                    t = loop.time()
                    SCREEN_EDIT_LAST_AT[user_id] = t
                    GLOBAL_SCREEN_EDIT_LAST_AT = t
                    SCREEN_LAST_CAPTION[user_id] = caption
                    return True
                except TelegramRetryAfter as exc:
                    retry = float(getattr(exc, 'retry_after', 3))
                    SCREEN_BLOCKED_UNTIL[user_id] = loop.time() + retry + 0.5
                    if attempt == 0 and retry <= 12:
                        await asyncio.sleep(retry + 0.5)
                        continue
                    print(f'⚠️ Flood control caption для {user_id}: {retry} сек.')
                    return False
                except TelegramBadRequest as exc:
                    if 'message is not modified' in str(exc).lower():
                        SCREEN_LAST_CAPTION[user_id] = caption
                        return True
                    raise
    return False


async def refresh_screen_media(user_id, state=None, action_text=None):
    """Меняем медиа главного экрана через одну глобальную очередь."""
    global GLOBAL_SCREEN_EDIT_LAST_AT
    screen = await get_screen(user_id)
    pet = await get_pet_by_user(user_id)
    if not screen or not pet:
        return False
    chat_id, message_id = screen
    visual_state = state or await screen_default_state(user_id, pet)
    source, cache_key = await room_animation_source(pet['id'], visual_state)
    caption = await pet_caption(user_id, action_text)

    async with GLOBAL_SCREEN_EDIT_LOCK:
        loop = asyncio.get_running_loop()
        wait = GLOBAL_SCREEN_EDIT_MIN_INTERVAL - (loop.time() - GLOBAL_SCREEN_EDIT_LAST_AT)
        if wait > 0:
            await asyncio.sleep(wait)

        for attempt in range(2):
            try:
                media = InputMediaAnimation(
                    media=source, caption=caption, parse_mode='HTML',
                    width=512, height=512,
                )
                edited = await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=media,
                    reply_markup=main_keyboard(),
                )
                GLOBAL_SCREEN_EDIT_LAST_AT = loop.time()
                if getattr(edited, 'animation', None):
                    ANIMATION_FILE_IDS[cache_key] = edited.animation.file_id
                await set_screen_state(user_id, visual_state)
                SCREEN_LAST_CAPTION[user_id] = caption
                SCREEN_LAST_STATE[user_id] = visual_state
                return True
            except TelegramRetryAfter as exc:
                retry = float(getattr(exc, 'retry_after', 3))
                SCREEN_BLOCKED_UNTIL[user_id] = loop.time() + retry + 0.5
                if attempt == 0 and retry <= 15:
                    await asyncio.sleep(retry + 0.5)
                    continue
                print(f'⚠️ Flood control room-media для {user_id}: {retry} сек.')
                return False
            except TelegramBadRequest as exc:
                if 'message is not modified' in str(exc).lower():
                    await set_screen_state(user_id, visual_state)
                    SCREEN_LAST_CAPTION[user_id] = caption
                    SCREEN_LAST_STATE[user_id] = visual_state
                    return True

                if _message_is_gone(exc):
                    await clear_screen(user_id)
                    SCREEN_LAST_CAPTION.pop(user_id, None)
                    SCREEN_LAST_STATE.pop(user_id, None)
                else:
                    print(f'⚠️ Не удалось обновить media экрана {user_id}: {exc}')
                return False
            except TelegramForbiddenError:
                await clear_screen(user_id)
                SCREEN_LAST_CAPTION.pop(user_id, None)
                SCREEN_LAST_STATE.pop(user_id, None)
                return False
    return False


async def refresh_room_media_for_pet(pet_id):
    couple_id = await get_couple_id_by_pet(pet_id)
    users = await get_couple_user_ids_by_pet(pet_id)
    targets = [u for u in users if await is_couple_active_for_user(u, couple_id)]
    await asyncio.gather(*(refresh_screen_media(u) for u in targets), return_exceptions=True)


async def refresh_screen(user_id, action_text=None):
    screen = await get_screen(user_id)
    if not screen:
        # Новый экран создаётся только явными /start и /screen.
        return False
    chat_id, message_id = screen
    try:
        return await safe_edit_caption(user_id, chat_id, message_id, await pet_caption(user_id, action_text))
    except TelegramBadRequest as exc:
        if _message_is_gone(exc):
            await clear_screen(user_id)
            SCREEN_LAST_CAPTION.pop(user_id, None)
            SCREEN_LAST_STATE.pop(user_id, None)
        else:
            print(f'⚠️ Не удалось обновить caption экрана {user_id}: {exc}')
    except TelegramForbiddenError:
        await clear_screen(user_id)
        SCREEN_LAST_CAPTION.pop(user_id, None)
        SCREEN_LAST_STATE.pop(user_id, None)
    return False


async def sync_screen(user_id, action_text=None, *, force_media=False):
    """Synchronize stats and animation without needlessly re-uploading media.

    Caption edits are the normal path. editMessageMedia is used only when the visual state
    really changed (or when explicitly forced). The current visual state is persisted in
    SQLite so this remains correct after a bot restart/deploy.
    """
    screen = await get_screen(user_id)
    if not screen:
        return False
    pet = await get_pet_by_user(user_id)  
    if not pet:
        return False
    desired_state = await screen_default_state(user_id, pet)
    stored_state = await get_screen_state(user_id)
    caption_updated = await refresh_screen(user_id, action_text)

    if force_media or stored_state != desired_state:
        media_updated = await refresh_screen_media(
            user_id, state=desired_state, action_text=action_text
        )
        return bool(caption_updated or media_updated)
    return caption_updated


async def refresh_pet_screens(pet_id, action_text=None):
    couple_id = await get_couple_id_by_pet(pet_id)
    users = await get_couple_user_ids_by_pet(pet_id)
    targets = [u for u in users if await is_couple_active_for_user(u, couple_id)]
    # Не перебиваем короткую action-анимацию фоновым тиканьем статов.
    normal_targets = [u for u in targets if not screen_action_is_active(u)]
    await asyncio.gather(
        *(sync_screen(u, action_text) for u in normal_targets),
        return_exceptions=True,
    )


async def delete_later(chat_id, message_id, delay, owner_id=None):
    await asyncio.sleep(delay)
    deleted = await try_delete_message(chat_id, message_id)
    if deleted and owner_id is not None:
        await forget_ephemeral(owner_id, chat_id, message_id)


async def send_temp_animation(
    chat_id,
    pet_id,
    state,
    caption=None,
    delay=ACTION_MESSAGE_SECONDS,
    *,
    cleanup_on_next=True,
):
    source, cache_key = await room_animation_source(pet_id, state)
    for attempt in range(2):
        try:
            sent = await bot.send_animation(
                chat_id=chat_id, animation=source, caption=caption,
                parse_mode='HTML' if caption else None,
                width=512, height=512,
            )
            if sent.animation:
                ANIMATION_FILE_IDS[cache_key] = sent.animation.file_id
            await remember_ephemeral(
                chat_id, chat_id, sent.message_id,
                cleanup_on_next=cleanup_on_next,
                expires_in_seconds=delay if delay else None,
            )
            if delay:
                asyncio.create_task(delete_later(chat_id, sent.message_id, delay, chat_id))
            return sent
        except TelegramRetryAfter as exc:
            retry = float(getattr(exc, 'retry_after', 3))
            if attempt == 0 and retry <= 15:
                await asyncio.sleep(retry + 0.25)
                continue
            print(f'⚠️ Flood control sendAnimation для {chat_id}: {retry} сек.')
            return None
        except (TelegramBadRequest, TelegramForbiddenError):
            return None
    return None


async def _restore_screen_after_action(user_id: int, pet_id: int, delay: float):
    """Restore the same main message after the short action animation finishes."""
    current_task = asyncio.current_task()
    try:
        await asyncio.sleep(delay)
        active_pet = await get_pet_by_user(user_id)
        if not active_pet or active_pet['id'] != pet_id:
            return
        SCREEN_ACTION_UNTIL.pop(user_id, None)
        await sync_screen(user_id)
    except asyncio.CancelledError:
        return
    finally:
        if ACTION_RESTORE_TASKS.get(user_id) is current_task:
            ACTION_RESTORE_TASKS.pop(user_id, None)
            SCREEN_ACTION_UNTIL.pop(user_id, None)


async def show_action_on_screen(user_id: int, pet_id: int, state: str, action_text: str):
    """Show an action on the existing Tamagotchi screen instead of sending a second screen."""
    cancel_screen_action(user_id)
    SCREEN_ACTION_UNTIL[user_id] = asyncio.get_running_loop().time() + ACTION_MESSAGE_SECONDS + 1.0
    edited = await refresh_screen_media(user_id, state=state, action_text=action_text)
    if not edited:
        SCREEN_ACTION_UNTIL.pop(user_id, None)
        return await send_temp_animation(
            user_id,
            pet_id,
            state,
            caption=escape(str(action_text)),
            delay=ACTION_MESSAGE_SECONDS,
        )

    task = asyncio.create_task(_restore_screen_after_action(user_id, pet_id, ACTION_MESSAGE_SECONDS))
    ACTION_RESTORE_TASKS[user_id] = task
    return True


async def send_temp_text(
    chat_id,
    text,
    delay=None,
    reply_markup=None,
    parse_mode='HTML',
    *,
    cleanup_on_next=True,
):
    for attempt in range(2):
        try:
            msg = await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
            await remember_ephemeral(
                chat_id, chat_id, msg.message_id,
                cleanup_on_next=cleanup_on_next,
                expires_in_seconds=delay if delay else None,
            )
            if delay:
                asyncio.create_task(delete_later(chat_id, msg.message_id, delay, chat_id))
            return msg
        except TelegramRetryAfter as exc:
            retry = float(getattr(exc, 'retry_after', 3))
            if attempt == 0 and retry <= 15:
                await asyncio.sleep(retry + 0.25)
                continue
            return None
        except (TelegramBadRequest, TelegramForbiddenError):
            return None
    return None


async def answer_ephemeral(message: Message, text, *, reply_markup=None, parse_mode='HTML', delay=None):
    # Универсальный answer, который мы потом подчистим следующим действием пользователя.
    return await send_temp_text(
        message.chat.id, text, delay=delay, reply_markup=reply_markup, parse_mode=parse_mode
    )


async def notify_action_to_couple(actor_id, couple_id, pet_id, state, action_text):
    actor = await get_user(actor_id)
    actor_name = actor['first_name'] if actor and actor['first_name'] else 'Партнёр'
    actor_name_html = escape(str(actor_name))
    action_text_html = escape(str(action_text))
    partner_id = await get_partner_id(actor_id, couple_id)
    if partner_id:
        await send_temp_text(
            partner_id,
            f'🐾 <b>{actor_name_html}</b>: {action_text_html}',
            delay=TEMP_NOTIFICATION_SECONDS,
        )


async def send_mochi_mail(
    sender_id: int,
    text: str,
    *,
    couple_id: int | None = None,
    pet_id: int | None = None,
):

    couple = await get_couple_by_id(couple_id) if couple_id else await get_active_couple(sender_id)
    if not couple or sender_id not in (couple['user1_id'], couple['user2_id']):
        return False, 'Сначала выбери Mochi 🐱'
    pet = await get_pet_by_couple(couple['id'])
    if not pet or (pet_id is not None and pet['id'] != pet_id):
        return False, 'Этот Mochi больше недоступен.'
    if not couple['user2_id']:
        return False, 'У этого Mochi пока нет второго хозяина 💞'

    now = asyncio.get_running_loop().time()
    mail_key = (sender_id, couple['id'])
    left = MOCHI_MAIL_COOLDOWN_SECONDS - (now - MOCHI_MAIL_LAST_AT.get(mail_key, 0))
    if left > 0:
        return False, f'Mochi ещё отдышится 😼 Подожди {left:.1f} сек.'

    partner_id = await get_partner_id(sender_id, couple['id'])
    if not partner_id:
        return False, 'Не смог найти второго хозяина 😿'

    sender = await get_user(sender_id)
    sender_name = escape(sender['first_name'] if sender and sender['first_name'] else 'Твой человек')
    pet_name = escape(str(pet['name']))
    clean_text = escape(text.strip())

    caption = (
        f'💌 <b>{pet_name} кое-что принёс от {sender_name}</b>\n\n'
        f'«{clean_text}»\n\n'
        f'🐾 <i>Передано через {pet_name}</i>'
    )

    sent = await send_temp_animation(partner_id, pet['id'], 'love', caption, delay=None)
    if not sent:
        return False, 'Не получилось доставить сообщение 😿'
    MOCHI_MAIL_LAST_AT[mail_key] = asyncio.get_running_loop().time()
    return True, partner_id


def cooldown_left(pet_id):
    now = asyncio.get_running_loop().time()
    return max(0, PET_ACTION_COOLDOWN_SECONDS - (now - PET_ACTION_LAST_AT.get(pet_id, 0)))


async def game_rewards(pet_id, action_key, base_xp, base_coins):
    before = await get_pet_by_id(pet_id)
    pet, _ = await add_progress(pet_id, xp=base_xp, coins=base_coins)
    daily_done = await record_action(pet_id, action_key)
    unlocked = await check_achievements(pet_id)
    after = await get_pet_by_id(pet_id)
    messages = []
    stage_changed = False
    if before and after and after['level'] > before['level']:
        messages.append(f'⭐ Новый уровень: <b>{after["level"]}</b>!')
        before_stage = current_growth_stage(before['level'])
        after_stage = current_growth_stage(after['level'])
        if before_stage['key'] != after_stage['key']:
            stage_changed = True
            messages.append(
                f'🌱 <b>{escape(str(after["name"]))} вырос!</b> Теперь этап — <b>{after_stage["title"]}</b> 🎉'
            )
    for task in daily_done:
        messages.append(f'📅 Ежедневка выполнена: <b>{task["title"]}</b> (+{task["reward_coins"]}🪙, +{task["reward_xp"]}XP)')
    for key in unlocked:
        title, _, coins, xp = ACHIEVEMENTS[key]
        messages.append(f'🏆 Достижение: <b>{title}</b> (+{coins}🪙, +{xp}XP)')
    return pet, messages, stage_changed


async def perform_action(callback, *, action_key, state, action_text, base_xp, base_coins,
                         hunger_delta=0, happiness_delta=0, energy_delta=0, love_delta=0):
    await touch_user(callback.from_user.id)
    couple = await get_active_couple(callback.from_user.id)
    pet = await get_pet_by_user(callback.from_user.id)
    if not couple or not pet:
        await safe_callback_answer(callback, 'Питомец не найден', show_alert=True)
        return
    if not couple['user2_id']:
        await safe_callback_answer(callback, 'Сначала пригласи второго хозяина 💞', show_alert=True)
        return

    await bind_screen_to_callback(callback)
    action_lock = PET_ACTION_LOCKS.setdefault(pet['id'], asyncio.Lock())
    async with action_lock:
        cd = cooldown_left(pet['id'])
        if cd > 0:
            await safe_callback_answer(callback, f'Секундочку 😼 {cd:.1f}с')
            return
        await safe_callback_answer(callback)
        PET_ACTION_LAST_AT[pet['id']] = asyncio.get_running_loop().time()

        pet = await update_pet(pet['id'], hunger_delta, happiness_delta, energy_delta, love_delta)
        text = action_text.replace('Mochi', pet['name'])
        _, reward_messages, _stage_changed = await game_rewards(pet['id'], action_key, base_xp, base_coins)

    couple_id = await get_couple_id_by_pet(pet['id'])
    users = await get_couple_user_ids_by_pet(pet['id'])
    targets = [u for u in users if await is_couple_active_for_user(u, couple_id)]
    for uid in targets:
        await show_action_on_screen(uid, pet['id'], state, text)

    partner_id = await get_partner_id(callback.from_user.id, couple_id)
    if partner_id and partner_id not in targets:
        await notify_action_to_couple(callback.from_user.id, couple_id, pet['id'], state, text)

    if reward_messages:
        for uid in await get_couple_user_ids_by_pet(pet['id']):
            await send_temp_text(uid, '\n'.join(reward_messages), 10)


async def recreate_own_screen(user_id, chat_id):
    await clear_screen(user_id)
    SCREEN_LAST_CAPTION.pop(user_id, None)
    SCREEN_LAST_STATE.pop(user_id, None)
    try:
        await create_screen(user_id, chat_id)
    except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter) as exc:
        print(f'⚠️ Не удалось пересоздать экран {user_id}: {exc}')


@dp.message(CommandStart())
async def start_handler(message: Message):
    await save_user(message.from_user)
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith('join_'):
        ok, result = await join_couple(message.from_user.id, parts[1].removeprefix('join_'))
        if not ok:
            await answer_ephemeral(message, result)
            return
        partner_id = await get_partner_id(message.from_user.id, result)
        pet = await get_pet_by_user(message.from_user.id)
        await answer_ephemeral(message, '💞 Готово! У вас теперь свой отдельный общий питомец.')
        await recreate_own_screen(message.from_user.id, message.chat.id)
        if partner_id:
            await send_temp_text(partner_id, f'💖 {escape(str(message.from_user.first_name or "Партнёр"))} подключился(ась) к вашему {escape(str(pet["name"]))}!')
            await refresh_screen(partner_id)
        return

    couples = await get_couples_by_user(message.from_user.id)
    if not couples:
        couple = await create_couple(message.from_user.id)
        url = f'https://t.me/{BOT_USERNAME}?start=join_{couple["invite_code"]}'
        await answer_ephemeral(message, '🐣 <b>Добро пожаловать в Mochi!</b>\n\nОтправь эту ссылку человеку, с которым хочешь растить первого питомца:\n\n' + url, parse_mode='HTML')
    await recreate_own_screen(message.from_user.id, message.chat.id)


@dp.message(Command('screen'))
async def screen_handler(message: Message):
    await save_user(message.from_user)
    if not await get_active_couple(message.from_user.id):
        await answer_ephemeral(message, 'Сначала напиши /start 🐱')
        return
    await recreate_own_screen(message.from_user.id, message.chat.id)


async def activate_promo_for_user(message: Message, state: FSMContext, raw_code: str):
    code = ' '.join((raw_code or '').strip().split())
    ok, result = await redeem_promo_code(message.from_user.id, code)
    if not ok:
        reason = result.get('reason') if isinstance(result, dict) else result
        errors = {
            'invalid_code': 'Промокод должен содержать 3–32 символа: латинские буквы, цифры, <code>_</code> или <code>-</code>.',
            'not_found': 'Такого промокода нет 😿',
            'disabled': 'Этот промокод больше не активен.',
            'exhausted': 'У этого промокода закончились доступные активации.',
            'already_used': 'Ты уже активировал(а) этот промокод.',
            'no_pet': 'Сначала создай или выбери Mochi через /start.',
        }
        await answer_ephemeral(message, errors.get(reason, 'Не удалось активировать промокод.'), parse_mode='HTML')
        return False

    await state.clear()
    pet_id = int(result['pet_id'])

    unlocked = []
    try:
        unlocked = await check_achievements(pet_id)
    except Exception as exc:
        print(f'Promo achievement check error pet={pet_id}: {exc}')
    try:
        pet = await get_pet_by_id(pet_id)
    except Exception as exc:
        print(f'Promo balance refresh error pet={pet_id}: {exc}')
        pet = None
    try:
        await refresh_pet_screens(pet_id)
    except Exception as exc:
        print(f'Promo screen refresh error pet={pet_id}: {exc}')
    achievement_line = ''
    if unlocked:
        achievement_line = f'\n🏆 Новых достижений: <b>{len(unlocked)}</b>'
    await answer_ephemeral(
        message,
        f'🎁 Промокод <code>{escape(str(result["code"]))}</code> активирован!\n'
        f'Начислено: <b>+{result["reward_coins"]}</b> 🪙\n'
        f'Баланс Mochi: <b>{pet["coins"] if pet else result["balance"]}</b> 🪙'
        f'{achievement_line}',
        parse_mode='HTML',
    )
    return True


@dp.message(Command('promo'))
async def promo_handler(message: Message, state: FSMContext):

    await state.clear()
    await save_user(message.from_user)
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        await activate_promo_for_user(message, state, parts[1])
        return
    await state.set_state(PromoRedeem.waiting_code)
    await answer_ephemeral(
        message,
        '🎁 Пришли промокод одним сообщением.\n\nНапример: <code>MOCHI500</code>\n/cancel — отмена.',
        parse_mode='HTML',
    )


@dp.message(PromoRedeem.waiting_code)
async def promo_text_handler(message: Message, state: FSMContext):
    if (message.text or '').strip().lower().startswith('/cancel'):
        await state.clear()
        await answer_ephemeral(message, 'Ввод промокода отменён.')
        return
    if not message.text:
        await answer_ephemeral(message, 'Промокод нужно прислать текстом.')
        return
    await save_user(message.from_user)
    await activate_promo_for_user(message, state, message.text)


def _promo_limit_from_text(raw: str):
    value = (raw or '').strip().lower()
    if value in {'', '0', '∞', 'inf', 'unlimited', 'безлимит'}:
        return None
    return int(value)


@dp.message(Command('promo_create'))
async def promo_create_handler(message: Message):
    if not is_admin(message.from_user.id):
        await answer_ephemeral(message, '⛔ Эта команда доступна только администратору.', parse_mode=None)
        return
    parts = (message.text or '').split()
    if len(parts) not in {3, 4}:
        await answer_ephemeral(
            message,
            'Формат:\n<code>/promo_create CODE COINS [LIMIT]</code>\n\n'
            'Пример на 100 активаций:\n<code>/promo_create SUMMER 500 100</code>\n\n'
            'Без лимита:\n<code>/promo_create WELCOME 250</code>',
            parse_mode='HTML',
        )
        return
    code = parts[1]
    try:
        reward = int(parts[2])
        limit = _promo_limit_from_text(parts[3]) if len(parts) == 4 else None
    except ValueError:
        await answer_ephemeral(message, 'COINS и LIMIT должны быть целыми числами. LIMIT можно не указывать.', parse_mode=None)
        return

    ok, result = await create_promo_code(code, reward, limit, message.from_user.id)
    if not ok:
        errors = {
            'invalid_code': 'Код: 3–32 символа, только A-Z, 0-9, _ и -.',
            'invalid_reward': 'Награда должна быть от 1 до 10 000 000 монет.',
            'invalid_limit': 'Лимит должен быть от 1 до 1 000 000 или 0/без параметра для безлимита.',
            'exists': 'Такой промокод уже существует. Старые коды намеренно нельзя создавать заново.',
        }
        await answer_ephemeral(message, errors.get(result, 'Не удалось создать промокод.'), parse_mode=None)
        return
    limit_text = 'без лимита' if limit is None else f'{limit} активаций'
    await answer_ephemeral(
        message,
        f'✅ Промокод <code>{escape(str(result))}</code> создан.\n'
        f'Награда: <b>{reward}</b> 🪙\nЛимит: <b>{limit_text}</b>',
        parse_mode='HTML',
    )


@dp.message(Command('promo_disable'))
async def promo_disable_handler(message: Message):
    if not is_admin(message.from_user.id):
        await answer_ephemeral(message, '⛔ Эта команда доступна только администратору.', parse_mode=None)
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2:
        await answer_ephemeral(message, 'Формат: <code>/promo_disable CODE</code>', parse_mode='HTML')
        return
    ok, result = await set_promo_enabled(parts[1], False)
    if not ok:
        await answer_ephemeral(message, 'Промокод не найден или указан неверно.', parse_mode=None)
        return
    await answer_ephemeral(message, f'⛔ Промокод <code>{escape(str(result))}</code> отключён.', parse_mode='HTML')


@dp.message(Command('promo_enable'))
async def promo_enable_handler(message: Message):
    if not is_admin(message.from_user.id):
        await answer_ephemeral(message, '⛔ Эта команда доступна только администратору.', parse_mode=None)
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2:
        await answer_ephemeral(message, 'Формат: <code>/promo_enable CODE</code>', parse_mode='HTML')
        return
    ok, result = await set_promo_enabled(parts[1], True)
    if not ok:
        await answer_ephemeral(message, 'Промокод не найден или указан неверно.', parse_mode=None)
        return
    await answer_ephemeral(message, f'✅ Промокод <code>{escape(str(result))}</code> снова активен.', parse_mode='HTML')


@dp.message(Command('promocodes'))
async def promocodes_handler(message: Message):
    if not is_admin(message.from_user.id):
        await answer_ephemeral(message, '⛔ Эта команда доступна только администратору.', parse_mode=None)
        return
    rows = await get_promo_codes(50)
    if not rows:
        await answer_ephemeral(message, 'Промокодов пока нет.', parse_mode=None)
        return
    lines = ['🎟 <b>Последние промокоды</b>']
    for promo in rows:
        status = '✅' if promo['enabled'] else '⛔'
        limit = '∞' if promo['max_uses'] is None else str(promo['max_uses'])
        lines.append(
            f'{status} <code>{escape(str(promo["code"]))}</code> — '
            f'<b>{promo["reward_coins"]}</b> 🪙 · {promo["used_count"]}/{limit}'
        )
    await answer_ephemeral(message, '\n'.join(lines), parse_mode='HTML')


async def prepare_broadcast(message: Message, state: FSMContext, raw_text: str):
    text = raw_text.strip()
    if not text:
        await state.set_state(BroadcastMessage.waiting_text)
        await answer_ephemeral(
            message,
            '📣 Пришли текст рассылки одним сообщением.\n\n/cancel — отмена.',
            parse_mode=None,
        )
        return
    if len(text) > BROADCAST_MAX_LENGTH:
        await state.set_state(BroadcastMessage.waiting_text)
        await answer_ephemeral(
            message,
            f'Сообщение слишком длинное: {len(text)} символов. Максимум — {BROADCAST_MAX_LENGTH}.',
            parse_mode=None,
        )
        return

    target_ids = await get_broadcast_user_ids()
    if not target_ids:
        await state.clear()
        await answer_ephemeral(message, 'В базе пока нет пользователей для рассылки.', parse_mode=None)
        return

    token = secrets.token_urlsafe(6)
    await state.set_state(BroadcastMessage.waiting_confirm)
    await state.update_data(broadcast_text=text, broadcast_token=token)
    # Preview carries its own header, so keep it safely below Telegram's 4096-char limit.
    preview_text = text if len(text) <= 3400 else text[:3400] + '\n… (предпросмотр сокращён)'
    preview = (
        f'📣 Предпросмотр рассылки\n'
        f'Получателей: {len(target_ids)}\n'
        f'Длина: {len(text)}/{BROADCAST_MAX_LENGTH}\n\n'
        f'{preview_text}'
    )

    sent = await bot.send_message(
        message.chat.id,
        preview,
        reply_markup=broadcast_confirm_keyboard(token),
        parse_mode=None,
    )
    await remember_ephemeral(
        message.from_user.id,
        message.chat.id,
        sent.message_id,
        cleanup_on_next=False,
        expires_in_seconds=BROADCAST_CONFIRM_TTL_SECONDS,
    )
    await state.update_data(
        broadcast_preview_chat_id=message.chat.id,
        broadcast_preview_message_id=sent.message_id,
    )


async def deliver_broadcast(text: str):
    """Send one plain-text broadcast with conservative rate limiting."""
    async with BROADCAST_LOCK:
        target_ids = await get_broadcast_user_ids()
        delivered = 0
        blocked = 0
        failed = 0

        for user_id in target_ids:
            sent = False
            for attempt in range(3):
                try:
                    await bot.send_message(user_id, text, parse_mode=None)
                    delivered += 1
                    sent = True
                    break
                except TelegramRetryAfter as exc:
                    retry = max(0.1, float(getattr(exc, 'retry_after', 1)))
                    if attempt < 2:
                        await asyncio.sleep(retry + 0.25)
                        continue
                    failed += 1
                    break
                except TelegramForbiddenError:
                    # Пользователь заблокировал бота или больше не разрешает ему писать.
                    blocked += 1
                    break
                except TelegramBadRequest as exc:
                    print(f'Broadcast bad request user={user_id}: {exc}')
                    failed += 1
                    break
                except Exception as exc:
                    print(f'Broadcast error user={user_id}: {exc}')
                    if attempt < 2:
                        await asyncio.sleep(1.0)
                        continue
                    failed += 1
                    break
            if sent:
                await asyncio.sleep(BROADCAST_SEND_INTERVAL_SECONDS)

        return {
            'total': len(target_ids),
            'delivered': delivered,
            'blocked': blocked,
            'failed': failed,
        }


@dp.message(Command('broadcast'))
async def broadcast_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await answer_ephemeral(message, '⛔ Эта команда доступна только администратору.', parse_mode=None)
        return
    parts = (message.text or '').split(maxsplit=1)
    await prepare_broadcast(message, state, parts[1] if len(parts) > 1 else '')


@dp.message(BroadcastMessage.waiting_text)
async def broadcast_text_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if (message.text or '').strip().lower().startswith('/cancel'):
        await state.clear()
        await answer_ephemeral(message, 'Рассылка отменена.', parse_mode=None)
        return
    if not message.text:
        await answer_ephemeral(message, 'Для рассылки нужен именно текст.', parse_mode=None)
        return
    await prepare_broadcast(message, state, message.text)


@dp.callback_query(F.data.startswith('broadcast_cancel:'))
async def broadcast_cancel_handler(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await safe_callback_answer(c, 'Нет доступа', show_alert=True)
        return
    data = await state.get_data()
    token = c.data.split(':', 1)[1] if ':' in c.data else ''
    if not token or token != data.get('broadcast_token'):
        await safe_callback_answer(c, 'Этот предпросмотр уже устарел.', show_alert=True)
        return
    await state.clear()
    await safe_callback_answer(c, 'Рассылка отменена')
    if c.message and await try_delete_message(c.message.chat.id, c.message.message_id):
        await forget_ephemeral(c.from_user.id, c.message.chat.id, c.message.message_id)


@dp.callback_query(F.data.startswith('broadcast_confirm:'))
async def broadcast_confirm_handler(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await safe_callback_answer(c, 'Нет доступа', show_alert=True)
        return

    data = await state.get_data()
    text = data.get('broadcast_text')
    token = c.data.split(':', 1)[1] if ':' in c.data else ''
    if (
        not text
        or not token
        or token != data.get('broadcast_token')
        or await state.get_state() != BroadcastMessage.waiting_confirm.state
    ):
        await safe_callback_answer(c, 'Предпросмотр устарел. Запусти /broadcast заново.', show_alert=True)
        return

    await state.clear()
    await safe_callback_answer(c, 'Рассылка запущена')
    if c.message and await try_delete_message(c.message.chat.id, c.message.message_id):
        await forget_ephemeral(c.from_user.id, c.message.chat.id, c.message.message_id)

    progress = await bot.send_message(c.message.chat.id if c.message else c.from_user.id, '📣 Рассылка выполняется…', parse_mode=None)
    stats = await deliver_broadcast(text)
    report = (
        '✅ Рассылка завершена.\n\n'
        f'Всего получателей: {stats["total"]}\n'
        f'Доставлено: {stats["delivered"]}\n'
        f'Бот заблокирован: {stats["blocked"]}\n'
        f'Ошибок: {stats["failed"]}'
    )
    try:
        await bot.edit_message_text(report, chat_id=progress.chat.id, message_id=progress.message_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


@dp.callback_query(F.data == 'feed')
async def feed_handler(c):
    await perform_action(c, action_key='feed', state='eat', action_text='🍓 Mochi вкусно поел и довольно урчит.', base_xp=6, base_coins=2, hunger_delta=15, happiness_delta=2)


@dp.callback_query(F.data == 'play')
async def play_handler(c):
    pet = await get_pet_by_user(c.from_user.id)
    if pet and pet['energy'] < 10:
        await safe_callback_answer(c, 'Питомец слишком устал 😴', show_alert=True)
        return
    await perform_action(c, action_key='play', state='play', action_text='🧶 Mochi носится за клубком по всей комнате.', base_xp=8, base_coins=3, happiness_delta=15, energy_delta=-10, hunger_delta=-3)


@dp.callback_query(F.data == 'love')
async def love_handler(c):
    await perform_action(c, action_key='love', state='love', action_text='❤️ Mochi замурчал и прижался поближе.', base_xp=6, base_coins=2, happiness_delta=5, love_delta=10)


@dp.callback_query(F.data == 'sleep')
async def sleep_handler(c):
    await perform_action(c, action_key='sleep', state='sleep', action_text='🌙 Mochi свернулся клубочком и уснул. Zzz...', base_xp=4, base_coins=1, energy_delta=100)


# ---- Переименование ----
@dp.callback_query(F.data == 'rename')
async def rename_handler(c: CallbackQuery, state: FSMContext):
    pet = await get_pet_by_user(c.from_user.id)
    await safe_callback_answer(c)
    if not pet:
        return
    await state.set_state(RenamePet.waiting_name)
    await state.update_data(rename_pet_id=pet['id'])
    await answer_ephemeral(c.message, f'✏️ Сейчас питомца зовут <b>{escape(str(pet["name"]))}</b>.\nНапиши новое имя (до 24 символов).\n/cancel — отмена.', parse_mode='HTML')


@dp.message(Command('cancel'))
async def cancel_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    preview_chat_id = data.get('broadcast_preview_chat_id')
    preview_message_id = data.get('broadcast_preview_message_id')
    await state.clear()

    if preview_chat_id and preview_message_id:
        if await try_delete_message(int(preview_chat_id), int(preview_message_id)):
            await forget_ephemeral(message.from_user.id, int(preview_chat_id), int(preview_message_id))
    await answer_ephemeral(message, 'Окей, отменил 😼')


@dp.message(RenamePet.waiting_name)
async def rename_receive(message: Message, state: FSMContext):
    data = await state.get_data()
    pet_id = data.get('rename_pet_id')
    pet = await get_pet_by_id(pet_id) if pet_id else None
    owners = await get_couple_user_ids_by_pet(pet_id) if pet_id else []
    if not pet or message.from_user.id not in owners:
        await state.clear()
        await answer_ephemeral(message, 'Этот Mochi больше недоступен. Открой переименование заново.')
        return
    name = ' '.join((message.text or '').strip().split())
    if not name or len(name) > 24:
        await answer_ephemeral(message, 'Имя должно быть от 1 до 24 символов. Давай ещё раз.')
        return
    await rename_pet(pet['id'], name)
    await state.clear()
    await answer_ephemeral(message, f'✨ Теперь его зовут <b>{escape(name)}</b>!', parse_mode='HTML')
    await refresh_pet_screens(pet['id'], f'✏️ Теперь меня зовут {name}!')

@dp.callback_query(F.data == 'dailies')
async def dailies_handler(c):
    await safe_callback_answer(c)
    pet = await get_pet_by_user(c.from_user.id)
    if not pet:
        return
    tasks = await get_daily_tasks(pet['id'])
    lines = ['📅 <b>Ежедневные задания</b>\n']
    for t in tasks:
        mark = '✅' if t['completed'] else '▫️'
        lines.append(f'{mark} {t["title"]} — <b>{t["progress"]}/{t["target"]}</b>\n   +{t["reward_coins"]}🪙 · +{t["reward_xp"]}XP')
    await answer_ephemeral(c.message, '\n'.join(lines), parse_mode='HTML', reply_markup=back_keyboard())


# ---- Магазин ----
@dp.callback_query(F.data == 'shop')
async def shop_handler(c):
    await safe_callback_answer(c)
    pet = await get_pet_by_user(c.from_user.id)
    if not pet:
        return
    inv = await get_inventory(pet['id'])
    owned = {r['item_key'] for r in inv}
    await answer_ephemeral(c.message, f'🛍 <b>Магазин комнаты</b>\nБаланс: <b>{pet["coins"]} 🪙</b>\n\nПокупки общие для двух хозяев.', parse_mode='HTML', reply_markup=shop_keyboard(SHOP_ITEMS, owned))


@dp.callback_query(F.data.startswith('buy:'))
async def buy_handler(c):
    # Callback гасим сразу, а результат покупки уже пишем сообщением — иначе опять можно словить old query.
    await safe_callback_answer(c)
    key = c.data.split(':', 1)[1]
    item = SHOP_ITEMS.get(key)
    pet = await get_pet_by_user(c.from_user.id)
    if not item or not pet:
        return
    bought, reason = await purchase_item(pet['id'], key, item['price'])
    if not bought:
        if reason == 'owned':
            await answer_ephemeral(c.message, '✅ Этот предмет у вас уже куплен.')
        elif reason == 'not_enough_coins':
            await answer_ephemeral(c.message, '🥲 Монет не хватает.')
        else:
            await answer_ephemeral(c.message, 'Не удалось купить предмет 😿')
        return
    before_stage = current_growth_stage(pet['level'])
    unlocked = await check_achievements(pet['id'])
    updated = await get_pet_by_id(pet['id'])
    after_stage = current_growth_stage(updated['level']) if updated else before_stage
    await answer_ephemeral(c.message, f'🛍 Куплено: <b>{item["emoji"]} {item["name"]}</b> за {item["price"]} 🪙\nТеперь поставь предмет через 🏠 Комната.', parse_mode='HTML')
    await refresh_pet_screens(pet['id'])
    if before_stage['key'] != after_stage['key']:
        await refresh_room_media_for_pet(pet['id'])
    for ach in unlocked:
        title, _, coins, xp = ACHIEVEMENTS[ach]
        await send_temp_text(c.from_user.id, f'🏆 {title} (+{coins}🪙, +{xp}XP)')


# ---- Комната ----
@dp.callback_query(F.data == 'room')
async def room_handler(c):
    await safe_callback_answer(c)
    pet = await get_pet_by_user(c.from_user.id)
    if not pet:
        return
    eq = await get_room_equipment(pet['id'])
    rows = []
    text = ['🏠 <b>Комната питомца</b>\n']
    for slot, title in SLOT_NAMES.items():
        current = item_label(eq[slot]) if slot in eq else 'Базовый'
        text.append(f'{title}: <b>{current}</b>')
        rows.append((slot, f'{title}: {current}'))
    text.append('\nВыбери слот и поставь купленный предмет.')
    await answer_ephemeral(c.message, '\n'.join(text), parse_mode='HTML', reply_markup=room_keyboard(rows))


@dp.callback_query(F.data.startswith('room_slot:'))
async def room_slot_handler(c):
    await safe_callback_answer(c)
    slot = c.data.split(':', 1)[1]
    pet = await get_pet_by_user(c.from_user.id)
    if not pet or slot not in SLOT_NAMES:
        return
    inv = await get_inventory(pet['id'])
    available = []
    for row in inv:
        item = SHOP_ITEMS.get(row['item_key'])
        if item and item['slot'] == slot:
            available.append({'key': row['item_key'], **item})
    if not available:
        await answer_ephemeral(c.message, f'{SLOT_NAMES[slot]}: пока ничего не куплено. Загляни в 🛍 Магазин.', reply_markup=back_keyboard('room'))
        return
    await answer_ephemeral(c.message, f'{SLOT_NAMES[slot]} — выбери предмет:', reply_markup=equip_keyboard(available))


@dp.callback_query(F.data.startswith('equip:'))
async def equip_handler(c):
    await safe_callback_answer(c)
    key = c.data.split(':', 1)[1]
    item = SHOP_ITEMS.get(key)
    pet = await get_pet_by_user(c.from_user.id)
    if not item or not pet:
        return
    if not await equip_item(pet['id'], item['slot'], key):
        await answer_ephemeral(c.message, 'Этого предмета нет в инвентаре 😼')
        return
    await answer_ephemeral(c.message, f'🏠 Поставлено: <b>{item["emoji"]} {item["name"]}</b>', parse_mode='HTML')
    unlocked = await check_achievements(pet['id'])
    # Один media-edit вместо caption-edit + media-edit подряд. Иначе при быстром тесте магазина Telegram душит Flood Control.
    await refresh_room_media_for_pet(pet['id'])
    for ach in unlocked:
        title, _, coins, xp = ACHIEVEMENTS[ach]
        await send_temp_text(c.from_user.id, f'🏆 {title} (+{coins}🪙, +{xp}XP)')


@dp.callback_query(F.data == 'noop')
async def noop_handler(c):
    await safe_callback_answer(c)


# ---- Достижения ----
@dp.callback_query((F.data == 'achievements') | F.data.startswith('achievements:'))
async def achievements_handler(c):
    await safe_callback_answer(c)
    pet = await get_pet_by_user(c.from_user.id)
    if not pet:
        return

    before_stage = current_growth_stage(pet['level'])
    newly_unlocked = await check_achievements(pet['id'])
    unlocked = await get_achievements(pet['id'])
    if newly_unlocked:
        updated = await get_pet_by_id(pet['id'])
        await refresh_pet_screens(pet['id'])
        if updated:
            after_stage = current_growth_stage(updated['level'])
            if before_stage['key'] != after_stage['key']:
                await refresh_room_media_for_pet(pet['id'])

    try:
        page = int(c.data.split(':', 1)[1]) if ':' in c.data else 0
    except ValueError:
        page = 0

    items = list(ACHIEVEMENTS.items())
    per_page = 7
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * per_page:(page + 1) * per_page]

    lines = [f'🏆 <b>Достижения</b> · {len(unlocked)}/{len(items)}\n']
    for key, (title, desc, coins, xp) in chunk:
        mark = '✅' if key in unlocked else '🔒'
        lines.append(f'{mark} <b>{title}</b> — {desc}\n   Награда: {coins}🪙 · {xp}XP')

    await answer_ephemeral(
        c.message,
        '\n'.join(lines),
        parse_mode='HTML',
        reply_markup=achievements_keyboard(page, total_pages),
    )


# ---- Совместные действия ----
@dp.callback_query(F.data == 'joint_start')
async def joint_start_handler(c):
    pet = await get_pet_by_user(c.from_user.id)
    couple = await get_active_couple(c.from_user.id)
    if not pet or not couple or not couple['user2_id']:
        await safe_callback_answer(c, 'Нужен второй хозяин 💞', show_alert=True)
        return
    jid, error = await create_joint_action(pet['id'], c.from_user.id)
    if error:
        await safe_callback_answer(c, error, show_alert=True)
        return
    await safe_callback_answer(c, 'Позвал второго хозяина 🤝')
    partner_id = await get_partner_id(c.from_user.id, couple['id'])
    actor = await get_user(c.from_user.id)
    name = actor['first_name'] if actor and actor['first_name'] else 'Партнёр'
    if partner_id:
        await send_temp_text(
            partner_id,
            f'🤝 <b>{escape(str(name))}</b> зовёт сделать что-то вместе с <b>{escape(str(pet["name"]))}</b>!\nУ вас 10 минут.',
            delay=600,
            reply_markup=joint_finish_keyboard(jid),
            cleanup_on_next=False,
        )
    await answer_ephemeral(c.message, '🤝 Приглашение отправлено. Второй хозяин должен нажать кнопку в течение 10 минут.')


@dp.callback_query(F.data.startswith('joint_finish:'))
async def joint_finish_handler(c):
    try:
        jid = int(c.data.split(':', 1)[1])
    except (ValueError, IndexError):
        await safe_callback_answer(c, 'Кривое приглашение', show_alert=True)
        return
    ok, result = await complete_joint_action(jid, c.from_user.id)
    if not ok:
        await safe_callback_answer(c, result, show_alert=True)
        return
    await safe_callback_answer(c, 'Получилось! 💞')
    if c.message and await try_delete_message(c.message.chat.id, c.message.message_id):
        await forget_ephemeral(c.from_user.id, c.message.chat.id, c.message.message_id)
    pet_id = result
    pet = await update_pet(pet_id, happiness_delta=15, love_delta=15)
    _, reward_messages, stage_changed = await game_rewards(pet_id, 'joint', 25, 15)
    text = f'🤝 Вы вместе поиграли и обняли {pet["name"]}. +15 счастья, +15 любви!'
    await refresh_pet_screens(pet_id)
    if stage_changed:
        await refresh_room_media_for_pet(pet_id)
    couple_id = await get_couple_id_by_pet(pet_id)
    users = await get_couple_user_ids_by_pet(pet_id)
    active_targets = [uid for uid in users if await is_couple_active_for_user(uid, couple_id)]
    for uid in active_targets:
        await send_temp_animation(uid, pet_id, 'love', caption=escape(text), delay=3.2)
        await asyncio.sleep(0.35)
    for uid in users:
        await send_temp_text(uid, '💞 Совместное действие завершено! +15🪙 · +25XP' + ('\n' + '\n'.join(reward_messages) if reward_messages else ''), 10)


# ---- Передать через Mochi ----
@dp.callback_query(F.data == 'mochi_mail')
async def mochi_mail_handler(c: CallbackQuery):
    couple = await get_active_couple(c.from_user.id)
    pet = await get_pet_by_user(c.from_user.id)
    if not couple or not pet:
        await safe_callback_answer(c, 'Сначала выбери Mochi 🐱', show_alert=True)
        return
    if not couple['user2_id']:
        await safe_callback_answer(c, 'Сначала пригласи второго хозяина 💞', show_alert=True)
        return
    await safe_callback_answer(c)
    partner = await partner_label(c.from_user.id, couple)
    await answer_ephemeral(
        c.message,
        f'💌 <b>Передать через {escape(str(pet["name"]))}</b>\n'
        f'Получатель: <b>{escape(partner)}</b>\n\n'
        'Выбери фразу или напиши свою:',
        parse_mode='HTML',
        reply_markup=mochi_mail_keyboard(),
    )


@dp.callback_query(F.data.startswith('mochi_mail_send:'))
async def mochi_mail_send_handler(c: CallbackQuery):
    key = c.data.split(':', 1)[1]
    phrase = MOCHI_MAIL_PHRASES.get(key)
    if not phrase:
        await safe_callback_answer(c, 'Такой фразы нет 😼', show_alert=True)
        return
    await safe_callback_answer(c, 'Mochi побежал передавать 💌')
    ok, result = await send_mochi_mail(c.from_user.id, phrase)
    if not ok:
        await answer_ephemeral(c.message, result)
        return
    await answer_ephemeral(c.message, '💌 Доставлено! Mochi всё передал.')


@dp.callback_query(F.data == 'mochi_mail_custom')
async def mochi_mail_custom_handler(c: CallbackQuery, state: FSMContext):
    couple = await get_active_couple(c.from_user.id)
    if not couple or not couple['user2_id']:
        await safe_callback_answer(c, 'Нужен второй хозяин 💞', show_alert=True)
        return
    pet = await get_pet_by_user(c.from_user.id)
    if not pet:
        await safe_callback_answer(c, 'Mochi не найден 😿', show_alert=True)
        return
    await safe_callback_answer(c)
    await state.set_state(MochiMail.waiting_custom_text)
    await state.update_data(mail_couple_id=couple['id'], mail_pet_id=pet['id'])
    await answer_ephemeral(
        c.message,
        '✍️ Напиши сообщение, которое Mochi передаст второму хозяину.\n'
        'До <b>240 символов</b>.\n/cancel — отмена.',
        parse_mode='HTML',
    )


@dp.message(MochiMail.waiting_custom_text)
async def mochi_mail_custom_receive(message: Message, state: FSMContext):
    text = ' '.join((message.text or '').strip().split())
    if not text:
        await answer_ephemeral(message, 'Напиши хотя бы что-нибудь 😼')
        return
    if len(text) > 240:
        await answer_ephemeral(message, f'Слишком длинно: {len(text)}/240 символов. Сократи немного.')
        return

    data = await state.get_data()
    ok, result = await send_mochi_mail(
        message.from_user.id,
        text,
        couple_id=data.get('mail_couple_id'),
        pet_id=data.get('mail_pet_id'),
    )
    if not ok:
        await state.clear()
        await answer_ephemeral(message, result)
        return

    await state.clear()
    await answer_ephemeral(message, '💌 Mochi доставил твоё сообщение.')


# ---- Несколько питомцев с разными людьми ----
async def build_bond_items(user_id):
    couples = await get_couples_by_user(user_id)
    return [(cp, await partner_label(user_id, cp) if cp['user2_id'] else None) for cp in couples]


@dp.callback_query(F.data == 'bonds')
async def bonds_handler(c):
    await touch_user(c.from_user.id)
    active = await get_active_couple(c.from_user.id)
    await safe_callback_answer(c)
    await answer_ephemeral(c.message, '🐾 <b>Мои питомцы</b>\nВыбери, с кем открыть общего питомца:', parse_mode='HTML', reply_markup=bonds_keyboard(await build_bond_items(c.from_user.id), active['id'] if active else None))


@dp.callback_query(F.data == 'bond_new')
async def bond_new_handler(c):
    await touch_user(c.from_user.id)
    await safe_callback_answer(c, 'Создаю нового питомца 🐣')
    couple = await create_couple(c.from_user.id)
    url = f'https://t.me/{BOT_USERNAME}?start=join_{couple["invite_code"]}'
    await answer_ephemeral(c.message, '🐣 Новый питомец ждёт второго хозяина.\n\nОтправь ссылку другому человеку:\n\n' + url)
    await refresh_screen_media(c.from_user.id, action_text='👀 Новый питомец ждёт второго хозяина.')


@dp.callback_query(F.data.startswith('bond_select:'))
async def bond_select_handler(c):
    try:
        cid = int(c.data.split(':', 1)[1])
    except (ValueError, IndexError):
        await safe_callback_answer(c, 'Кривой ID', show_alert=True)
        return
    couple = await set_active_couple(c.from_user.id, cid)
    if not couple:
        await safe_callback_answer(c, 'Это не твой питомец 😼', show_alert=True)
        return
    await safe_callback_answer(c, 'Переключил 🐾')
    await refresh_screen_media(c.from_user.id)


@dp.callback_query(F.data == 'menu_back')
async def menu_back_handler(c):
    await safe_callback_answer(c)
    await refresh_screen(c.from_user.id)
    try:
        await c.message.delete()
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data == 'status')
async def status_handler(c):
    await touch_user(c.from_user.id)
    await bind_screen_to_callback(c)
    cancel_screen_action(c.from_user.id)
    await safe_callback_answer(c, 'Обновляю ✨')
    await get_pet_by_user(c.from_user.id)
    await sync_screen(c.from_user.id)


@dp.callback_query(F.data == 'partner')
async def partner_handler(c):
    await touch_user(c.from_user.id)
    couple = await get_active_couple(c.from_user.id)
    if not couple:
        await safe_callback_answer(c, 'Сначала /start', show_alert=True)
        return
    if couple['user2_id']:
        await safe_callback_answer(c, f'💞 Этот питомец с {await partner_label(c.from_user.id, couple)}', show_alert=True)
    else:
        url = f'https://t.me/{BOT_USERNAME}?start=join_{couple["invite_code"]}'
        await safe_callback_answer(c)
        await answer_ephemeral(c.message, '👀 Этот питомец ждёт второго хозяина.\n\nСсылка:\n\n' + url)


async def decay_loop():
    first_pass = True
    while True:
        try:
            changed = set(await apply_decay_to_all_pets())
            active = set(await get_active_pet_ids())
            targets = active if first_pass else (changed & active)
            for pet_id in sorted(targets):
                try:
                    await refresh_pet_screens(pet_id)
                except Exception as exc:
                    print(f'Decay refresh error pet={pet_id}: {exc}')
            first_pass = False
        except Exception as exc:
            print(f'Decay loop error: {exc}')
        # Проверяем раз в минуту. Баланс остаётся -3 сытости / -1 настроения
        # за полный час; UI синхронизируется автоматически после наступления тика.
        await asyncio.sleep(DECAY_REFRESH_INTERVAL_SECONDS)


async def main():
    await init_db()

    decay_task = asyncio.create_task(decay_loop(), name='mochi-decay-loop')
    if ADMIN_IDS:
        print(f'Mochi запущен 🐱 · admins: {len(ADMIN_IDS)} · /broadcast + promo management enabled')
    else:
        print('Mochi запущен 🐱 · ⚠️ ADMIN_IDS не задан: /broadcast и управление промокодами отключены')
    try:
        await dp.start_polling(bot)
    finally:
        decay_task.cancel()
        try:
            await decay_task
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    asyncio.run(main())
