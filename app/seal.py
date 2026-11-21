"""Find a coloured inspection seal on an uploaded page and save a crop of it."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.ocr_pipeline import load_images


def save_seal(source: Path, dest: Path) -> bool:
    try:
        images = load_images(source.read_bytes(), source.name)
    except (OSError, ValueError):
        return False
    for image in images:
        image = _stamp_at_bottom(image)
        crop = _ink_extract(image) or _seal_crop(image)
        if crop is None:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest, format="PNG")
        return True
    return False


def _stamp_at_bottom(image: Image.Image) -> Image.Image:
    """Inspection stamps sit at the bottom. A stamp in the top half means the photo is upside down."""
    found = _stamp_box(image)
    if found is None:
        return image
    _unused, box = found
    center_y = (box[1] + box[3]) / 2
    if center_y < image.height * 0.5:
        return image.rotate(180, expand=True)
    return image


def _stamp_pixel(red: int, green: int, blue: int) -> bool:
    if red + green + blue > 680:
        return False
    blue_ink = blue > 85 and blue - red > 28 and blue - green > 12
    red_ink = red > 145 and red - green > 45 and red - blue > 45
    return blue_ink or red_ink


def _ink_extract(image: Image.Image) -> Image.Image | None:
    """Keep the blue or red signature and stamp. Leave out the form lines and printed words."""
    found = _stamp_box(image)
    if found is None:
        return None
    image, (left, top, right, bottom) = found
    width, height = image.size
    stamp_w = max(1, right - left)
    stamp_h = max(1, bottom - top)
    region = (
        max(0, left - int(stamp_w * 1.8)),
        max(0, top - int(stamp_h * 0.35)),
        min(width, right + int(stamp_w * 0.25)),
        min(height, bottom + int(stamp_h * 0.2)),
    )
    crop = image.crop(region).convert("RGB")
    extracted = Image.new("RGBA", crop.size, (255, 255, 255, 0))
    source = crop.load()
    target = extracted.load()
    kept = 0
    for y in range(crop.height):
        for x in range(crop.width):
            red, green, blue = source[x, y]
            if _stamp_pixel(red, green, blue):
                target[x, y] = (red, green, blue, 255)
                kept += 1
    if kept < 40:
        return None
    bounds = extracted.getbbox()
    if bounds is None:
        return None
    return extracted.crop(bounds)


def _seal_crop(image: Image.Image) -> Image.Image | None:
    found = _stamp_box(image)
    if found is None:
        return None
    image, box = found
    crop = image.crop(box)
    if crop.width < 24 or crop.height < 24:
        return None
    return crop


def _stamp_box(image: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    image = image.convert("RGB")
    width, height = image.size
    sample_w = max(80, width // 4)
    sample_h = max(80, height // 4)
    small = image.resize((sample_w, sample_h), Image.Resampling.BILINEAR)
    pixels = list(small.getdata())
    mask = [_stamp_pixel(*pixel) for pixel in pixels]
    window = max(14, int(sample_w * 0.16))
    step = max(2, window // 5)
    best = None
    best_score = 0.0
    for top in range(0, sample_h - window, step):
        for left in range(0, sample_w - window, step):
            hits = 0
            checked = 0
            for y in range(top, top + window, 2):
                row = y * sample_w
                for x in range(left, left + window, 2):
                    checked += 1
                    if mask[row + x]:
                        hits += 1
            if not checked:
                continue
            density = hits / checked
            lower = 1.35 if top > sample_h * 0.5 else 1.0
            score = density * lower
            if score > best_score:
                best_score = score
                best = (left, top, density)
    if best is None or best[2] < 0.12:
        return None
    left, top, _density = best
    center_x = left + window / 2
    center_y = top + window / 2
    half = window * 0.95
    ink = [
        (x, y)
        for y in range(max(0, int(center_y - half)), min(sample_h, int(center_y + half)))
        for x in range(max(0, int(center_x - half)), min(sample_w, int(center_x + half)))
        if mask[y * sample_w + x]
    ]
    if len(ink) < 30:
        return None
    min_x = min(point[0] for point in ink)
    max_x = max(point[0] for point in ink)
    min_y = min(point[1] for point in ink)
    max_y = max(point[1] for point in ink)
    span = max(max_x - min_x, max_y - min_y)
    if span < window * 0.35 or span > sample_w * 0.45:
        return None
    scale_x = width / sample_w
    scale_y = height / sample_h
    pad = int(max(span * scale_x, span * scale_y) * 0.08)
    box = (
        max(0, int(min_x * scale_x) - pad),
        max(0, int(min_y * scale_y) - pad),
        min(width, int((max_x + 1) * scale_x) + pad),
        min(height, int((max_y + 1) * scale_y) + pad),
    )
    return image, box


def _signature_band(image: Image.Image) -> Image.Image | None:
    """Crop the Inspected By signature, seal, Approved By, and Comments block."""
    found = _stamp_box(image)
    if found is None:
        return None
    image, (stamp_left, stamp_top, stamp_right, stamp_bottom) = found
    width, height = image.size
    gray = image.convert("L")
    pixels = gray.load()
    y0 = max(0, stamp_top - 80)
    y1 = min(height - 1, stamp_bottom + 30)
    raw_lines = []
    for y in range(y0, y1 + 1):
        xs = [x for x in range(0, width, 2) if pixels[x, y] < 90]
        if len(xs) > 40 and max(xs) - min(xs) > width * 0.45:
            raw_lines.append((y, min(xs), max(xs)))
    lines = []
    for y, left, right in raw_lines:
        if lines and y - lines[-1]["ys"][-1] <= 4:
            lines[-1]["ys"].append(y)
            lines[-1]["left"] = min(lines[-1]["left"], left)
            lines[-1]["right"] = max(lines[-1]["right"], right)
        else:
            lines.append({"ys": [y], "left": left, "right": right})
    full = []
    for line in lines:
        y = line["ys"][len(line["ys"]) // 2]
        if line["right"] - line["left"] > width * 0.55:
            full.append((y, line["left"], line["right"]))
    rules = [item for item in full if item[0] >= stamp_top - 6]
    if not rules:
        return None
    rule_y = rules[0][0]
    pad = 8
    box = (
        0,
        max(0, min(stamp_top, rule_y) - pad),
        width,
        min(height, stamp_bottom + 36),
    )
    if box[2] - box[0] < width * 0.35 or box[3] - box[1] > height * 0.28:
        return None
    crop = image.crop(box).convert("RGB")
    rule_in_crop = rule_y - box[1]
    crop_pixels = crop.load()
    for y in range(0, max(0, rule_in_crop - 1)):
        for x in range(crop.width):
            page_x = box[0] + x
            red, green, blue = crop_pixels[x, y]
            inside_stamp = stamp_left - 10 <= page_x <= stamp_right + 10 and _stamp_pixel(red, green, blue)
            if not inside_stamp:
                crop_pixels[x, y] = (255, 255, 255)
    if crop.width < 80 or crop.height < 40:
        return None
    return crop


def draw_test_stamp(size: int = 900) -> Image.Image:
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    draw.line((40, 80, 700, 90), fill=(20, 80, 180), width=3)
    draw.ellipse((620, 640, 860, 880), outline=(20, 70, 190), width=10)
    draw.ellipse((660, 680, 820, 840), outline=(20, 70, 190), width=6)
    draw.text((690, 740), "QC", fill=(20, 70, 190))
    return image
