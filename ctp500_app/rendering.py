from __future__ import annotations

from collections.abc import Iterator

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import PRINTER_WIDTH, RASTER_BAND_HEIGHT
from .models import GearItem, PLACEHOLDERS, PrintDocument


TAG_FIELDS: dict[str, tuple[str, ...]] = {
    "Weapons": ("Element", "Rank", "Range", "Form Factor", "Clip", "Ammo", "Special Notes", "Price"),
    "Gene Treatments": ("Effect", "Corruption", "Price"),
    "Apparel": ("Coverage", "Effects", "Rank", "Description", "Special Notes", "Price"),
    "Consumables/Gear": ("Type", "Effect", "Rank", "Description", "Special Notes", "Price"),
    "Bionics": ("Type", "Effects", "Rank", "Description", "Special Notes", "Price", "Cap"),
    "Vehicles": ("Type", "Speed", "Crew", "Range/Fuel", "Fuel", "Toughness", "Description", "Price"),
}
MAX_RENDER_TEXT_CHARS = 64 * 1024
MAX_RENDER_HEIGHT = 32_768
MAX_RENDER_WIDTH = 4_096
MAX_RENDER_PIXELS = 64 * 1024 * 1024


def _validate_render_bounds(text: str, height: int | None = None, width: int | None = None) -> None:
    if len(text) > MAX_RENDER_TEXT_CHARS:
        raise ValueError("Printable text exceeds the allowed size")
    if width is not None and not 1 <= width <= MAX_RENDER_WIDTH:
        raise ValueError("Printable output exceeds the allowed width")
    if height is not None and height > MAX_RENDER_HEIGHT:
        raise ValueError("Printable output exceeds the allowed height")
    if width is not None and height is not None and width * height > MAX_RENDER_PIXELS:
        raise ValueError("Printable output exceeds the allowed pixel count")


def _present(value: str) -> bool:
    return value.strip().casefold() not in PLACEHOLDERS


def tag_text(item: GearItem) -> str:
    fields = item.field_map
    lines = [item.name, item.category]
    if _present(item.manufacturer):
        lines.append(f"Manufacturer: {item.manufacturer}")
    for label in TAG_FIELDS.get(item.category, tuple(fields)):
        value = fields.get(label, "")
        if _present(value):
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _text_width(font: ImageFont.ImageFont, text: str) -> float:
    try:
        return font.getlength(text)
    except AttributeError:
        box = font.getbbox(text)
        return float(box[2] - box[0])


def _break_word(word: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if current and _text_width(font, candidate) > width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


def wrap_text(text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    output: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words: list[str] = []
        for word in paragraph.split():
            words.extend(_break_word(word, font, width))
        if not words:
            output.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if _text_width(font, candidate) <= width:
                line = candidate
            else:
                output.append(line)
                line = word
        output.append(line)
    return output


def _line_height(font: ImageFont.ImageFont) -> int:
    box = font.getbbox("Ag")
    return max(1, box[3] - box[1] + 5)


def render_tag(item: GearItem, width: int = PRINTER_WIDTH) -> Image.Image:
    margin = 12
    content_width = width - 2 * margin
    name_font = _font(30, bold=True)
    category_font = _font(18, bold=True)
    body_font = _font(19)
    sections: list[tuple[list[str], ImageFont.ImageFont, int]] = []

    text = tag_text(item)
    _validate_render_bounds(text, width=width)
    raw_lines = text.splitlines()
    for index, text in enumerate(raw_lines):
        font = name_font if index == 0 else category_font if index == 1 else body_font
        spacing = 8 if index < 2 else 4
        sections.append((wrap_text(text, font, content_width), font, spacing))

    height = margin
    for lines, font, spacing in sections:
        height += len(lines) * _line_height(font) + spacing
    height += 2 + 42
    _validate_render_bounds(text, height, width)

    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    y = margin
    for lines, font, spacing in sections:
        line_height = _line_height(font)
        for line in lines:
            draw.text((margin, y), line, font=font, fill=0)
            y += line_height
        y += spacing
    draw.line((margin, y + 5, width - margin, y + 5), fill=0, width=2)
    return image


def render_document(document: PrintDocument, width: int = PRINTER_WIDTH) -> Image.Image:
    """Render a compact multi-section receipt from saved text."""
    margin = 10
    content_width = width - margin * 2
    title_font = _font(25, bold=True)
    body_font = _font(17)
    _validate_render_bounds(document.text, width=width)
    sections: list[tuple[str, ImageFont.ImageFont, int]] = []
    for index, raw in enumerate(document.text.splitlines()):
        font = title_font if index == 0 else body_font
        sections.extend((line, font, 4 if not raw else 2) for line in wrap_text(raw, font, content_width))
    height = margin + sum(_line_height(font) + spacing for _line, font, spacing in sections) + 40
    _validate_render_bounds(document.text, height, width)
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    y = margin
    for line, font, spacing in sections:
        draw.text((margin, y), line, font=font, fill=0)
        y += _line_height(font) + spacing
    draw.line((margin, y + 4, width - margin, y + 4), fill=0, width=2)
    return image


def render_printable(item: GearItem | PrintDocument, width: int = PRINTER_WIDTH) -> Image.Image:
    return render_document(item, width) if isinstance(item, PrintDocument) else render_tag(item, width)


def prepare_bitmap(image: Image.Image, width: int = PRINTER_WIDTH) -> Image.Image:
    if image.width > width:
        height = max(1, round(image.height * width / image.width))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    if image.width < width:
        padded = Image.new("L", (width, image.height), 255)
        padded.paste(image.convert("L"), (0, 0))
        image = padded
    inverted = ImageOps.invert(image.convert("L"))
    return inverted.point(lambda pixel: 255 if pixel >= 128 else 0, mode="1")


def raster_bands(
    image: Image.Image,
    width: int = PRINTER_WIDTH,
    band_height: int = RASTER_BAND_HEIGHT,
) -> Iterator[bytes]:
    bitmap = prepare_bitmap(image, width)
    width_bytes = bitmap.width // 8
    for top in range(0, bitmap.height, band_height):
        band = bitmap.crop((0, top, bitmap.width, min(top + band_height, bitmap.height)))
        header = bytes(
            (
                0x1D, 0x76, 0x30, 0x00,
                width_bytes & 0xFF, (width_bytes >> 8) & 0xFF,
                band.height & 0xFF, (band.height >> 8) & 0xFF,
            )
        )
        yield header + band.tobytes()
