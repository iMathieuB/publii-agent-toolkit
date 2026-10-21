"""Responsive copies of a post image, made the way Publii makes them.

When someone adds an image to a post or a page in the Publii app, Publii also
writes a smaller copy for each width the theme declares, and the theme's
srcset points at those copies. An image copied in by the toolkit had none, so
the srcset pointed at missing files until someone ran Tools, "Regenerate
thumbnails" in Publii. add_media now makes them here instead.

What Publii 0.48.1 produced on its default theme for a 2752 x 1536 JPEG
(2026-10-21), and what this module reproduces:

- one file per size, ``responsive/<stem>-<size><ext>`` next to the image, for
  each entry of the theme's config.json ``files.responsiveImages``:
  ``featuredImages.dimensions`` for a featured image, ``contentImages`` for an
  image in the text;
- the width given, the height rounded from the image's ratio (1024 -> 572);
- JPEG at site.config.json ``advanced.imagesQuality`` (60 by default).

A size is never enlarged: a smaller image is written at its own width under
the size's name, so every file the srcset names exists. Sizes with a fixed
height (a crop) are left to Publii and reported.

Pillow is optional: ``pip install "publii-agent-toolkit[images]"``. Without
it, nothing is made and add_media says so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

RESIZABLE = (".jpg", ".jpeg", ".png", ".webp")


def pillow_available() -> bool:
    try:
        import PIL.Image  # noqa: F401
    except ImportError:
        return False
    return True


def variant_widths(theme_manifest: dict, featured: bool) -> tuple[dict[str, int], list[str]]:
    """Widths to make, by size name, and the sizes left to Publii (fixed height)."""
    responsive = ((theme_manifest.get("files") or {}).get("responsiveImages") or {})
    group = responsive.get("featuredImages" if featured else "contentImages") or {}
    dimensions = group.get("dimensions") or {}
    widths: dict[str, int] = {}
    skipped: list[str] = []
    if not isinstance(dimensions, dict):
        return widths, skipped
    for size, spec in dimensions.items():
        if not isinstance(spec, dict):
            continue
        width, height = spec.get("width"), spec.get("height", "auto")
        if isinstance(width, int) and width > 0 and height == "auto":
            widths[str(size)] = width
        else:
            skipped.append(str(size))
    return widths, skipped


def make_variants(source: Path, out_dir: Path, stem: str, ext: str,
                  widths: dict[str, int], quality: int) -> list[tuple[str, Path, Any]]:
    """Write one copy per size into out_dir. Returns (size, path, (width, height))."""
    from PIL import Image, ImageOps

    quality = max(1, min(100, int(quality)))
    made = []
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        width, height = image.size
        for size, wanted in widths.items():
            w = min(wanted, width)
            h = max(1, int(height * w / width + 0.5))
            copy = image.resize((w, h), Image.LANCZOS) if (w, h) != (width, height) else image.copy()
            path = out_dir / f"{stem}-{size}{ext}"
            _save(copy, path, ext, quality)
            made.append((size, path, (w, h)))
    return made


def _save(image, path: Path, ext: str, quality: int) -> None:
    ext = ext.lower()
    if ext in (".jpg", ".jpeg"):
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(path, "JPEG", quality=quality, optimize=True)
    elif ext == ".webp":
        image.save(path, "WEBP", quality=quality)
    else:
        image.save(path, "PNG", optimize=True)
