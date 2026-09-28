"""
Card template definitions and preview renderer.

Matches the exact design from the Edward Paxton reference SVGs:
  - Front: dark bg, left avatar, right labeled contact fields, top-right QR, bottom watermark
  - Back: dark bg, top-left logo, large card image, text, shield at bottom-right

Each template defines fields and a render(fields) -> PIL Image (85.6×54mm @ 300 DPI).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Callable

import qrcode
import qrcode.constants
from PIL import Image, ImageDraw, ImageFont

# ── Card dimensions (ISO/IEC 7810 ID-1 @ 300 DPI) ──────────────────────────
CARD_W, CARD_H = 1011, 638  # 85.6mm × 54mm

# ── Design tokens (from SVG reference) ───────────────────────────────────────
BG = "#0D0D0D"
BG_CARD = "#141416"
BG_INSET = "#0A0A0A"
FG_0 = "#FEFEFE"
FG_1 = "#BDB9B6"
FG_2 = "#9D9D9D"
FG_3 = "#5C5957"
RED_500 = "#DA2A1C"
RED_400 = "#E8543F"
RED_300 = "#F17A6A"
LINE_1 = "#202023"
LINE_2 = "#2C2C30"
GREEN_400 = "#46C95F"
CYAN_400 = "#2FD3C9"
AMBER_400 = "#E9B23C"
WHITE = "#FFFFFF"


@dataclass
class CardField:
    key: str
    label: str
    type: str = "text"  # text | email | tel | url | textarea | select
    placeholder: str = ""
    required: bool = False
    options: list[str] = field(default_factory=list)


@dataclass
class CardTemplate:
    id: str
    name: str
    description: str
    category: str
    thumbnail_color: str
    fields: list[CardField]
    render: Callable[..., Image.Image]


# ── Font helpers ─────────────────────────────────────────────────────────────

_FONT_REG = "static/assets/fonts/geist-latin.woff2"
_FONT_MONO = "static/assets/fonts/geist-mono-latin.woff2"
_font_cache: dict[str, ImageFont.FreeTypeFont] = {}


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = f"{path}:{size}"
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(path, size)
        except Exception:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return _load_font(_FONT_REG, size)


def _mono(size: int) -> ImageFont.FreeTypeFont:
    return _load_font(_FONT_MONO, size)


# ── Drawing primitives ───────────────────────────────────────────────────────

def _card_bg() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (CARD_W, CARD_H), BG)
    draw = ImageDraw.Draw(img)
    return img, draw


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _make_qr_image(data: str, size: int, fill=WHITE, back=BG_CARD) -> Image.Image:
    """Build a QR marking PNG that blends into the card background.

    Uses the card's dark background (not a stark white box) and light modules,
    so it reads as part of the design rather than a foreign sticker.
    """
    if not data:
        return _qr_placeholder(size, fill, back)
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=fill, back_color=back).convert("RGB")
    return qr_img.resize((size, size), Image.Resampling.NEAREST)


def _qr_placeholder(size: int, fill=FG_2, back=BG_CARD) -> Image.Image:
    """Generate a decorative QR-style placeholder grid for empty templates."""
    img = Image.new("RGB", (size, size), back)
    d = ImageDraw.Draw(img)
    n, m = 6, size / 6
    for r in range(n):
        for c in range(n):
            if (r + c) % 3 == 0:
                d.rounded_rectangle(
                    [c * m + m / 7, r * m + m / 7, (c + 1) * m - m / 7, (r + 1) * m - m / 7],
                    radius=max(2, m / 10),
                    fill=fill,
                )
    cb = size / 3
    for ox, oy in ((0, 0), (0, size - cb), (size - cb, 0)):
        d.rounded_rectangle(
            [ox + m / 4, oy + m / 4, ox + cb - m / 4, oy + cb - m / 4],
            radius=max(3, m / 3),
            fill=fill,
        )
    return img


def _draw_qr_panel(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    data: str,
    label: str = "",
    fill=WHITE,
    back=BG_CARD,
    label_color=FG_2,
):
    """Draw a rounded QR panel that occupies the (former) photo slot."""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle([x0, y0, x1, y1], radius=22, fill=back, outline=LINE_2, width=2)
    pad = 30
    label_h = 36 if label else 4
    qr_max = min(x1 - x0, y1 - y0) - pad * 2 - label_h
    qr_size = max(int(qr_max), 40)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - (10 if label else 0)
    qr_img = _make_qr_image(data, qr_size, fill=fill, back=back)
    img.paste(qr_img, (int(cx - qr_size / 2), int(cy - qr_size / 2)))
    if label:
        draw.text((cx, cy + qr_size / 2 + 16), label, fill=label_color, font=_mono(10), anchor="ma")


def _draw_contact_field(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, value: str, max_w: int = 380):
    """Draw a labeled contact field matching the SVG reference layout."""
    # Label (uppercase, small, gray)
    draw.text((x, y - 12), label.upper(), fill=FG_2, font=_mono(10))
    # Value (white, readable)
    # Truncate if too wide
    val = value
    while val and _text_size(draw, val, _font(16))[0] > max_w:
        val = val[:-1]
    if val != value:
        val += "…"
    draw.text((x, y + 4), val, fill=FG_0, font=_font(16))


def _draw_watermark(draw: ImageDraw.ImageDraw):
    """Draw 'UWITZ CARDS' watermark at bottom center."""
    draw.text((CARD_W // 2, CARD_H - 16), "UWITZ CARDS", fill=FG_3, font=_mono(11), anchor="mm")


def _draw_brand(draw: ImageDraw.ImageDraw, x: int, y: int, size: int = 17):
    """Small red logo mark + 'UWITZ' wordmark (left-aligned)."""
    s = size
    draw.rounded_rectangle([x, y, x + s, y + s], radius=int(s / 4), fill=RED_500)
    draw.text((x + s + 10, y + s * 0.7), "UWITZ", fill=FG_0, font=_font(size, True), anchor="lm")


def _draw_brand_rt(draw: ImageDraw.ImageDraw, x_right: int, y: int, size: int = 17):
    """Small red logo mark + 'UWITZ' wordmark (right-aligned to x_right)."""
    s = size
    t = "UWITZ"
    tw = _text_size(draw, t, _font(size, True))[0]
    x = x_right - tw - s - 10
    draw.rounded_rectangle([x, y, x + s, y + s], radius=int(s / 4), fill=RED_500)
    draw.text((x + s + 10, y + s * 0.7), t, fill=FG_0, font=_font(size, True), anchor="lm")


def _draw_serial(draw: ImageDraw.ImageDraw, text: str):
    """Small micro-print serial line at bottom-left, matching the reference."""
    draw.text((36, CARD_H - 30), text, fill=FG_3, font=_mono(9))


def _draw_asterisk(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, color=RED_500, width: int = 8):
    """Six-bar asterisk star mark (Uwitz brand glyph) centered at (cx, cy)."""
    for angle in (0, 60, 120):
        rad = math.radians(angle)
        dx, dy = math.cos(rad) * r, math.sin(rad) * r
        draw.line([(cx - dx, cy - dy), (cx + dx, cy + dy)], fill=color, width=width)


def _render_back(fields: dict, qr_data: str = "", card_id: str = "") -> Image.Image:
    """Card back face, matching the back.svg reference: logo top-left, large
    card image, contact text, shield at bottom-right."""
    img, draw = _card_bg()

    # ── Logo block, top-left ──
    draw.rounded_rectangle([30, 24, 214, 138], radius=16, fill=RED_500)
    draw.rounded_rectangle([38, 32, 206, 130], radius=10, fill=BG)
    _draw_asterisk(draw, 94, 81, 26, color=RED_500, width=6)
    draw.rounded_rectangle([112, 66, 148, 102], radius=8, fill=RED_500)
    draw.text((30, 150), "UWITZ", fill=FG_0, font=_font(26, bold=True))
    draw.text((30, 186), "DIGITAL BUSINESS CARD", fill=FG_2, font=_mono(9))

    # ── Large card image area ──
    img_box = (30, 220, 330, 500)
    draw.rounded_rectangle(img_box, radius=22, fill=BG_INSET, outline=LINE_2, width=2)

    # Style line (reduced card mock) + asterisk watermark inside
    cx, cy = (img_box[0] + img_box[2]) / 2, (img_box[1] + img_box[3]) / 2
    _draw_asterisk(draw, int(cx), int(cy), 56, color=RED_400, width=7)
    draw.rounded_rectangle([int(cx) - 90, int(cy) + 66, int(cx) + 90, int(cy) + 86], radius=10, fill=LINE_1)
    draw.text((int(cx), int(cy) + 116), "THE CARD IMAGE APPEARS HERE", fill=FG_3, font=_mono(8), anchor="mm")

    # ── Contact text block, right ──
    rx = 370
    ry = 96
    name = fields.get("name", "")
    if name:
        draw.text((rx, ry), name, fill=FG_0, font=_font(22, bold=True))
        ry += 40
    for label, key in (("EMAIL", "email"), ("TEL", "phone"), ("URL", "website")):
        val = fields.get(key, "")
        if val:
            draw.text((rx, ry), label, fill=FG_2, font=_mono(9))
            draw.text((rx, ry + 14), val, fill=FG_1, font=_font(14))
            ry += 46

    if qr_data:
        qr_box = (rx, CARD_H - 170, rx + 140, CARD_H - 30)
        _draw_qr_panel(img, draw, qr_box, qr_data, back=BG_CARD)

    # ── Shield at bottom-right ──
    sx, sy = CARD_W - 90, CARD_H - 90
    draw.rounded_rectangle([sx - 42, sy - 52, sx + 42, sy + 40], radius=14, fill=RED_500)
    _draw_asterisk(draw, sx, sy - 6, 22, color=WHITE, width=5)

    _draw_serial(draw, f"UWITZ {'/'.join([card_id, 'BACK']) if card_id else 'BACK'}")
    _draw_watermark(draw)
    return img


# ── Template renderers ───────────────────────────────────────────────────────

def _render_business(fields: dict, qr_data: str = "") -> Image.Image:
    """Business card matching the front.svg reference layout."""
    img, draw = _card_bg()

    # ── Left: QR panel (replaces the avatar slot) ──
    qr_box = (30, 40, 380, 420)
    _draw_qr_panel(img, draw, qr_box, qr_data, label="SCAN TO CONNECT", back=BG_INSET)

    # ── Brand wordmark, top center-right ──
    _draw_brand_rt(draw, CARD_W - 40, 46, size=16)

    # ── Right side: contact fields ──
    rx = 420
    ry = 60

    contact_items = [
        ("Email", fields.get("email", "")),
        ("Phone", fields.get("phone", "")),
        ("Website", fields.get("website", "")),
        ("Location", fields.get("address", "")),
    ]
    for label, value in contact_items:
        if value:
            _draw_contact_field(draw, rx, ry, label, value)
            ry += 62

    # ── Bottom-left: micro serial line ──
    _draw_serial(draw, "UWITZ DIGITAL BUSINESS CARD")

    # ── Bottom: watermark ──
    _draw_watermark(draw)

    return img


def _render_emergency(fields: dict, qr_data: str = "") -> Image.Image:
    """Emergency / ICE card with QR in the left slot."""
    img, draw = _card_bg()

    # ── Left: QR panel (replaces the avatar slot) ──
    qr_box = (30, 80, 290, 540)
    _draw_qr_panel(img, draw, qr_box, qr_data, label="MEDICAL QR", back=BG_INSET)

    # ── Medical cross icon, top of left column ──
    cross_x, cross_y = 160, 48
    draw.rectangle([cross_x - 4, cross_y - 16, cross_x + 4, cross_y + 16], fill=RED_400)
    draw.rectangle([cross_x - 16, cross_y - 4, cross_x + 16, cross_y + 4], fill=RED_400)

    # ── Right side: Emergency info ──
    rx = 340
    ry = 40

    # Title
    draw.text((rx, ry), "EMERGENCY CONTACT", fill=RED_300, font=_mono(11))
    ry += 30
    name = fields.get("name", "Name")
    draw.text((rx, ry), name, fill=FG_0, font=_font(20, bold=True))
    ry += 36

    # Separator
    draw.line([(rx, ry), (CARD_W - 30, ry)], fill=LINE_2, width=1)
    ry += 16

    info_items = [
        ("Blood Type", fields.get("blood_type", "")),
        ("Allergies", fields.get("allergies", "")),
        ("Conditions", fields.get("conditions", "")),
        ("Medications", fields.get("medications", "")),
        ("Phone", fields.get("phone", "")),
        ("ICE Contact", fields.get("emergency_contact", "")),
        ("ICE Phone", fields.get("emergency_phone", "")),
        ("Physician", fields.get("physician", "")),
        ("Insurance", fields.get("insurance", "")),
    ]
    for label, value in info_items:
        if value:
            draw.text((rx, ry), label.upper(), fill=FG_2, font=_mono(9))
            val = value[:30] + "…" if len(value) > 30 else value
            draw.text((rx, ry + 14), val, fill=FG_0, font=_font(14))
            ry += 38

    _draw_serial(draw, "UWITZ MEDICAL  ·  NFC")
    _draw_watermark(draw)
    return img


def _render_student(fields: dict, qr_data: str = "") -> Image.Image:
    """Student ID card with QR in the photo slot."""
    img, draw = _card_bg()

    # ── Header bar ──
    draw.rectangle([0, 0, CARD_W, 50], fill=BG_INSET)
    uni = fields.get("university", "UNIVERSITY")
    draw.text((30, 12), uni.upper(), fill=CYAN_400, font=_mono(14))
    draw.text((30, 32), "STUDENT IDENTIFICATION", fill=FG_2, font=_mono(9))

    # ── Left: QR panel (replaces the photo area) ──
    qr_box = (30, 70, 320, 470)
    _draw_qr_panel(img, draw, qr_box, qr_data, label="STUDENT QR", back=BG_INSET)

    # ── Right: Info ──
    sx = 360
    name = fields.get("name", "Student")
    draw.text((sx, 80), name, fill=FG_0, font=_font(20, bold=True))

    info_y = 120
    info_items = [
        ("STUDENT ID", fields.get("student_id", "")),
        ("FACULTY", fields.get("faculty", "")),
        ("PROGRAM", fields.get("program", "")),
        ("VALID UNTIL", fields.get("valid_until", "")),
    ]
    for label, val in info_items:
        if val:
            draw.text((sx, info_y), label, fill=FG_2, font=_mono(9))
            draw.text((sx, info_y + 14), val, fill=FG_0, font=_font(14))
            info_y += 42

    # ── Bottom bar ──
    draw.line([(30, CARD_H - 50), (CARD_W - 30, CARD_H - 50)], fill=LINE_1, width=1)
    if fields.get("email"):
        draw.text((30, CARD_H - 40), fields["email"], fill=FG_2, font=_mono(10))
    if fields.get("phone"):
        draw.text((CARD_W - 30, CARD_H - 40), fields["phone"], fill=FG_2, font=_mono(10), anchor="ra")

    _draw_watermark(draw)
    return img


def _render_event_badge(fields: dict, qr_data: str = "") -> Image.Image:
    """Event / conference badge with QR in the photo slot."""
    img, draw = _card_bg()

    # ── Event header ──
    draw.rectangle([0, 0, CARD_W, 60], fill=BG_INSET)
    event = fields.get("event_name", "EVENT")
    draw.text((CARD_W // 2, 14), event.upper(), fill=AMBER_400, font=_mono(14), anchor="mm")
    date = fields.get("event_date", "")
    if date:
        draw.text((CARD_W // 2, 38), date, fill=FG_2, font=_mono(10), anchor="mm")

    # ── Left: QR panel (replaces the avatar slot) ──
    qr_box = (30, 80, 290, 440)
    _draw_qr_panel(img, draw, qr_box, qr_data, label="BADGE QR", back=BG_INSET)

    # ── Name & title, right of QR panel ──
    name = fields.get("name", "Attendee")
    draw.text((330, 120), name, fill=FG_0, font=_font(22, bold=True))
    subtitle = " · ".join(filter(None, [fields.get("title", ""), fields.get("company", "")]))
    if subtitle:
        draw.text((330, 152), subtitle, fill=FG_1, font=_font(13))

    # ── Badge type pill ──
    badge_type = fields.get("badge_type", "Attendee").upper()
    badge_colors = {"VIP": RED_500, "SPEAKER": CYAN_400, "ATTENDEE": FG_2, "ORGANIZER": AMBER_400}
    badge_color = badge_colors.get(badge_type, FG_2)
    bw = max(len(badge_type) * 12, 100)
    bx = 330
    by = 200
    draw.rounded_rectangle([bx, by, bx + bw, by + 30], radius=15, fill=badge_color)
    draw.text((bx + bw // 2, by + 15), badge_type, fill=WHITE, font=_mono(11), anchor="mm")

    # ── Contact ──
    cy = 270
    for val in [fields.get("email", ""), fields.get("phone", ""), fields.get("website", "")]:
        if val:
            draw.text((330, cy), val[:40], fill=FG_1, font=_font(12))
            cy += 28

    _draw_serial(draw, "UWITZ EVENTS  ·  DIGITAL BADGE")
    _draw_watermark(draw)
    return img


def _render_real_estate(fields: dict, qr_data: str = "") -> Image.Image:
    """Real estate agent card with QR in the photo slot."""
    img, draw = _card_bg()

    # ── Green accent bar at top ──
    draw.rectangle([0, 0, CARD_W, 6], fill=GREEN_400)

    # ── Left: QR panel (replaces the agent photo area) ──
    qr_box = (30, 30, 280, 530)
    _draw_qr_panel(img, draw, qr_box, qr_data, label="AGENT QR", back=BG_INSET)

    # ── Right: Info ──
    rx = 320
    name = fields.get("name", "Agent")
    draw.text((rx, 40), name, fill=FG_0, font=_font(20, bold=True))
    draw.text((rx, 70), "Licensed Real Estate Agent", fill=GREEN_400, font=_mono(10))
    draw.text((rx, 90), fields.get("company", ""), fill=FG_1, font=_font(14))

    ry = 130
    info_items = [
        ("LICENSE", fields.get("license", "")),
        ("PHONE", fields.get("phone", "")),
        ("EMAIL", fields.get("email", "")),
        ("WEBSITE", fields.get("website", "")),
    ]
    for label, val in info_items:
        if val:
            draw.text((rx, ry), label, fill=FG_2, font=_mono(9))
            draw.text((rx, ry + 14), val, fill=FG_0, font=_font(13))
            ry += 40

    # ── Bottom bar ──
    draw.line([(30, CARD_H - 50), (CARD_W - 30, CARD_H - 50)], fill=LINE_1, width=1)
    specs = fields.get("specialties", "")
    if specs:
        draw.text((30, CARD_H - 40), "SPECIALIZING IN", fill=FG_3, font=_mono(8))
        draw.text((140, CARD_H - 40), specs[:50], fill=FG_1, font=_font(10))

    _draw_watermark(draw)
    return img


def _render_medical(fields: dict, qr_data: str = "") -> Image.Image:
    """Medical professional card with QR panel."""
    img, draw = _card_bg()

    # ── Cyan accent ──
    draw.rectangle([0, 0, CARD_W, 6], fill=CYAN_400)

    # ── Medical cross ──
    cx, cy = 50, 40
    draw.rectangle([cx - 4, cy - 16, cx + 4, cy + 16], fill=CYAN_400)
    draw.rectangle([cx - 16, cy - 4, cx + 16, cy + 4], fill=CYAN_400)
    draw.text((cx + 28, cy - 10), "MEDICAL PROFESSIONAL", fill=CYAN_400, font=_mono(10))

    # ── Name ──
    name = fields.get("name", "Dr. Name")
    draw.text((30, 80), name, fill=FG_0, font=_font(22, bold=True))
    draw.text((30, 112), fields.get("specialty", ""), fill=FG_1, font=_font(14))

    # ── Left column: info ──
    ry = 150
    info_items = [
        ("LICENSE", fields.get("license", "")),
        ("HOSPITAL", fields.get("hospital", "")),
        ("PHONE", fields.get("phone", "")),
        ("EMAIL", fields.get("email", "")),
        ("EMERGENCY", fields.get("emergency_line", "")),
    ]
    for label, val in info_items:
        if val:
            draw.text((40, ry), label, fill=FG_2, font=_mono(9))
            draw.text((130, ry), val[:28], fill=FG_0, font=_font(12))
            ry += 32

    # ── Right panel: Medical ID ──
    rx = CARD_W // 2 + 30
    draw.rounded_rectangle([rx, 30, CARD_W - 30, CARD_H - 200], radius=10, fill=BG_INSET, outline=CYAN_400, width=1)
    draw.text((rx + 16, 46), "MEDICAL ID", fill=CYAN_400, font=_mono(10))

    iy = 80
    med_items = [
        ("Blood Type", fields.get("blood_type", "")),
        ("Allergies", fields.get("allergies", "")),
        ("Conditions", fields.get("conditions", "")),
    ]
    for label, val in med_items:
        if val:
            draw.text((rx + 16, iy), label.upper(), fill=FG_2, font=_mono(8))
            draw.text((rx + 16, iy + 14), val[:20], fill=FG_0, font=_font(14))
            iy += 50

    # ── QR panel, bottom right ──
    qr_box = (rx, CARD_H - 170, CARD_W - 30, CARD_H - 30)
    _draw_qr_panel(img, draw, qr_box, qr_data, label="CONTACT QR", back=BG_CARD)

    _draw_watermark(draw)
    return img


def _render_minimal(fields: dict, qr_data: str = "") -> Image.Image:
    """Minimalist card with subtle QR panel."""
    img, draw = _card_bg()

    # ── Left accent line ──
    draw.rectangle([30, 30, 33, CARD_H - 30], fill=RED_500)

    # ── Name ──
    name = fields.get("name", "Name")
    draw.text((55, 60), name, fill=FG_0, font=_font(28, bold=True))

    # ── Tagline ──
    tagline = fields.get("tagline", "")
    if tagline:
        draw.text((55, 100), tagline, fill=RED_300, font=_mono(11))

    # ── Separator ──
    draw.line([(55, 140), (CARD_W - 30, 140)], fill=LINE_2, width=1)

    # ── Contact ──
    cy = 165
    for val in [fields.get("phone", ""), fields.get("email", ""), fields.get("website", ""), fields.get("tagline_2", "")]:
        if val:
            draw.text((55, cy), val, fill=FG_1, font=_font(14))
            cy += 36

    # ── Right: Large monogram ──
    mono = name[0].upper() if name else "N"
    draw.text((CARD_W - 130, CARD_H // 2), mono, fill=BG_INSET, font=_font(180, bold=True), anchor="mm")

    # ── QR panel, bottom-right (smaller to keep the minimal feel) ──
    qr_box = (CARD_W - 190, CARD_H - 150, CARD_W - 40, CARD_H - 50)
    _draw_qr_panel(img, draw, qr_box, qr_data, back=BG_CARD)

    _draw_watermark(draw)
    return img


def _render_taglink(fields: dict, qr_data: str = "") -> Image.Image:
    """TagLink dynamic redirect card with QR panel."""
    img, draw = _card_bg()

    # ── Center content ──
    draw.text((CARD_W // 2, 100), "TAGLINK", fill=RED_400, font=_mono(16), anchor="mm")
    draw.text((CARD_W // 2, 128), "DYNAMIC REDIRECT", fill=FG_2, font=_mono(10), anchor="mm")

    # ── URL pill ──
    url = fields.get("url", "https://example.com")
    url_w = min(_text_size(draw, url[:50], _mono(13))[0] + 40, CARD_W - 100)
    ux = (CARD_W - url_w) // 2
    draw.rounded_rectangle([ux, 170, ux + url_w, 215], radius=12, fill=BG_INSET, outline=LINE_2, width=1)
    draw.text((CARD_W // 2, 192), url[:50], fill=FG_1, font=_mono(13), anchor="mm")

    # ── Description ──
    desc = fields.get("description", "")
    if desc:
        draw.text((CARD_W // 2, 250), desc[:50], fill=FG_2, font=_font(13), anchor="mm")

    # ── QR panel, left side ──
    qr_box = (50, CARD_H - 180, 250, CARD_H - 40)
    _draw_qr_panel(img, draw, qr_box, qr_data, label="SCAN ME", back=BG_CARD)

    # ── Tap button ──
    draw.rounded_rectangle([CARD_W // 2 - 110, CARD_H - 95, CARD_W // 2 + 110, CARD_H - 55], radius=20, fill=RED_500)
    draw.text((CARD_W // 2, CARD_H - 75), "TAP TO REDIRECT", fill=WHITE, font=_mono(10), anchor="mm")

    _draw_watermark(draw)
    return img


def _render_corporate(fields: dict, qr_data: str = "") -> Image.Image:
    """Corporate / branded team card with QR in the photo slot."""
    img, draw = _card_bg()

    # ── Full-width header ──
    draw.rectangle([0, 0, CARD_W, 70], fill=BG_INSET)
    company = fields.get("company", "Company")
    draw.text((30, 15), company.upper(), fill=FG_0, font=_mono(16))
    draw.text((30, 40), "EMPLOYEE CARD", fill=FG_2, font=_mono(9))

    # ── Left: QR panel (replaces the photo area) ──
    qr_box = (30, 90, 300, 470)
    _draw_qr_panel(img, draw, qr_box, qr_data, label="EMPLOYEE QR", back=BG_INSET)

    # ── Right: info ──
    sx = 340
    name = fields.get("name", "Employee")
    draw.text((sx, 100), name, fill=FG_0, font=_font(20, bold=True))
    draw.text((sx, 132), fields.get("title", ""), fill=FG_1, font=_font(14))

    ry = 175
    info_items = [
        ("DEPARTMENT", fields.get("department", "")),
        ("EMPLOYEE ID", fields.get("employee_id", "")),
        ("EMAIL", fields.get("email", "")),
        ("PHONE", fields.get("phone", "")),
    ]
    for label, val in info_items:
        if val:
            draw.text((sx, ry), label, fill=FG_2, font=_mono(9))
            draw.text((sx, ry + 14), val[:28], fill=FG_0, font=_font(13))
            ry += 40

    _draw_serial(draw, "UWITZ CORPORATE  ·  NFC ENABLED")
    _draw_watermark(draw)
    return img


# ── Template registry ────────────────────────────────────────────────────────

TEMPLATES: list[CardTemplate] = [
    CardTemplate(
        id="emergency",
        name="Emergency Info Card",
        description="ICE card with blood type, allergies, medical conditions, and emergency contacts",
        category="emergency",
        thumbnail_color=RED_500,
        fields=[
            CardField("name", "Full Name", required=True, placeholder="Jane Smith"),
            CardField("blood_type", "Blood Type", options=["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]),
            CardField("allergies", "Allergies", placeholder="Penicillin, Peanuts"),
            CardField("conditions", "Medical Conditions", placeholder="Diabetes"),
            CardField("medications", "Current Medications", placeholder="Metformin 500mg"),
            CardField("phone", "Phone", type="tel", placeholder="+60 12 345 6789"),
            CardField("emergency_contact", "ICE Contact Name", placeholder="John Smith"),
            CardField("emergency_phone", "ICE Phone", type="tel", placeholder="+60 19 999 0000"),
            CardField("physician", "Physician", placeholder="Dr. Lee"),
            CardField("insurance", "Insurance ID", placeholder="INS-12345"),
            CardField("weight", "Weight", placeholder="70kg"),
            CardField("height", "Height", placeholder="175cm"),
        ],
        render=_render_emergency,
    ),
    CardTemplate(
        id="business",
        name="Business Card",
        description="Professional card with avatar, contact details, and QR code",
        category="business",
        thumbnail_color=FG_0,
        fields=[
            CardField("name", "Full Name", required=True, placeholder="Edward Paxton"),
            CardField("title", "Job Title", placeholder="Software Engineer"),
            CardField("company", "Company", placeholder="Uwitz"),
            CardField("email", "Email", type="email", required=True, placeholder="ed@uwitz.org"),
            CardField("phone", "Phone", type="tel", placeholder="+60 19 906 6530"),
            CardField("website", "Website", type="url", placeholder="https://uwitz.org"),
            CardField("address", "Address", placeholder="Kuala Lumpur, MY"),
        ],
        render=_render_business,
    ),
    CardTemplate(
        id="student",
        name="Student ID Card",
        description="University student identification card with photo area and faculty info",
        category="student",
        thumbnail_color=CYAN_400,
        fields=[
            CardField("name", "Full Name", required=True, placeholder="Alice Chen"),
            CardField("university", "University", required=True, placeholder="Universiti Malaya"),
            CardField("student_id", "Student ID Number", required=True, placeholder="UM-2024-001"),
            CardField("faculty", "Faculty", placeholder="Computer Science"),
            CardField("program", "Program", placeholder="Bachelor of IT"),
            CardField("valid_until", "Valid Until", placeholder="Dec 2026"),
            CardField("email", "Student Email", type="email", placeholder="alice@um.edu.my"),
            CardField("phone", "Phone", type="tel", placeholder="+60 12 345 6789"),
        ],
        render=_render_student,
    ),
    CardTemplate(
        id="event",
        name="Event Badge",
        description="Conference or event attendee badge with badge type and QR",
        category="events",
        thumbnail_color=AMBER_400,
        fields=[
            CardField("name", "Attendee Name", required=True, placeholder="Sarah Kim"),
            CardField("title", "Title", placeholder="Product Manager"),
            CardField("company", "Company", placeholder="Tech Corp"),
            CardField("event_name", "Event Name", required=True, placeholder="DEF CON 2026"),
            CardField("event_date", "Event Date", placeholder="Aug 15-17, 2026"),
            CardField("badge_type", "Badge Type", options=["Attendee", "VIP", "Speaker", "Organizer"], required=True),
            CardField("email", "Email", type="email", placeholder="sarah@techcorp.com"),
            CardField("phone", "Phone", type="tel", placeholder="+60 19 906 6530"),
            CardField("website", "Website", type="url", placeholder="https://techcorp.com"),
        ],
        render=_render_event_badge,
    ),
    CardTemplate(
        id="real_estate",
        name="Real Estate Agent",
        description="Agent card with license number, agency, and specialties",
        category="business",
        thumbnail_color=GREEN_400,
        fields=[
            CardField("name", "Agent Name", required=True, placeholder="David Lim"),
            CardField("company", "Agency", required=True, placeholder="Premier Properties"),
            CardField("license", "License Number", required=True, placeholder="REN-12345"),
            CardField("phone", "Phone", type="tel", required=True, placeholder="+60 19 906 6530"),
            CardField("email", "Email", type="email", placeholder="david@premier.com"),
            CardField("website", "Website", type="url", placeholder="https://premier.com"),
            CardField("specialties", "Specialties", placeholder="Residential, Commercial"),
        ],
        render=_render_real_estate,
    ),
    CardTemplate(
        id="medical",
        name="Medical Professional",
        description="Doctor or nurse card with license, hospital, and medical ID panel",
        category="medical",
        thumbnail_color=CYAN_400,
        fields=[
            CardField("name", "Full Name", required=True, placeholder="Dr. Sarah Tan"),
            CardField("specialty", "Specialty", placeholder="Cardiology"),
            CardField("license", "License Number", required=True, placeholder="MMC-12345"),
            CardField("hospital", "Hospital/Clinic", placeholder="General Hospital KL"),
            CardField("phone", "Phone", type="tel", placeholder="+60 3 2345 6789"),
            CardField("email", "Email", type="email", placeholder="dr.tan@ghkl.gov.my"),
            CardField("emergency_line", "Emergency Line", type="tel", placeholder="+60 3 2345 6790"),
            CardField("blood_type", "Blood Type", options=["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]),
            CardField("allergies", "Allergies", placeholder="None"),
            CardField("conditions", "Conditions", placeholder="None"),
        ],
        render=_render_medical,
    ),
    CardTemplate(
        id="minimalist",
        name="Minimalist Card",
        description="Clean, simple design focused on name and essential contact",
        category="personal",
        thumbnail_color=FG_0,
        fields=[
            CardField("name", "Name", required=True, placeholder="Alex Morgan"),
            CardField("tagline", "Tagline / Title", placeholder="Creative Director"),
            CardField("phone", "Phone", type="tel", placeholder="+60 19 906 6530"),
            CardField("email", "Email", type="email", placeholder="alex@morgan.io"),
            CardField("website", "Website", type="url", placeholder="https://morgan.io"),
            CardField("tagline_2", "Additional Info", placeholder="@alexmorgan"),
        ],
        render=_render_minimal,
    ),
    CardTemplate(
        id="taglink",
        name="TagLink Card",
        description="Dynamic redirect — update where it points anytime via API",
        category="personal",
        thumbnail_color=RED_500,
        fields=[
            CardField("url", "Redirect URL", type="url", required=True, placeholder="https://example.com"),
            CardField("description", "Card Label", placeholder="My portfolio site"),
        ],
        render=_render_taglink,
    ),
    CardTemplate(
        id="corporate",
        name="Corporate Card",
        description="Branded employee card with department and employee ID",
        category="business",
        thumbnail_color=FG_0,
        fields=[
            CardField("name", "Employee Name", required=True, placeholder="Sarah Lee"),
            CardField("company", "Company", required=True, placeholder="Uwitz Corp"),
            CardField("title", "Job Title", placeholder="Product Designer"),
            CardField("department", "Department", placeholder="Engineering"),
            CardField("employee_id", "Employee ID", placeholder="EMP-001"),
            CardField("email", "Email", type="email", placeholder="sarah@uwitzcorp.com"),
            CardField("phone", "Phone", type="tel", placeholder="+60 19 906 6530"),
        ],
        render=_render_corporate,
    ),
]

TEMPLATES_BY_ID = {t.id: t for t in TEMPLATES}
TEMPLATE_CATEGORIES = {
    "emergency": "Emergency & Safety",
    "business": "Business & Professional",
    "student": "Student & Academic",
    "events": "Events & Conferences",
    "medical": "Medical",
    "personal": "Personal",
}


def get_template(template_id: str) -> CardTemplate | None:
    return TEMPLATES_BY_ID.get(template_id)


def list_templates() -> list[dict]:
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "category": t.category,
            "thumbnail_color": t.thumbnail_color,
            "fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "type": f.type,
                    "placeholder": f.placeholder,
                    "required": f.required,
                    "options": f.options,
                }
                for f in t.fields
            ],
        }
        for t in TEMPLATES
    ]


def render_template_preview(template_id: str, fields: dict, qr_data: str = "") -> bytes:
    """Render a full card preview from template + field values."""
    tpl = get_template(template_id)
    if not tpl:
        raise ValueError(f"Unknown template: {template_id}")
    img = tpl.render(fields, qr_data)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_template_back(template_id: str, fields: dict, qr_data: str = "", card_id: str = "") -> bytes:
    """Render the card back face (shared design across templates)."""
    if not get_template(template_id):
        raise ValueError(f"Unknown template: {template_id}")
    img = _render_back(fields, qr_data, card_id)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_template_thumbnail(template_id: str) -> bytes:
    """Render a small thumbnail for the template gallery using placeholder values."""
    tpl = get_template(template_id)
    if not tpl:
        raise ValueError(f"Unknown template: {template_id}")

    sample_fields = {}
    for f in tpl.fields:
        if f.options:
            sample_fields[f.key] = f.options[0]
        elif f.placeholder:
            sample_fields[f.key] = f.placeholder
        else:
            sample_fields[f.key] = "Sample"

    img = tpl.render(sample_fields)

    # Scale to thumbnail
    thumb_w, thumb_h = 400, 252
    img = img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_vcard_from_fields(fields: dict) -> str:
    """Build a vCard 4.0 string from template fields."""
    lines = ["BEGIN:VCARD", "VERSION:4.0"]
    if fields.get("name"):
        lines.append(f"FN:{fields['name']}")
    if fields.get("company") or fields.get("university"):
        lines.append(f"ORG:{fields.get('company') or fields.get('university', '')}")
    if fields.get("title"):
        lines.append(f"TITLE:{fields['title']}")
    if fields.get("email"):
        lines.append(f"EMAIL:{fields['email']}")
    if fields.get("phone"):
        lines.append(f"TEL;TYPE=CELL:{fields['phone']}")
    if fields.get("website"):
        lines.append(f"URL:{fields['website']}")
    if fields.get("address"):
        lines.append(f"ADR;TYPE=HOME:;;{fields['address']};;;")
    lines.append("END:VCARD")
    return "\n".join(lines) + "\n"
