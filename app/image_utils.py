import io
from PIL import Image

MIN_WIDTH_PX = 1011
MIN_HEIGHT_PX = 638
EXPECTED_RATIO = 85.6 / 54
RATIO_TOLERANCE = 0.01


def validate_card_image(file_bytes: bytes) -> tuple[bool, str, dict | None]:
    try:
        img = Image.open(io.BytesIO(file_bytes))
    except Exception:
        return False, "Invalid image file", None

    w, h = img.size
    ratio = w / h

    if w < MIN_WIDTH_PX or h < MIN_HEIGHT_PX:
        return False, f"Minimum {MIN_WIDTH_PX}x{MIN_HEIGHT_PX}px (300 DPI for ISO/IEC 7810 ID-1)", None

    if abs(ratio - EXPECTED_RATIO) > RATIO_TOLERANCE:
        return False, "Aspect ratio must be 85.6:54mm (ISO/IEC 7810 ID-1)", None

    info = {"width": w, "height": h, "dpi": img.info.get("dpi", (300, 300))[0]}
    return True, "ok", info
