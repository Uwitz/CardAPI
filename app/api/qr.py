"""In-house QR code generation — no external API dependencies.

Generates QR codes locally using the qrcode + Pillow libraries for PNG output
and segno for SVG output.  Supports all content types (text, WiFi, SMS, email,
phone, crypto, vCard, WhatsApp, vEvent, geo), colour customization, error
correction levels, border sizing, and basic logo overlay.
"""

import io
import os
import uuid

import qrcode
import qrcode.constants
import segno
from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
from PIL import Image, ImageDraw, ImageFont

router = APIRouter(prefix="/api/qr", tags=["qr"])

# ---------------------------------------------------------------------------
# Content-type encoding helpers
# ---------------------------------------------------------------------------

QR_TYPES = {
    0: (["text"], ["text"]),  # Text / URL
    1: (["network"], ["network", "password", "hidden", "encryption"]),  # WiFi
    2: (["text", "number"], ["text", "number"]),  # SMS
    3: (["email"], ["email", "email-cc", "email-bcc", "subject", "message"]),  # Email
    4: (["number"], ["number"]),  # Phone
    5: (["cryptotype", "address"], ["cryptotype", "address", "amount"]),  # Crypto
    6: (["vc_first_name", "vc_last_name"],
        ["vc_first_name", "vc_last_name", "vc_company", "vc_job", "vc_street", "vc_city",
         "vc_state", "vc_zip", "vc_country", "vc_phone", "vc_mobile", "vc_fax",
         "vc_website", "vc_email", "vc_note"]),  # vCard
    7: (["number"], ["number", "text"]),  # WhatsApp
    8: ([], ["ve_summary", "ve_description", "ve_location", "ve_timezone", "ve_url",
            "ve_start", "ve_end"]),  # vEvent
    9: (["ge_latitude", "ge_longitude"], ["ge_latitude", "ge_longitude"]),  # Geo
}

# Formats we can produce natively
SUPPORTED_FORMATS = {"png", "svg"}

ERROR_CORRECTION_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}

# Logo storage directory
_LOGO_DIR = os.path.join("static", "uploads", "qr_logos")

# ---------------------------------------------------------------------------
# Content encoding
# ---------------------------------------------------------------------------

def _encode_content(body: dict, qr_type: int) -> str:
    """Build the QR payload string from the typed fields."""
    if qr_type == 0:  # Text / URL
        return body.get("text", "")

    if qr_type == 1:  # WiFi
        ssid = body.get("network", "")
        pwd = body.get("password", "")
        enc = body.get("encryption", "WPA")
        hidden = body.get("hidden", "false")
        return f"WIFI:T:{enc};S:{ssid};P:{pwd};H:{hidden};;"

    if qr_type == 2:  # SMS
        num = body.get("number", "")
        msg = body.get("text", "")
        return f"SMSTO:{num}:{msg}"

    if qr_type == 3:  # Email
        to = body.get("email", "")
        cc = body.get("email-cc", "")
        bcc = body.get("email-bcc", "")
        subj = body.get("subject", "")
        msg = body.get("message", "")
        parts = [f"mailto:{to}"]
        params = []
        if cc:
            params.append(f"cc={cc}")
        if bcc:
            params.append(f"bcc={bcc}")
        if subj:
            params.append(f"subject={subj}")
        if msg:
            params.append(f"body={msg}")
        if params:
            parts.append("?" + "&".join(params))
        return "".join(parts)

    if qr_type == 4:  # Phone
        return f"tel:{body.get('number', '')}"

    if qr_type == 5:  # Crypto
        coin = body.get("cryptotype", "")
        addr = body.get("address", "")
        amt = body.get("amount", "")
        return f"{coin.lower()}:{addr}?amount={amt}" if amt else f"{coin.lower()}:{addr}"

    if qr_type == 6:  # vCard
        lines = ["BEGIN:VCARD", "VERSION:3.0"]
        fn = f"{body.get('vc_first_name', '')} {body.get('vc_last_name', '')}".strip()
        if fn:
            lines.append(f"FN:{fn}")
        if body.get("vc_company"):
            lines.append(f"ORG:{body['vc_company']}")
        if body.get("vc_job"):
            lines.append(f"TITLE:{body['vc_job']}")
        if body.get("vc_email"):
            lines.append(f"EMAIL:{body['vc_email']}")
        for tag, key in [("TEL", "vc_phone"), ("TEL", "vc_mobile"), ("TEL", "vc_fax"),
                         ("URL", "vc_website"), ("NOTE", "vc_note")]:
            val = body.get(key, "")
            if val:
                prefix = "TEL;TYPE=CELL" if tag == "TEL" and key == "vc_mobile" else \
                         "TEL;TYPE=FAX" if tag == "TEL" and key == "vc_fax" else "TEL"
                if tag != "TEL":
                    prefix = tag
                lines.append(f"{prefix}:{val}")
        adr_parts = [body.get("vc_street", ""), body.get("vc_city", ""),
                     body.get("vc_state", ""), body.get("vc_zip", ""),
                     body.get("vc_country", "")]
        if any(adr_parts):
            lines.append(f"ADR;TYPE=HOME:;;{';'.join(adr_parts)};;")
        lines.append("END:VCARD")
        return "\r\n".join(lines)

    if qr_type == 7:  # WhatsApp
        num = body.get("number", "")
        msg = body.get("text", "")
        return f"https://wa.me/{num}?text={msg}" if msg else f"https://wa.me/{num}"

    if qr_type == 8:  # vEvent
        lines = ["BEGIN:VEVENT"]
        if body.get("ve_summary"):
            lines.append(f"SUMMARY:{body['ve_summary']}")
        if body.get("ve_description"):
            lines.append(f"DESCRIPTION:{body['ve_description']}")
        if body.get("ve_location"):
            lines.append(f"LOCATION:{body['ve_location']}")
        if body.get("ve_timezone"):
            lines.append(f"TZID:{body['ve_timezone']}")
        if body.get("ve_url"):
            lines.append(f"URL:{body['ve_url']}")
        if body.get("ve_start"):
            lines.append(f"DTSTART:{body['ve_start']}")
        if body.get("ve_end"):
            lines.append(f"DTEND:{body['ve_end']}")
        lines.append("END:VEVENT")
        return "\r\n".join(lines)

    if qr_type == 9:  # Geo
        lat = body.get("ge_latitude", "")
        lon = body.get("ge_longitude", "")
        return f"geo:{lat},{lon}"

    return body.get("text", "")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _clean_int(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _parse_hex_color(value: str) -> tuple[int, int, int, int]:
    """Parse '#RRGGBB' or '#RRGGBBAA' to (R, G, B, A)."""
    v = value.strip().lstrip("#")
    if len(v) == 6:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), 255)
    if len(v) == 8:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), int(v[6:8], 16))
    raise ValueError(f"bad color: {value}")


def build_payload(body: dict, *, fast: bool = False) -> tuple[dict, bool]:
    """Validate a browser-supplied body and return (payload, is_custom)."""
    try:
        qr_type = int(body.get("type", 0))
    except (TypeError, ValueError):
        qr_type = 0
    if qr_type not in QR_TYPES:
        raise HTTPException(status_code=400, detail={"error": "invalid_type"})

    required, allowed = QR_TYPES[qr_type]
    payload: dict = {"type": qr_type}

    # Content fields
    for field in allowed:
        value = body.get(field)
        if value is None or value == "":
            continue
        payload[field] = str(value)
    missing = [f for f in required if not body.get(f)]
    if missing:
        raise HTTPException(status_code=400, detail={"error": "missing_fields", "fields": missing})

    # Fast QR codes: only content + geometry, no styling
    if fast:
        payload["imageformat"] = body.get("imageformat", "png")
        if payload["imageformat"] not in SUPPORTED_FORMATS:
            payload["imageformat"] = "png"
        payload["bordersize"] = _clean_int(body.get("bordersize", 4), 0, 16, 4)
        payload["width"] = _clean_int(body.get("width", 500), 1, 4000, 500)
        payload["height"] = _clean_int(body.get("height", 500), 1, 4000, 500)
        return payload, False

    # Full QR codes: styling
    fmt = body.get("imageformat", "png")
    if fmt not in SUPPORTED_FORMATS:
        fmt = "png"
    payload["imageformat"] = fmt

    ec = str(body.get("error_correction", "M")).upper()
    if ec not in ERROR_CORRECTION_MAP:
        ec = "M"
    payload["error_correction"] = ec

    payload["bordersize"] = _clean_int(body.get("bordersize", 4), 0, 16, 4)
    payload["width"] = _clean_int(body.get("width", 500), 1, 4000, 500)
    payload["height"] = _clean_int(body.get("height", 500), 1, 4000, 500)

    # Colour params
    for key in ("color", "background_color"):
        val = body.get(key)
        if val:
            _parse_hex_color(val)  # validate
            payload[key] = val

    # Logo
    logo = body.get("logo")
    if logo:
        payload["logo"] = logo

    return payload, True


# ---------------------------------------------------------------------------
# PNG generation
# ---------------------------------------------------------------------------

def _generate_png(payload: dict) -> bytes:
    """Generate a QR code PNG image using qrcode + Pillow."""
    qr_type = payload["type"]
    content = _encode_content(payload, qr_type)
    if not content:
        content = payload.get("text", "")

    ec = ERROR_CORRECTION_MAP.get(payload.get("error_correction", "M"), qrcode.constants.ERROR_CORRECT_M)
    border = payload.get("bordersize", 4)
    target_w = payload.get("width", 500)
    target_h = payload.get("height", 500)

    qr = qrcode.QRCode(
        version=None,
        error_correction=ec,
        box_size=10,
        border=border,
    )
    qr.add_data(content)
    qr.make(fit=True)

    # Build colours
    fill_hex = payload.get("color", "#0D0D0D")
    back_hex = payload.get("background_color", "#FFFFFF")
    fill_rgba = _parse_hex_color(fill_hex)
    back_rgba = _parse_hex_color(back_hex)

    qr_img = qr.make_image(fill_color=fill_rgba, back_color=back_rgba).convert("RGBA")

    # Resize to requested dimensions
    if target_w and target_h:
        qr_img = qr_img.resize((target_w, target_h), Image.Resampling.NEAREST)

    # Overlay custom logo if provided
    logo_ref = payload.get("logo")
    if logo_ref:
        _overlay_logo(qr_img, logo_ref)

    buf = io.BytesIO()
    qr_img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# SVG generation
# ---------------------------------------------------------------------------

def _generate_svg(payload: dict) -> bytes:
    """Generate a QR code SVG using segno."""
    qr_type = payload["type"]
    content = _encode_content(payload, qr_type)
    if not content:
        content = payload.get("text", "")

    ec_str = payload.get("error_correction", "M")
    ec_map = {"L": "L", "M": "M", "Q": "Q", "H": "H"}

    qr = segno.make(content, error=ec_map.get(ec_str, "M"))
    fill_hex = payload.get("color", "#0D0D0D").lstrip("#")
    back_hex = payload.get("background_color", "#FFFFFF").lstrip("#")
    border = payload.get("bordersize", 4)
    target_w = payload.get("width", 500)

    sym_w, _ = qr.symbol_size(border=border)
    scale = max(1, target_w / sym_w) if sym_w else 1

    buf = io.BytesIO()
    qr.save(
        buf,
        kind="svg",
        scale=scale,
        dark=fill_hex,
        light=back_hex,
        border=border,
    )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Logo overlay
# ---------------------------------------------------------------------------

def _overlay_logo(img: Image.Image, logo_ref: str):
    """Paste a logo into the centre of the QR image."""
    logo_path = os.path.join(_LOGO_DIR, f"{logo_ref}.png")
    if not os.path.isfile(logo_path):
        return
    try:
        logo = Image.open(logo_path).convert("RGBA")
        logo_max = min(img.size) // 5
        logo.thumbnail((logo_max, logo_max), Image.Resampling.LANCZOS)
        lx = (img.width - logo.width) // 2
        ly = (img.height - logo.height) // 2
        img.paste(logo, (lx, ly), logo)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Logo upload
# ---------------------------------------------------------------------------

@router.post("/upload-logo")
async def upload_logo(file: UploadFile):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail={"error": "empty_file"})
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail={"error": "file_too_large"})

    os.makedirs(_LOGO_DIR, exist_ok=True)
    logo_id = uuid.uuid4().hex
    path = os.path.join(_LOGO_DIR, f"{logo_id}.png")

    img = Image.open(io.BytesIO(data))
    img = img.convert("RGBA")
    img.save(path, format="PNG")
    return {"logo_id": logo_id, "status": 200}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/generate")
async def generate_qr(request: Request):
    body = await request.json()
    payload, _ = build_payload(body, fast=False)
    fmt = payload.get("imageformat", "png")

    if fmt == "svg":
        content = _generate_svg(payload)
        return Response(content=content, media_type="image/svg+xml")

    content = _generate_png(payload)
    return Response(content=content, media_type="image/png")


@router.post("/fastgenerate")
async def generate_fast_qr(request: Request):
    body = await request.json()
    payload, _ = build_payload(body, fast=True)
    fmt = payload.get("imageformat", "png")

    if fmt == "svg":
        content = _generate_svg(payload)
        return Response(content=content, media_type="image/svg+xml")

    content = _generate_png(payload)
    return Response(content=content, media_type="image/png")


@router.post("/fastbatch")
async def generate_fast_batch(request: Request):
    body = await request.json()
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=400, detail={"error": "batch_empty"})

    results: dict = {}
    for name, item in body.items():
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail={"error": "batch_item_invalid", "name": name})
        payload, _ = build_payload(item, fast=True)
        fmt = payload.get("imageformat", "png")
        if fmt == "svg":
            results[str(name)] = {"data": _generate_svg(payload).decode("utf-8"), "type": "image/svg+xml"}
        else:
            import base64
            results[str(name)] = {"data": base64.b64encode(_generate_png(payload)).decode(), "type": "image/png"}

    return results
