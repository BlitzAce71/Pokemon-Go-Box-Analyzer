from __future__ import annotations

import math
from typing import Iterator

from PIL import Image

from .config import GridConfig, Rect


def load_image(path: str) -> Image.Image:
    img = Image.open(path)
    if "A" in img.getbands():
        # Composite transparent assets on white so reference icons match white UI backgrounds.
        base = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(base, img.convert("RGBA")).convert("RGB")
    else:
        img = img.convert("RGB")
    return img


def crop_rect(image: Image.Image, rect: Rect) -> Image.Image:
    w, h = image.size
    x0 = max(0, min(w - 1, int(round(rect.x * w))))
    y0 = max(0, min(h - 1, int(round(rect.y * h))))
    x1 = max(x0 + 1, min(w, int(round((rect.x + rect.w) * w))))
    y1 = max(y0 + 1, min(h, int(round((rect.y + rect.h) * h))))
    return image.crop((x0, y0, x1, y1))


def crop_nested_rect(image: Image.Image, outer: Rect, inner: Rect) -> Image.Image:
    outer_crop = crop_rect(image, outer)
    return crop_rect(outer_crop, inner)


def iter_grid_cells(image: Image.Image, grid: GridConfig) -> Iterator[tuple[int, Rect, Image.Image]]:
    slot_index = 0
    for row in range(grid.rows):
        for col in range(grid.cols):
            x = grid.start_x + (col * (grid.cell_w + grid.gap_x))
            y = grid.start_y + (row * (grid.cell_h + grid.gap_y))
            cell_rect = Rect(x=x, y=y, w=grid.cell_w, h=grid.cell_h)
            yield slot_index, cell_rect, crop_rect(image, cell_rect)
            slot_index += 1


def extract_foreground(
    image: Image.Image,
    skip_top_ratio: float = 0.0,
    white_threshold: int = 245,
    min_size: int = 8,
) -> Image.Image:
    img = image.convert("RGB")
    w, h = img.size

    y_start = int(h * max(0.0, min(0.9, skip_top_ratio)))
    work = img.crop((0, y_start, w, h)) if y_start > 0 else img
    ww, wh = work.size
    pixels = list(work.getdata())

    min_x = ww
    min_y = wh
    max_x = -1
    max_y = -1

    for idx, (r, g, b) in enumerate(pixels):
        if r < white_threshold or g < white_threshold or b < white_threshold:
            x = idx % ww
            y = idx // ww
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y

    if max_x < min_x or max_y < min_y:
        return work

    pad = 2
    x0 = max(0, min_x - pad)
    y0 = max(0, min_y - pad)
    x1 = min(ww, max_x + pad + 1)
    y1 = min(wh, max_y + pad + 1)

    if (x1 - x0) < min_size or (y1 - y0) < min_size:
        return work

    return work.crop((x0, y0, x1, y1))


def extract_sprite_component(
    image: Image.Image,
    top_ignore_ratio: float = 0.22,
    bottom_ignore_ratio: float = 0.12,
    white_threshold: int = 245,
) -> Image.Image:
    img = image.convert("RGB")
    w, h = img.size
    pixels = list(img.getdata())

    y0_limit = int(h * max(0.0, min(0.9, top_ignore_ratio)))
    y1_limit = int(h * (1.0 - max(0.0, min(0.5, bottom_ignore_ratio))))

    active = [False] * (w * h)
    for y in range(y0_limit, max(y0_limit + 1, y1_limit)):
        row = y * w
        for x in range(w):
            r, g, b = pixels[row + x]
            if r < white_threshold or g < white_threshold or b < white_threshold:
                active[row + x] = True

    visited = [False] * (w * h)
    best_bbox: tuple[int, int, int, int] | None = None
    best_score = 0.0

    for y in range(y0_limit, max(y0_limit + 1, y1_limit)):
        for x in range(w):
            idx = (y * w) + x
            if not active[idx] or visited[idx]:
                continue

            stack = [idx]
            visited[idx] = True

            min_x = x
            min_y = y
            max_x = x
            max_y = y
            count = 0
            sum_x = 0
            sum_y = 0

            while stack:
                cur = stack.pop()
                cx = cur % w
                cy = cur // w

                count += 1
                sum_x += cx
                sum_y += cy

                if cx < min_x:
                    min_x = cx
                if cy < min_y:
                    min_y = cy
                if cx > max_x:
                    max_x = cx
                if cy > max_y:
                    max_y = cy

                neighbors = [
                    (cx - 1, cy),
                    (cx + 1, cy),
                    (cx, cy - 1),
                    (cx, cy + 1),
                ]
                for nx, ny in neighbors:
                    if nx < 0 or nx >= w or ny < y0_limit or ny >= y1_limit:
                        continue
                    nidx = (ny * w) + nx
                    if active[nidx] and not visited[nidx]:
                        visited[nidx] = True
                        stack.append(nidx)

            if count < 80:
                continue

            bw = (max_x - min_x + 1)
            bh = (max_y - min_y + 1)
            if bw < 12 or bh < 12:
                continue

            cx = sum_x / count
            cy = sum_y / count

            x_dist = abs(cx - (w * 0.5)) / max(1.0, (w * 0.5))
            y_dist = abs(cy - (h * 0.58)) / max(1.0, (h * 0.58))
            center_weight = max(0.1, 1.0 - ((0.6 * x_dist) + (0.4 * y_dist)))
            score = float(count) * center_weight

            if score > best_score:
                best_score = score
                best_bbox = (min_x, min_y, max_x, max_y)

    if best_bbox is None:
        return extract_foreground(image, skip_top_ratio=0.35)

    min_x, min_y, max_x, max_y = best_bbox
    pad = 3
    x0 = max(0, min_x - pad)
    y0 = max(0, min_y - pad)
    x1 = min(w, max_x + pad + 1)
    y1 = min(h, max_y + pad + 1)

    return img.crop((x0, y0, x1, y1))


def dhash(image: Image.Image, hash_size: int = 8) -> int:
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.BILINEAR)
    pixels = list(gray.getdata())

    bits = 0
    for y in range(hash_size):
        for x in range(hash_size):
            left = pixels[y * (hash_size + 1) + x]
            right = pixels[y * (hash_size + 1) + x + 1]
            bits = (bits << 1) | (1 if right > left else 0)
    return bits


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def average_color(image: Image.Image) -> tuple[float, float, float]:
    small = image.resize((16, 16), Image.Resampling.BILINEAR)
    pixels = list(small.getdata())
    n = len(pixels)
    r = sum(p[0] for p in pixels) / n
    g = sum(p[1] for p in pixels) / n
    b = sum(p[2] for p in pixels) / n
    return (r, g, b)


def color_similarity(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dr = a[0] - b[0]
    dg = a[1] - b[1]
    db = a[2] - b[2]
    distance = math.sqrt((dr * dr) + (dg * dg) + (db * db))
    max_distance = math.sqrt(3 * (255.0 * 255.0))
    return max(0.0, 1.0 - (distance / max_distance))


def to_gray_vector(image: Image.Image, size: tuple[int, int] = (32, 32)) -> bytes:
    gray = image.convert("L").resize(size, Image.Resampling.BILINEAR)
    return bytes(gray.getdata())


def gray_vector_similarity(a: bytes | list[int], b: bytes | list[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    mae = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return max(0.0, 1.0 - (mae / 255.0))


def grayscale_similarity(a: Image.Image, b: Image.Image, size: tuple[int, int] = (28, 28)) -> float:
    aa = a.convert("L").resize(size, Image.Resampling.BILINEAR)
    bb = b.convert("L").resize(size, Image.Resampling.BILINEAR)
    pa = list(aa.getdata())
    pb = list(bb.getdata())
    mae = sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)
    return max(0.0, 1.0 - (mae / 255.0))

