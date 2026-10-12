from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

import imageio_ffmpeg
from PIL import Image

ROOT = Path(__file__).resolve().parent
ROOM = ROOT / "assets" / "room"
ITEMS = ROOM / "items"
PETS = ROOM / "pets"
PET_GROWTH = ROOM / "pets_growth"
PET_ANIMS = ROOT / "assets" / "pets"
CACHE = ROOT / "cache" / "rooms"
CACHE.mkdir(parents=True, exist_ok=True)

BASE_PATH = ROOM / "base.png"
with Image.open(BASE_PATH) as _base:
    CANVAS = _base.size

SAFE_MARGIN = 8
FRAME_COUNT = 4
# Timing follows the supplied 6-frame pixel-art pack. MP4 uses a constant frame
# duration, so eat uses the rounded mean of its source GIF frame timings.
DURATIONS = {
    "idle": 170, "happy": 110, "sad": 190, "sleep": 260, "hungry": 160,
    "play": 100, "sick": 230, "eat": 125, "love": 110,
}
RENDER_VERSION = "layout-v43-new-pet-assets"

# Каждый тип предмета живёт в своей зоне комнаты. Координаты подобраны под base.png 512x512.
SLOT_LAYOUT = {
    "window": {"anchor": (257, 172), "max_size": (245, 250), "align": "center"},
    "decor": {"anchor": (83, 133), "max_size": (105, 115), "align": "center"},
    "plant": {"anchor": (80, 392), "max_size": (150, 198), "align": "bottom"},
    "lamp": {"anchor": (470, 486), "max_size": (138, 240), "align": "bottom"},
    "rug": {"anchor": (250, 496), "max_size": (236, 98), "align": "bottom"},
    "bed": {"anchor": (360, 338), "max_size": (130, 92), "align": "bottom"},
    "bowl": {"anchor": (159, 477), "max_size": (68, 50), "align": "bottom"},
    "toy": {"anchor": (337, 481), "max_size": (62, 58), "align": "bottom"},
}

# Точечные поправки для ассетов с необычной формой.
ITEM_OVERRIDES = {
    "decor_star_garland": {"anchor": (88, 118), "max_size": (120, 65)},
    "decor_photo_garland": {"anchor": (91, 120), "max_size": (125, 62)},
    "decor_macrame_plant": {"anchor": (89, 147), "max_size": (78, 118)},
    "toy_wand": {"anchor": (349, 481), "max_size": (72, 56)},
    "plant_bonsai": {"anchor": (82, 392), "max_size": (152, 152)},
    "bed_canopy": {"anchor": (362, 336), "max_size": (124, 98)},
    "plant_monstera": {"anchor": (78, 392), "max_size": (154, 186)},
    "bed_bunny": {"anchor": (360, 340), "max_size": (130, 94)},
    "bed_flower": {"anchor": (360, 340), "max_size": (132, 96)},
    "bed_cloud": {"anchor": (360, 338), "max_size": (132, 94)},
    "plant_calathea": {"anchor": (78, 392), "max_size": (150, 190)},
    "plant_blossom": {"anchor": (78, 392), "max_size": (150, 188)},
    "plant_ficus": {"anchor": (78, 392), "max_size": (146, 190)},
    "lamp_flowers": {"anchor": (470, 486), "max_size": (146, 236)},
    "lamp_bow_tall": {"anchor": (470, 486), "max_size": (132, 240)},
}

# Координаты четырёх стёкол встроенного окна base.png.
WINDOW_PANES = [
    (181, 62, 248, 125),
    (261, 62, 328, 125),
    (181, 132, 248, 196),
    (261, 132, 328, 196),
]

BACK_SLOTS = ["decor", "plant", "lamp", "rug", "bed"]
FRONT_SLOTS = ["bowl", "toy"]

GROWTH_STAGES = [
    {"key": "baby", "title": "Малыш", "min_level": 1},
    {"key": "kitten", "title": "Котёнок", "min_level": 5},
    {"key": "teen", "title": "Подросток", "min_level": 12},
    {"key": "adult", "title": "Взрослый Mochi", "min_level": 20},
]

PET_FRAME_CANVAS = (224, 172)
PET_TARGET_SIZE = {
    "baby": (140, 110),
    "kitten": (158, 126),
    "teen": (176, 140),
    "adult": (192, 150),
}


def growth_stage(level: int | None) -> dict:
    level = max(1, int(level or 1))
    current = GROWTH_STAGES[0]
    for stage in GROWTH_STAGES:
        if level >= stage["min_level"]:
            current = stage
        else:
            break
    return current


def _asset_signature(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return f"{path.name}:missing"
    return f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def _pet_asset_path(stage_key: str, state: str) -> Path:
    candidates = [
        PET_ANIMS / stage_key / f"{state}.gif",
        PET_ANIMS / stage_key / "idle.gif",
        PET_GROWTH / stage_key / f"{state}.png",
        PET_GROWTH / stage_key / "idle.png",
        PETS / f"{state}.png",
        PETS / "idle.png",
    ]
    return next((path for path in candidates if path.exists()), candidates[-1])


def _equipment_key(equipment: dict[str, str], state: str, stage_key: str) -> str:
    pet_path = _pet_asset_path(stage_key, state)
    parts = [
        RENDER_VERSION,
        stage_key,
        state,
        _asset_signature(BASE_PATH),
        _asset_signature(pet_path),
    ]
    for slot in sorted(equipment):
        item = equipment[slot]
        item_path = ITEMS / f"{item}.png"
        parts.append(f"{slot}={item}:{_asset_signature(item_path)}")
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=1)
def _background_template() -> Image.Image:
    with Image.open(BASE_PATH) as img:
        return img.convert("RGBA")


def _load_background() -> Image.Image:
    # Возвращаем копию: alpha_composite изменяет canvas на месте.
    return _background_template().copy()


def _load_item(item_name: str) -> Image.Image | None:
    path = ITEMS / f"{item_name}.png"
    if not path.exists():
        return None
    with Image.open(path) as src:
        img = src.convert("RGBA")
    bbox = img.getchannel("A").getbbox()
    if not bbox:
        return None
    return img.crop(bbox)


def _fit_item(item: Image.Image, max_size: tuple[int, int]) -> Image.Image:
    max_w, max_h = max_size
    ratio = min(max_w / max(1, item.width), max_h / max(1, item.height), 1.0)
    w = max(1, round(item.width * ratio))
    h = max(1, round(item.height * ratio))
    resampling = Image.Resampling.LANCZOS if ratio < 1 else Image.Resampling.NEAREST
    return item.resize((w, h), resampling)


@lru_cache(maxsize=128)
def _prepared_item(item_name: str, max_size: tuple[int, int]) -> Image.Image | None:
    # Кэшируем уже уменьшенный слой, а не огромный исходный PNG — заметно меньше RAM.
    item = _load_item(item_name)
    if item is None:
        return None
    return _fit_item(item, max_size)


def _safe_position(item: Image.Image, anchor: tuple[int, int], align: str) -> tuple[int, int]:
    ax, ay = anchor
    x = ax - item.width // 2
    y = ay - item.height if align == "bottom" else ay - item.height // 2
    x = max(SAFE_MARGIN, min(x, CANVAS[0] - item.width - SAFE_MARGIN))
    y = max(SAFE_MARGIN, min(y, CANVAS[1] - item.height - SAFE_MARGIN))
    return x, y


@lru_cache(maxsize=16)
def _window_landscape(item_name: str) -> Image.Image | None:
    src = _load_item(item_name)
    if src is None:
        return None
    w, h = src.size
    left, top = int(w * 0.31), int(h * 0.24)
    right, bottom = max(left + 1, int(w * 0.72)), max(top + 1, int(h * 0.79))
    crop = src.crop((left, top, min(w, right), min(h, bottom)))
    if crop.width <= 0 or crop.height <= 0:
        return None
    return crop.resize((147, 134), Image.Resampling.LANCZOS)


def _draw_window_view(canvas: Image.Image, equipment: dict[str, str]) -> None:
    item_name = equipment.get("window")
    if not item_name:
        return
    landscape = _window_landscape(item_name)
    if landscape is None:
        return

    pieces = [
        landscape.crop((0, 0, 67, 63)),
        landscape.crop((80, 0, 147, 63)),
        landscape.crop((0, 70, 67, 134)),
        landscape.crop((80, 70, 147, 134)),
    ]
    for pane, piece in zip(WINDOW_PANES, pieces):
        x1, y1, x2, y2 = pane
        if piece.size != (x2 - x1, y2 - y1):
            piece = piece.resize((x2 - x1, y2 - y1), Image.Resampling.LANCZOS)
        canvas.alpha_composite(piece, (x1, y1))


def _draw_slot(canvas: Image.Image, equipment: dict[str, str], slot: str) -> None:
    item_name = equipment.get(slot)
    if not item_name or slot not in SLOT_LAYOUT:
        return

    cfg = dict(SLOT_LAYOUT[slot])
    cfg.update(ITEM_OVERRIDES.get(item_name, {}))
    max_size = tuple(cfg["max_size"])
    item = _prepared_item(item_name, max_size)
    if item is None:
        return
    x, y = _safe_position(item, tuple(cfg["anchor"]), cfg["align"])
    canvas.alpha_composite(item, (x, y))


def _read_pet_source_frames(stage_key: str, state: str) -> list[Image.Image]:
    source = _pet_asset_path(stage_key, state)
    if not source.exists():
        return []

    frames: list[Image.Image] = []
    with Image.open(source) as anim:
        count = max(1, int(getattr(anim, "n_frames", 1)))
        for i in range(count):
            anim.seek(i)
            frames.append(anim.convert("RGBA"))
    return frames


def _common_canvas(frames: list[Image.Image]) -> list[Image.Image]:
    if not frames:
        return []
    max_w = max(frame.width for frame in frames)
    max_h = max(frame.height for frame in frames)
    out = []
    for frame in frames:
        if frame.size == (max_w, max_h):
            out.append(frame)
            continue
        canvas = Image.new("RGBA", (max_w, max_h), (0, 0, 0, 0))
        x = (max_w - frame.width) // 2
        y = max_h - frame.height
        canvas.alpha_composite(frame, (x, y))
        out.append(canvas)
    return out


def _union_alpha_bbox(frames: list[Image.Image]) -> tuple[int, int, int, int] | None:
    boxes = [frame.getchannel("A").getbbox() for frame in frames]
    boxes = [box for box in boxes if box]
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _normalize_pet_frames(frames: list[Image.Image], stage_key: str) -> list[Image.Image]:
    """Normalize all frames with one shared crop so animation movement is preserved."""
    frames = _common_canvas(frames)
    bbox = _union_alpha_bbox(frames)
    if not frames or not bbox:
        return [Image.new("RGBA", PET_FRAME_CANVAS, (0, 0, 0, 0))]

    crop_w = max(1, bbox[2] - bbox[0])
    crop_h = max(1, bbox[3] - bbox[1])
    target_w, target_h = PET_TARGET_SIZE.get(stage_key, PET_TARGET_SIZE["baby"])
    ratio = min(target_w / crop_w, target_h / crop_h)
    ratio = max(0.05, min(ratio, 2.0))
    out_w = max(1, round(crop_w * ratio))
    out_h = max(1, round(crop_h * ratio))

    normalized: list[Image.Image] = []
    for frame in frames:
        cropped = frame.crop(bbox)
        if cropped.size != (out_w, out_h):
            # Для пиксельной графики NEAREST сохраняет чёткие границы и не создаёт ореолы.
            cropped = cropped.resize((out_w, out_h), Image.Resampling.NEAREST)
        canvas = Image.new("RGBA", PET_FRAME_CANVAS, (0, 0, 0, 0))
        x = (PET_FRAME_CANVAS[0] - cropped.width) // 2
        y = PET_FRAME_CANVAS[1] - cropped.height - 4
        canvas.alpha_composite(cropped, (x, y))
        normalized.append(canvas)
    return normalized


def _pet_frames(state: str, stage_key: str = "baby") -> list[Image.Image]:
    frames = _normalize_pet_frames(_read_pet_source_frames(stage_key, state), stage_key)
    if not frames:
        frames = [Image.new("RGBA", PET_FRAME_CANVAS, (0, 0, 0, 0))]
    while len(frames) < FRAME_COUNT:
        frames.append(frames[-1].copy())
    return frames


def _place_pet(canvas: Image.Image, pet: Image.Image, state: str) -> tuple[int, int]:
    bottom = 476 if state == "sleep" else 468
    center_x = 250
    x = center_x - pet.width // 2
    y = bottom - pet.height
    x = max(SAFE_MARGIN, min(x, CANVAS[0] - pet.width - SAFE_MARGIN))
    y = max(SAFE_MARGIN, min(y, CANVAS[1] - pet.height - SAFE_MARGIN))
    canvas.alpha_composite(pet, (x, y))
    return x, y


def _compose_frame(equipment: dict[str, str], pet: Image.Image, state: str) -> Image.Image:
    out = _load_background()
    _draw_window_view(out, equipment)
    for slot in BACK_SLOTS:
        _draw_slot(out, equipment, slot)
    _place_pet(out, pet, state)
    for slot in FRONT_SLOTS:
        _draw_slot(out, equipment, slot)
    return out


def _save_animation_mp4(frames: list[Image.Image], duration_ms: int, path: Path) -> None:
    if not frames:
        raise ValueError("no frames")

    width, height = frames[0].size
    width -= width % 2
    height -= height % 2
    fps = max(3, min(10, round(1000 / max(1, duration_ms))))
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    cmd = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
        "-r", str(fps), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-profile:v", "baseline", "-level", "3.0",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-y", str(path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    write_error: Exception | None = None
    try:
        assert proc.stdin is not None
        for frame in frames:
            rgb = frame.convert("RGB")
            if rgb.size != (width, height):
                rgb = rgb.resize((width, height), Image.Resampling.NEAREST)
            try:
                proc.stdin.write(rgb.tobytes())
            except BrokenPipeError as exc:
                write_error = exc
                break
        if not proc.stdin.closed:
            proc.stdin.close()
        assert proc.stderr is not None
        err = proc.stderr.read()
        code = proc.wait()
        if code != 0 or write_error is not None:
            detail = err.decode("utf-8", "ignore").strip()
            raise RuntimeError(f"ffmpeg failed ({code}): {detail or write_error}")
    finally:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def render_room_gif(
    equipment: dict[str, str],
    state: str = "idle",
    pet_level: int = 1,
) -> tuple[Path, str]:
    """Render the room to H.264 MP4. Historical function name kept for bot compatibility."""
    if state not in DURATIONS:
        state = "idle"

    stage_key = growth_stage(pet_level)["key"]
    key = _equipment_key(equipment, state, stage_key)
    path = CACHE / f"{key}_{state}.mp4"
    if path.exists() and path.stat().st_size > 0:
        return path, key

    pet_frames = _pet_frames(state, stage_key)
    # Preserve every frame supplied by the source GIF. The old fixed sequence only
    # showed frames 0..3 and silently dropped frames 4..5 from the new animations.
    rgba_frames = [_compose_frame(equipment, frame, state) for frame in pet_frames]

    # Пишем во временный файл и только затем атомарно подменяем итоговый. Два параллельных
    # рендера одного кадра больше не могут оставить в cache/rooms обрезанный MP4.
    tmp = path.with_name(f".{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp.mp4")
    try:
        _save_animation_mp4(rgba_frames, DURATIONS[state], tmp)
        if not tmp.exists() or tmp.stat().st_size <= 0:
            raise RuntimeError("renderer produced an empty MP4")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return path, key
