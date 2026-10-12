import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
# Загружаем только .env этого проекта, а не случайный файл из текущей рабочей папки.
load_dotenv(ROOT / '.env')

BOT_TOKEN = (os.getenv('BOT_TOKEN') or '').strip()
BOT_USERNAME = (os.getenv('BOT_USERNAME') or '').strip().lstrip('@')


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for value in re.split(r'[\s,;]+', raw.strip()):
        if not value:
            continue
        try:
            user_id = int(value)
        except ValueError as exc:
            raise RuntimeError('ADMIN_IDS must contain Telegram numeric IDs separated by commas.') from exc
        if user_id <= 0:
            raise RuntimeError('ADMIN_IDS must contain positive Telegram numeric IDs.')
        ids.add(user_id)
    return frozenset(ids)


ADMIN_IDS = _parse_admin_ids(os.getenv('ADMIN_IDS') or '')

if not BOT_TOKEN:
    raise RuntimeError('BOT_TOKEN is missing. Copy .env.example to .env and fill it in.')

if not BOT_USERNAME:
    raise RuntimeError('BOT_USERNAME is missing. Copy .env.example to .env and fill it in.')

if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', BOT_USERNAME):
    raise RuntimeError('BOT_USERNAME must be a Telegram username without @ (5-32 letters, digits or underscores).')
