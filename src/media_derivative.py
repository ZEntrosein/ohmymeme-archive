"""为预览、普通复制和拖拽生成可再生的日常副本。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    from PIL import Image, ImageOps, ImageSequence

    HAS_PIL = True
except ImportError:
    Image = ImageOps = ImageSequence = None
    HAS_PIL = False

DAILY_COPY_VERSION = 1
DAILY_COPY_MAX_SIDE = 1600
DAILY_COPY_TARGET_BYTES = 8 * 1024 * 1024
STATIC_WEBP_QUALITIES = (84, 78, 72, 66, 60)
ANIMATED_WEBP_QUALITY = 80
LARGE_JPEG_MAX_PIXELS = 300_000_000
_JPEG_HEADER_SCAN_LIMIT = 4 * 1024 * 1024
_PIL_PIXEL_LIMIT_LOCK = threading.Lock()
_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def _fit_size(size, max_side=DAILY_COPY_MAX_SIDE):
    width, height = size
    longest = max(width, height)
    if longest <= max_side:
        return width, height
    ratio = max_side / longest
    return max(1, round(width * ratio)), max(1, round(height * ratio))


def _mime_for_suffix(suffix: str) -> str:
    return {
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
    }.get(suffix.lower(), "application/octet-stream")


def _suffix_for_format(image_format: str, fallback: str) -> str:
    return {
        "GIF": ".gif",
        "WEBP": ".webp",
        "PNG": ".png",
        "JPEG": ".jpg",
        "BMP": ".bmp",
    }.get(image_format.upper(), fallback.lower() or ".img")


def _safe_asset_stem(value: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z_-]+", "_", value or "").strip("_-")
    return (stem or "image")[:128].lower()


def _jpeg_dimensions(path: Path):
    """有界读取 JPEG SOF 尺寸，不触发像素解码。"""
    try:
        with open(path, "rb") as source:
            if source.read(2) != b"\xff\xd8":
                return None
            while source.tell() < _JPEG_HEADER_SCAN_LIMIT:
                byte = source.read(1)
                while byte and byte != b"\xff":
                    byte = source.read(1)
                if not byte:
                    return None
                marker_byte = source.read(1)
                while marker_byte == b"\xff":
                    marker_byte = source.read(1)
                if not marker_byte:
                    return None
                marker = marker_byte[0]
                if marker in (0x00, 0x01) or 0xD0 <= marker <= 0xD9:
                    continue
                length_data = source.read(2)
                if len(length_data) != 2:
                    return None
                segment_length = int.from_bytes(length_data, "big")
                if segment_length < 2:
                    return None
                if marker in _JPEG_SOF_MARKERS:
                    frame_header = source.read(5)
                    if len(frame_header) != 5:
                        return None
                    height = int.from_bytes(frame_header[1:3], "big")
                    width = int.from_bytes(frame_header[3:5], "big")
                    if width <= 0 or height <= 0:
                        return None
                    return width, height
                source.seek(segment_length - 2, os.SEEK_CUR)
    except OSError:
        return None
    return None


@contextmanager
def _open_source_image(source: Path):
    """打开原图；超大 JPEG 预检后先由解码器降采样。"""
    jpeg_size = _jpeg_dimensions(source)
    if jpeg_size is None:
        with Image.open(source) as image:
            yield image, image.size
        return

    pixel_count = jpeg_size[0] * jpeg_size[1]
    if pixel_count > LARGE_JPEG_MAX_PIXELS:
        raise ValueError(f"JPEG 像素数过大: {jpeg_size[0]} × {jpeg_size[1]}")
    with _PIL_PIXEL_LIMIT_LOCK:
        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = LARGE_JPEG_MAX_PIXELS
        try:
            image = Image.open(source)
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit
    try:
        if max(jpeg_size) > DAILY_COPY_MAX_SIDE:
            image.draft("RGB", _fit_size(jpeg_size))
        yield image, jpeg_size
    finally:
        image.close()


def _save_static_webp(image: Image.Image, path: str):
    image = ImageOps.exif_transpose(image)
    target_size = _fit_size(image.size)
    if image.size != target_size:
        image = image.resize(target_size, Image.Resampling.LANCZOS)
    has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
    image = image.convert("RGBA" if has_alpha else "RGB")
    for quality in STATIC_WEBP_QUALITIES:
        image.save(
            path,
            format="WEBP",
            quality=quality,
            method=6,
            exact=has_alpha,
        )
        if os.path.getsize(path) <= DAILY_COPY_TARGET_BYTES:
            break


def _save_gif(image: Image.Image, path: str):
    frames = []
    durations = []
    for frame in ImageSequence.Iterator(image):
        rgba = frame.convert("RGBA")
        target_size = _fit_size(rgba.size)
        if rgba.size != target_size:
            rgba = rgba.resize(target_size, Image.Resampling.LANCZOS)
        frames.append(rgba)
        durations.append(max(20, int(frame.info.get("duration", 100) or 100)))
    if not frames:
        raise ValueError("GIF 中没有可用帧")
    frames[0].save(
        path,
        format="GIF",
        save_all=len(frames) > 1,
        append_images=frames[1:],
        duration=durations if len(frames) > 1 else durations[0],
        loop=int(image.info.get("loop", 0) or 0),
        disposal=2,
        optimize=True,
    )


def _save_animated_webp(image: Image.Image, path: str):
    frames = []
    durations = []
    for frame in ImageSequence.Iterator(image):
        rgba = frame.convert("RGBA")
        target_size = _fit_size(rgba.size)
        if rgba.size != target_size:
            rgba = rgba.resize(target_size, Image.Resampling.LANCZOS)
        frames.append(rgba)
        durations.append(max(20, int(frame.info.get("duration", 100) or 100)))
    if not frames:
        raise ValueError("WebP 中没有可用帧")
    frames[0].save(
        path,
        format="WEBP",
        save_all=len(frames) > 1,
        append_images=frames[1:],
        duration=durations,
        loop=int(image.info.get("loop", 0) or 0),
        quality=ANIMATED_WEBP_QUALITY,
        method=6,
    )


def _validate_image(path: Path, expected_frames: int) -> tuple:
    with Image.open(path) as image:
        image.load()
        frames = int(getattr(image, "n_frames", 1) or 1)
        if expected_frames > 1 and frames <= 1:
            raise ValueError("日常副本丢失动画帧")
        return image.size, frames


def create_daily_copy(source, target_dir, file_hash: str) -> dict:
    """生成日常副本并原子落盘。

    静态图统一使用 WebP；GIF 保持 GIF；动画 WebP 保持 WebP。若重新编码
    后比原件更大，则仍在日常副本目录保存一份原格式字节副本。
    """
    if not HAS_PIL:
        raise RuntimeError("缺少 Pillow，无法生成日常副本")
    source = Path(source)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    with _open_source_image(source) as (image, source_size):
        source_format = (image.format or source.suffix.lstrip(".")).upper()
        frame_count = int(getattr(image, "n_frames", 1) or 1)
        animated = frame_count > 1
        if source_format == "GIF":
            suffix = ".gif"
            saver = _save_gif
        elif source_format == "WEBP" and animated:
            suffix = ".webp"
            saver = _save_animated_webp
        else:
            suffix = ".webp"
            saver = _save_static_webp

        stem = _safe_asset_stem(file_hash or source.stem)
        encoded_name = f"{stem}_daily_v{DAILY_COPY_VERSION}{suffix}"
        encoded_path = target_dir / encoded_name
        if encoded_path.exists():
            try:
                size, _ = _validate_image(encoded_path, frame_count)
                return {
                    "filename": encoded_name,
                    "file_size": encoded_path.stat().st_size,
                    "mime_type": _mime_for_suffix(suffix),
                    "width": size[0],
                    "height": size[1],
                    "profile": DAILY_COPY_VERSION,
                }
            except Exception:
                try:
                    encoded_path.unlink()
                except OSError:
                    pass

        fd, tmp_name = tempfile.mkstemp(dir=str(target_dir), suffix=suffix + ".tmp")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            saver(image, str(tmp_path))
            final_path = encoded_path
            size, _ = _validate_image(tmp_path, frame_count)
            # 小型、已高度压缩的源文件不因重新编码变大；仍复制到日常副本目录，
            # 保持原件与日常使用文件在物理上隔离。
            can_reuse_original = (
                max(source_size) <= DAILY_COPY_MAX_SIDE
                and source.stat().st_size <= DAILY_COPY_TARGET_BYTES
            )
            if can_reuse_original and tmp_path.stat().st_size >= source.stat().st_size:
                original_suffix = _suffix_for_format(source_format, suffix)
                copied_name = f"{stem}_daily_v{DAILY_COPY_VERSION}{original_suffix}"
                final_path = target_dir / copied_name
                shutil.copy2(source, tmp_path)
                size, _ = _validate_image(tmp_path, frame_count)
            os.replace(tmp_path, final_path)
            return {
                "filename": final_path.name,
                "file_size": final_path.stat().st_size,
                "mime_type": _mime_for_suffix(final_path.suffix),
                "width": size[0],
                "height": size[1],
                "profile": DAILY_COPY_VERSION,
            }
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
