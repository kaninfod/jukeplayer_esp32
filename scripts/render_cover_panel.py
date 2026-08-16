#!/usr/bin/env python3
"""
Standalone cover panel renderer for the ILI9488 jukeplayer display.

Generates a 480x300 pixel panel (below the 32px status bar on a 480x320 screen)
containing album art, artist/album/title text, and a background derived from
the cover's dominant colors.

Usage:
    python scripts/render_cover_panel.py \
        --cover /path/to/cover.jpg \
        --artist "Artist Name" \
        --album "Album Name" \
        --title "Track Title" \
        --output panel.png
"""

import argparse
import colorsys
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont


PANEL_WIDTH = 480
PANEL_HEIGHT = 300
COVER_SIZE = 200  # Size of the square cover art on the panel
COVER_MARGIN = 24  # Left margin for the cover


def dominant_colors(image: Image.Image, k: int = 4) -> list[tuple[int, int, int]]:
    """Return the k dominant RGB colors of an image using median-cut quantization."""
    small = image.copy().convert("RGB").resize((80, 80), Image.Resampling.LANCZOS)
    quantized = small.quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()[: k * 3]
    return [tuple(palette[i : i + 3]) for i in range(0, k * 3, 3)]


def choose_text_color(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    """Choose black or white text depending on background luminance."""
    r, g, b = bg
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return (255, 255, 255) if luminance < 128 else (0, 0, 0)


def brighter(rgb: tuple[int, int, int], factor: float = 1.3) -> tuple[int, int, int]:
    return tuple(min(255, int(c * factor)) for c in rgb)


def darker(rgb: tuple[int, int, int], factor: float = 0.5) -> tuple[int, int, int]:
    return tuple(int(c * factor) for c in rgb)


def get_font(size: int):
    """Try to load a bundled font, fall back to PIL default."""
    candidates = [
        "jukeplayer/nanogui/fonts/GeistMono-Bold.ttf",
        "jukeplayer/nanogui/fonts/GeistMono-Regular.ttf",
        "jukeplayer/nanogui/fonts/MaterialSymbolsOutlined-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def render_cover(cover_path: str, size: int = 180) -> Image.Image:
    """Render a square cover image at the target size, cropping to center."""
    cover = Image.open(cover_path).convert("RGB")
    w, h = cover.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    cover_square = cover.crop((left, top, left + side, top + side))
    return cover_square.resize((size, size), Image.Resampling.LANCZOS)


def cover_to_rgb565_bytes(cover_path: str, size: int = 180) -> bytes:
    """Return a 180x180 RGB565 raw byte string suitable for direct device blit."""
    img = render_cover(cover_path, size).convert("RGB")
    pixels = list(img.getdata())
    packed = bytearray()
    for r, g, b in pixels:
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        packed.append((rgb565 >> 8) & 0xFF)
        packed.append(rgb565 & 0xFF)
    return bytes(packed)


def render_panel(cover_path: str, artist: str, album: str, title: str) -> Image.Image:
    """Render a 480x300 panel from a cover image and metadata."""
    cover = Image.open(cover_path).convert("RGB")

    # Crop cover to square and resize
    w, h = cover.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    cover_square = cover.crop((left, top, left + side, top + side))
    cover_thumb = cover_square.resize((COVER_SIZE, COVER_SIZE), Image.Resampling.LANCZOS)

    # Extract dominant colors and build moody background
    colors = dominant_colors(cover_square, k=4)
    bg_color = colors[0]
    accent = colors[1] if len(colors) > 1 else brighter(bg_color)

    # Create gradient/blurred background
    panel = Image.new("RGB", (PANEL_WIDTH, PANEL_HEIGHT), bg_color)
    draw = ImageDraw.Draw(panel)

    # Use a blurred, darkened version of the cover as full-panel backdrop
    backdrop = cover_square.resize((PANEL_WIDTH, PANEL_HEIGHT), Image.Resampling.LANCZOS)
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=20))
    backdrop = Image.blend(backdrop, Image.new("RGB", backdrop.size, bg_color), alpha=0.4)
    # Darken slightly
    enhancer = Image.new("RGB", backdrop.size, (0, 0, 0))
    backdrop = Image.blend(backdrop, enhancer, alpha=0.35)
    panel.paste(backdrop, (0, 0))

    # Place cover art on the left with a soft shadow
    cover_x = COVER_MARGIN
    cover_y = (PANEL_HEIGHT - COVER_SIZE) // 2
    shadow_offset = 6
    draw.rectangle(
        [cover_x + shadow_offset, cover_y + shadow_offset, cover_x + COVER_SIZE + shadow_offset, cover_y + COVER_SIZE + shadow_offset],
        fill=(0, 0, 0),
    )
    panel.paste(cover_thumb, (cover_x, cover_y))

    # Text area to the right of the cover
    text_x = cover_x + COVER_SIZE + 24
    text_w = PANEL_WIDTH - text_x - 24
    text_y_start = 36

    font_artist = get_font(28)
    font_album = get_font(20)
    font_title = get_font(24)

    text_color = choose_text_color(bg_color)

    # Draw labels
    def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        """Naive word wrap to max_width in pixels."""
        words = text.split()
        lines = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        if not lines:
            lines.append(text)
        return lines

    y = text_y_start
    line_gap = 8

    # Artist
    for line in wrap_text(artist, font_artist, text_w):
        draw.text((text_x, y), line, font=font_artist, fill=text_color)
        y += draw.textbbox((0, 0), line, font=font_artist)[3] + line_gap

    y += 12  # gap before album

    # Album
    for line in wrap_text(album, font_album, text_w):
        draw.text((text_x, y), line, font=font_album, fill=brighter(text_color, 0.9))
        y += draw.textbbox((0, 0), line, font=font_album)[3] + line_gap

    y += 18  # gap before title

    # Title
    for line in wrap_text(title, font_title, text_w):
        draw.text((text_x, y), line, font=font_title, fill=accent)
        y += draw.textbbox((0, 0), line, font=font_title)[3] + line_gap

    return panel


DEVICE_LUT_16 = [
    (255, 255, 255),  # 0 white (background)
    (0, 0, 0),        # 1
    (200, 0, 0),      # 2 red
    (0, 150, 0),      # 3
    (0, 0, 200),      # 4
    (150, 80, 0),     # 5 accent2
    (0, 200, 200),    # 6
    (128, 128, 128),  # 7
    (0, 100, 150),    # 8 accent
    (200, 100, 0),    # 9
    (0, 200, 0),      # 10
    (200, 0, 200),    # 11
    (255, 255, 0),    # 12
    (0, 255, 255),    # 13
    (192, 192, 192),  # 14
    (0, 0, 0),        # 15 black (text/border)
]


def simulate_device_16color(panel: Image.Image) -> Image.Image:
    """Quantize the panel to the 16 colors the current ESP32 LUT uses."""
    pal_img = Image.new("P", (1, 1))
    flat_palette = [c for rgb in DEVICE_LUT_16 for c in rgb] + [0] * (768 - len(DEVICE_LUT_16) * 3)
    pal_img.putpalette(flat_palette)
    return panel.convert("RGB").quantize(palette=pal_img, dither=Image.Dither.NONE).convert("RGB")


def simulate_device_256color(panel: Image.Image) -> Image.Image:
    """Quantize the panel to a 256-color adaptive palette (GS8 simulation)."""
    adaptive = panel.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    return adaptive.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="Render jukeplayer cover art or panel")
    parser.add_argument("--cover", required=True, help="Path to cover image")
    parser.add_argument("--artist", default="Unknown Artist")
    parser.add_argument("--album", default="Unknown Album")
    parser.add_argument("--title", default="Unknown Title")
    parser.add_argument("--output", default="panel.png")
    parser.add_argument(
        "--mode",
        choices=["panel", "cover-rgb565"],
        default="panel",
        help="Render a full 480x300 panel or just a 180x180 RGB565 raw cover",
    )
    parser.add_argument(
        "--simulate-device",
        choices=["none", "16color", "256color"],
        default="none",
        help="Simulate how the panel will look on the ESP32 display",
    )
    parser.add_argument(
        "--output-device",
        default=None,
        help="Separate output file for the device-simulated image (defaults to <output>-device.png)",
    )
    args = parser.parse_args()

    if args.mode == "cover-rgb565":
        raw = cover_to_rgb565_bytes(args.cover, size=180)
        output = args.output
        with open(output, "wb") as f:
            f.write(raw)
        print(f"Saved 180x180 RGB565 raw cover to {output} ({len(raw)} bytes)")
        return

    panel = render_panel(args.cover, args.artist, args.album, args.title)
    panel.save(args.output)
    print(f"Saved panel to {args.output} ({PANEL_WIDTH}x{PANEL_HEIGHT})")

    if args.simulate_device != "none":
        if args.simulate_device == "16color":
            device_panel = simulate_device_16color(panel)
        else:
            device_panel = simulate_device_256color(panel)
        device_output = args.output_device or args.output.replace(".png", "-device.png")
        device_panel.save(device_output)
        print(f"Saved {args.simulate_device} device simulation to {device_output}")


if __name__ == "__main__":
    main()
