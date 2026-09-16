from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🍓 Покормить', callback_data='feed'), InlineKeyboardButton(text='🧶 Играть', callback_data='play')],
        [InlineKeyboardButton(text='❤️ Погладить', callback_data='love'), InlineKeyboardButton(text='🌙 Уложить', callback_data='sleep')],
        [InlineKeyboardButton(text='📅 Ежедневки', callback_data='dailies'), InlineKeyboardButton(text='🤝 Вместе', callback_data='joint_start')],
        [InlineKeyboardButton(text='🛍 Магазин', callback_data='shop'), InlineKeyboardButton(text='🏠 Комната', callback_data='room')],
        [InlineKeyboardButton(text='🏆 Достижения', callback_data='achievements'), InlineKeyboardButton(text='✏️ Имя', callback_data='rename')],
        [InlineKeyboardButton(text='💌 Передать через Mochi', callback_data='mochi_mail')],
        [InlineKeyboardButton(text='🐾 Мои Mochi', callback_data='bonds')],
        [InlineKeyboardButton(text='🔄 Обновить', callback_data='status'), InlineKeyboardButton(text='💞 Партнёр', callback_data='partner')],
    ])


def back_keyboard(callback_data='menu_back'):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data=callback_data)]])


def bonds_keyboard(items, active_couple_id):
    rows=[]
    for couple, partner_name in items:
        prefix='✅' if couple['id']==active_couple_id else '🐱'
        label = partner_name if partner_name else 'Ждёт партнёра…'
        rows.append([InlineKeyboardButton(text=f'{prefix} {label}', callback_data=f'bond_select:{couple["id"]}')])
    rows.append([InlineKeyboardButton(text='➕ Новый Mochi с другим человеком', callback_data='bond_new')])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='menu_back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shop_keyboard(items, owned_keys):
    rows=[]
    for key, item in items.items():
        owned = '✅' if key in owned_keys else f'💰{item["price"]}'
        rows.append([InlineKeyboardButton(text=f'{owned} {item["emoji"]} {item["name"]}', callback_data=f'buy:{key}')])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='menu_back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def room_keyboard(slot_items):
    rows=[]
    for slot, label in slot_items:
        rows.append([InlineKeyboardButton(text=label, callback_data=f'room_slot:{slot}')])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='menu_back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def equip_keyboard(items):
    rows = [[InlineKeyboardButton(text=f'{item["emoji"]} {item["name"]}', callback_data=f'equip:{item["key"]}')] for item in items]
    rows.append([InlineKeyboardButton(text='⬅️ Комната', callback_data='room')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def joint_finish_keyboard(joint_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🤝 Сделать вместе', callback_data=f'joint_finish:{joint_id}')]
    ])


def achievements_keyboard(page: int, total_pages: int):
    # Разбиваем достижения на страницы, чтобы меню оставалось компактным.
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'achievements:{page - 1}'))
    nav.append(InlineKeyboardButton(text=f'{page + 1}/{total_pages}', callback_data='noop'))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text='➡️', callback_data=f'achievements:{page + 1}'))
    return InlineKeyboardMarkup(inline_keyboard=[nav, [InlineKeyboardButton(text='⬅️ Назад', callback_data='menu_back')]])



def mochi_mail_keyboard():
    # Быстрые фразы — чтобы не заставлять человека каждый раз печатать роман на три тома.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🥺 Скучаю', callback_data='mochi_mail_send:miss'), InlineKeyboardButton(text='❤️ Люблю тебя', callback_data='mochi_mail_send:love')],
        [InlineKeyboardButton(text='🫂 Обними меня', callback_data='mochi_mail_send:hug'), InlineKeyboardButton(text='🍀 Удачи сегодня', callback_data='mochi_mail_send:luck')],
        [InlineKeyboardButton(text='🌞 Доброе утро', callback_data='mochi_mail_send:morning'), InlineKeyboardButton(text='🌙 Спокойной ночи', callback_data='mochi_mail_send:night')],
        [InlineKeyboardButton(text='💗 Ты мне очень важен(а)', callback_data='mochi_mail_send:important')],
        [InlineKeyboardButton(text='🙏 Спасибо, что ты есть', callback_data='mochi_mail_send:thanks')],
        [InlineKeyboardButton(text='✍️ Свой текст', callback_data='mochi_mail_custom')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='menu_back')],
    ])

def broadcast_confirm_keyboard(token: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Отправить всем', callback_data=f'broadcast_confirm:{token}')],
        [InlineKeyboardButton(text='❌ Отмена', callback_data=f'broadcast_cancel:{token}')],
    ])

