"""Non-destructive preparation and physical placement of act signature images."""
from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageMath, ImageOps, UnidentifiedImageError

SIGNATURE_WIDTH_MM = 38
SIGNATURE_HEIGHT_MM = 26
SIGNATURE_LINE_MM = 8


def normalize_image_settings(settings=None, *, kind):
    if kind not in {"signature", "stamp"}:
        raise ValueError("Неизвестный вид изображения.")
    settings = settings or {}
    if not isinstance(settings, dict):
        raise ValueError("Не удалось прочитать настройки изображения.")

    def number(key, default, lower, upper):
        try:
            value = float(settings.get(key, default))
        except (ValueError, TypeError) as exc:
            raise ValueError("Проверьте размер изображения.") from exc
        if not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError("Размер или смещение изображения вне допустимых границ.")
        return value

    crop = settings.get("crop", [0, 0, 1, 1])
    try:
        left, top, right, bottom = map(float, crop)
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ValueError("Выберите область изображения заново.") from exc
    rotation = settings.get("rotation", 0)
    if rotation not in (0, 90, 180, 270):
        raise ValueError("Поворот должен быть кратен 90°.")
    result = dict(crop=[left, top, right, bottom], rotation=rotation,
                  remove_background=bool(settings.get("remove_background", True)))
    if kind == "stamp":
        result["size_mm"] = number("size_mm", 40, 10, 50)
    else:
        result.update(width_mm=number("width_mm", 38, 5, 38),
                      height_mm=number("height_mm", 13, 3, 13),
                      offset_mm=number("offset_mm", 0, -5, 5))
    return result


def decode_act_image(content):
    if len(content) > 5 * 1024 * 1024:
        raise ValueError("Изображение должно занимать не больше 5 МБ.")
    if not (content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8\xff")):
        raise ValueError("Выберите изображение PNG или JPEG.")
    try:
        with Image.open(BytesIO(content)) as source:
            if max(source.size) > 8000 or source.width * source.height > 32_000_000:
                raise ValueError("Допустимо до 8000 пикселей на сторону и 32 млн пикселей на изображение.")
            return ImageOps.exif_transpose(source).convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Не удалось прочитать изображение PNG / JPEG.") from exc


def load_act_image(path):
    file = Path(path)
    if file.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Изображение должно занимать не больше 5 МБ.")
    content = file.read_bytes()
    decode_act_image(content)
    return content, file.name


def oriented_image(image, rotation):
    # Crop coordinates are relative to the EXIF-corrected, user-rotated source.
    return image.rotate(-rotation, expand=True) if rotation else image.copy()


def _ink_bounds(image):
    white = Image.new("RGBA", image.size, "white")
    red, green, blue = Image.alpha_composite(white, image).convert("RGB").split()
    darkness = ImageOps.invert(ImageChops.darker(ImageChops.darker(red, green), blue))
    return darkness.point(lambda x: 255 if x > 8 else 0).getbbox()


def prepare_act_image(content, settings=None, *, kind):
    settings = normalize_image_settings(settings, kind=kind)
    image = oriented_image(decode_act_image(content), settings["rotation"])
    left, top, right, bottom = settings["crop"]
    box = (int(left * image.width), int(top * image.height),
           min(image.width, math.ceil(right * image.width)),
           min(image.height, math.ceil(bottom * image.height)))
    image = image.crop(box)
    bounds = _ink_bounds(image)
    if not bounds:
        raise ValueError("В выбранной области нет видимой подписи или печати.")
    image = image.crop(bounds)
    # Already transparent PNGs retain their original colours and alpha channel.
    # For opaque scans unmatting white keeps antialiased ink, without a white tile
    # covering the signature line. All transformations operate on a copy.
    if settings["remove_background"] and image.getextrema()[3] == (255, 255):
        red, green, blue, _ = image.split()
        alpha = ImageOps.invert(ImageChops.darker(ImageChops.darker(red, green), blue))
        alpha = alpha.point(lambda x: 0 if x <= 8 else x)
        safe = ImageChops.lighter(alpha, Image.new("L", image.size, 1))
        channels = [ImageMath.lambda_eval(
            lambda v: 255 - (255 - v["c"]) * 255 / v["a"],
            c=channel.convert("I"), a=safe.convert("I")).convert("L") for channel in (red, green, blue)]
        image = Image.merge("RGBA", (*channels, alpha))
    return image


def image_png(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def fitted_size(size, width_mm, height_mm):
    factor = min(width_mm / size[0], height_mm / size[1])
    return size[0] * factor, size[1] * factor


def signature_placement(size, settings=None):
    """x, y, width, height in mm, from bottom-left of the 38 x 26 mm field."""
    settings = normalize_image_settings(settings, kind="signature")
    width, height = fitted_size(size, settings["width_mm"], settings["height_mm"])
    return ((SIGNATURE_WIDTH_MM - width) / 2,
            SIGNATURE_LINE_MM - height * .2 + settings["offset_mm"], width, height)
