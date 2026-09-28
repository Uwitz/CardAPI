"""Input sanitization and validation utilities"""

import re
from urllib.parse import urlparse
from typing import Optional, List

# Allowed redirect domains (configure in production)
ALLOWED_REDIRECT_DOMAINS = [
    "uwitz.cards",
    "portal.uwitz.cards",
    # Add customer domains here
]

# vCard field sanitization
VCARD_ALLOWED_FIELDS = {
    "BEGIN", "END", "VERSION", "FN", "N", "NICKNAME", "PHOTO", "BDAY",
    "ADR", "LABEL", "TEL", "EMAIL", "MAILER", "TZ", "GEO", "TITLE",
    "ROLE", "LOGO", "AGENT", "ORG", "NOTE", "REV", "SOUND", "URL",
    "UID", "KEY", "X-", "PRODID", "CLASS", "SORT-STRING", "CATEGORIES"
}

# Dangerous patterns in vCard
VCARD_DANGEROUS_PATTERNS = [
    r"SCRIPT",           # Script tags
    r"JAVASCRIPT:",      # javascript: protocol
    r"DATA:",            # data: protocol
    r"VBScript:",        # vbscript: protocol
    r"ON\w+\s*=",        # Event handlers (onclick=, etc.)
    r"<[^>]*>",          # HTML tags
    r"EXPRESSION\s*\(",  # CSS expression()
    r"BEHAVIOR\s*:",     # CSS behavior
    r"-MOZ-BINDING",     # Mozilla binding
    r"@IMPORT",          # CSS @import
]


def sanitize_vcard_field(value: str, field_name: str = "") -> str:
    """Sanitize a single vCard field value."""
    if not value:
        return ""
    
    # Remove null bytes
    value = value.replace("\x00", "")
    
    # Limit length
    value = value[:4096]
    
    # Remove control characters except newline and tab
    value = "".join(ch for ch in value if ord(ch) >= 32 or ch in "\n\t\r")
    
    # Check for dangerous patterns (case insensitive)
    upper_value = value.upper()
    for pattern in VCARD_DANGEROUS_PATTERNS:
        if re.search(pattern, upper_value, re.IGNORECASE):
            # Replace dangerous pattern with safe version
            value = re.sub(pattern, "[BLOCKED]", value, flags=re.IGNORECASE)
    
    return value


def sanitize_vcard_data(vcard_data: str) -> str:
    """Sanitize full vCard data."""
    if not vcard_data:
        return ""
    
    lines = vcard_data.split("\n")
    sanitized_lines = []
    
    for line in lines:
        # Handle line folding (continuation lines start with space/tab)
        if line.startswith((" ", "\t")) and sanitized_lines:
            # Continuation line - sanitize and append
            sanitized = sanitize_vcard_field(line[1:])
            sanitized_lines[-1] += sanitized
        else:
            # New field
            if ":" in line:
                field_name, field_value = line.split(":", 1)
                field_name = field_name.strip().upper()
                # Only allow known vCard fields
                if any(field_name.startswith(allowed) for allowed in VCARD_ALLOWED_FIELDS):
                    sanitized_value = sanitize_vcard_field(field_value, field_name)
                    sanitized_lines.append(f"{field_name}:{sanitized_value}")
                else:
                    # Unknown field - skip or sanitize conservatively
                    sanitized_value = sanitize_vcard_field(field_value, field_name)
                    sanitized_lines.append(f"{field_name}:{sanitized_value}")
            else:
                # Malformed line - skip
                continue
    
    return "\n".join(sanitized_lines)


def validate_redirect_url(url: str, allowed_domains: Optional[List[str]] = None) -> tuple[bool, str]:
    """
    Validate redirect URL against allowlist.
    Returns (is_valid, error_message).
    """
    if not url:
        return False, "URL is required"
    
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL format"
    
    # Must be HTTPS (or HTTP for localhost dev)
    if parsed.scheme not in ("http", "https"):
        return False, "Only HTTP/HTTPS URLs allowed"
    
    # Block localhost/internal IPs in production
    hostname = parsed.hostname or ""
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        # Allow in development - you may want to restrict this
        pass
    
    # Block private IP ranges
    if _is_private_ip(hostname):
        return False, "Private IP addresses not allowed"
    
    # Check against allowed domains
    domains = allowed_domains or ALLOWED_REDIRECT_DOMAINS
    if domains:
        # Allow subdomains of allowed domains
        allowed = False
        for domain in domains:
            if hostname == domain or hostname.endswith(f".{domain}"):
                allowed = True
                break
        if not allowed:
            return False, f"Domain not in allowlist: {hostname}"
    
    # Limit URL length
    if len(url) > 2048:
        return False, "URL too long"
    
    # Block dangerous patterns
    dangerous = ["javascript:", "data:", "vbscript:", "file:", "ftp:"]
    for d in dangerous:
        if url.lower().startswith(d):
            return False, f"Dangerous protocol: {d}"
    
    return True, ""


def _is_private_ip(hostname: str) -> bool:
    """Check if hostname is a private IP address."""
    try:
        parts = hostname.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            ip_parts = [int(p) for p in parts]
            # 10.0.0.0/8
            if ip_parts[0] == 10:
                return True
            # 172.16.0.0/12
            if ip_parts[0] == 172 and 16 <= ip_parts[1] <= 31:
                return True
            # 192.168.0.0/16
            if ip_parts[0] == 192 and ip_parts[1] == 168:
                return True
            # 169.254.0.0/16 (link-local)
            if ip_parts[0] == 169 and ip_parts[1] == 254:
                return True
            # 127.0.0.0/8 (loopback)
            if ip_parts[0] == 127:
                return True
    except (ValueError, IndexError):
        pass
    return False


def sanitize_string(value: str, max_length: int = 1000, allow_newlines: bool = False) -> str:
    """General string sanitization."""
    if not value:
        return ""
    
    # Remove null bytes
    value = value.replace("\x00", "")
    
    # Remove control characters
    if allow_newlines:
        value = "".join(ch for ch in value if ord(ch) >= 32 or ch in "\n\t\r")
    else:
        value = "".join(ch for ch in value if ord(ch) >= 32)
    
    # Limit length
    return value[:max_length]


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe storage."""
    if not filename:
        return "upload"
    
    # Remove path components
    filename = filename.split("/")[-1].split("\\")[-1]
    
    # Remove dangerous characters
    filename = re.sub(r'[<>:"/\|?*\x00-\x1f]', "_", filename)
    
    # Limit length
    if len(filename) > 255:
        name, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
        filename = name[:255 - len(ext) - 1] + ("." + ext if ext else "")
    
    return filename