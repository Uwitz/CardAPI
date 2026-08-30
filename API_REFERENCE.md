# UZC API Reference

> **Base URL**: `https://api.uwitz.org` (production)  
> **Protocol**: HTTPS (TLS 1.3). No OpenAPI/Swagger.  
> **Rate limit**: 100 req / 60s per IP.

---

## Important: No CRUD Endpoints Exist Yet

This API currently exposes **only admin/analytics endpoints** — no generic REST CRUD for application data (users, cards, etc.).  
To save/update/delete MongoDB data from Python, you have two options:

1. **Add CRUD endpoints** to the Rust API (preferred) — define routes, handlers, serde schemas in `src/api/`
2. **Access MongoDB directly** from Python with `pymongo` (simpler, but bypasses the API gateway)

This reference covers everything that *does* exist.

---

## Auth

### Login
```
POST /api/admin/auth/login
Content-Type: application/json

{"username": "admin", "password": "<secret>"}
→ 200: {"token": "jwt..."}
→ 401: {"error": "invalid token"}
```

### Authenticated requests
```
Authorization: Bearer <token>
```

### Verify token
```
POST /api/admin/auth/verify
Content-Type: application/json

{"token": "jwt..."}
→ 200: {"valid": true, "claims": {...}}
```

JWT: EdDSA (Ed25519), 30min TTL, role `admin` required.

---

## Admin Endpoints (all require `Authorization: Bearer`)

### GET /api/admin/summary?window=15
```json
{
  "total_requests": 1234,
  "total_errors": 5,
  "error_rate_pct": 0.4,
  "avg_latency_ms": 12.3,
  "p95_latency_ms": 45.6,
  "p99_latency_ms": 120.0,
  "unique_endpoints": ["/health"],
  "requests_last_minute": 10,
  "events_last_hour": 3
}
```

### GET /api/admin/timeseries?window=60&bucket=5
```json
[
  {"bucket": "2026-06-24T12:00:00Z", "count": 100, "error_count": 2, "avg_latency_ms": 10.5}
]
```

### GET /api/admin/requests?page=1&per_page=50
```json
{
  "data": [
    {
      "_id": "objectid hex",
      "timestamp": "ISO8601",
      "method": "GET",
      "path": "/health",
      "status": 200,
      "latency_ms": 5.2,
      "peer_ip_prefix": "10.0.0.0/24",
      "user_agent": null,
      "endpoint_group": "health"
    }
  ],
  "total": 5000,
  "page": 1,
  "per_page": 50
}
```

### GET /api/admin/events?page=1&per_page=50
```json
{
  "data": [
    {
      "_id": "objectid hex",
      "timestamp": "ISO8601",
      "event_type": "server.startup",
      "level": "info",
      "message": "API started",
      "metadata": null
    }
  ],
  "total": 200,
  "page": 1,
  "per_page": 50
}
```

### GET /api/admin/system/health
```json
{
  "uptime_secs": 86400,
  "version": "0.1.2",
  "commit": "abc123def",
  "mem_used_mb": 128,
  "mem_total_mb": 256,
  "mem_percent": 50.0,
  "connections_active": 5,
  "db_connected": true,
  "current_time": "2026-06-24T12:00:00Z"
}
```

### GET /api/admin/db/stats
```json
{"db": "uzc", "collections": 6, "total_size_bytes": 1048576.0, "total_index_size_bytes": 262144.0}
```

### GET /api/admin/db/collections
```json
[
  {"name": "analytics.requests", "count": 5000, "size_bytes": 524288, "avg_obj_size_bytes": 104.8, "indexes": 3}
]
```

---

## Public Endpoints

### GET /health
```json
{"status": "ok", "version": "0.1.2"}
```

### POST /webhook/telnyx (Telnyx SMS/call webhook)
```json
{"data": {"event_type": "message.received", "payload": {...}}}
```
Header: `X-Telnyx-Signature: t=<unix_ts>,v1=<64_hex_chars>`

### POST /api/trigger-update (GitLab auto-deploy webhook)
Standard Webhooks format. `webhook-signature`, `webhook-id`, `webhook-timestamp` headers.

---

## MongoDB Collections (read-only)

| Database | Collection |
|---|---|
| `uzc` | `analytics.requests` |
| `uzc` | `analytics.events` |
| `uzc` | `analytics.system_snapshots` |
| `uzc` | `analytics.admin_users` |
| `uzc` | `analytics.api_keys` |

---

## Python Client (vanilla requests)

```python
import requests

class UzcAPI:
    def __init__(self, base_url: str = "https://api.uwitz.org"):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.token: str | None = None

    def login(self, username: str, password: str) -> str:
        r = self.session.post(f"{self.base}/api/admin/auth/login",
                              json={"username": username, "password": password})
        r.raise_for_status()
        self.token = r.json()["token"]
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        return self.token

    def health(self) -> dict:
        return self.session.get(f"{self.base}/health").json()

    def summary(self, window: int = 15) -> dict:
        return self.session.get(f"{self.base}/api/admin/summary", params={"window": window}).json()

    def timeseries(self, window: int = 60, bucket: int = 5) -> list:
        return self.session.get(f"{self.base}/api/admin/timeseries",
                                params={"window": window, "bucket": bucket}).json()

    def requests(self, page: int = 1, per_page: int = 50) -> dict:
        return self.session.get(f"{self.base}/api/admin/requests",
                                params={"page": page, "per_page": per_page}).json()

    def events(self, page: int = 1, per_page: int = 50) -> dict:
        return self.session.get(f"{self.base}/api/admin/events",
                                params={"page": page, "per_page": per_page}).json()

    def system_health(self) -> dict:
        return self.session.get(f"{self.base}/api/admin/system/health").json()

    def db_stats(self) -> dict:
        return self.session.get(f"{self.base}/api/admin/db/stats").json()

    def db_collections(self) -> list:
        return self.session.get(f"{self.base}/api/admin/db/collections").json()
```

---

## Error Format (all endpoints)
```json
{"error": "string"}
```

| Status | Meaning |
|---|---|
| 401 | `invalid token` / `token expired` / `missing credentials` |
| 403 | `insufficient permissions` |
| 404 | `not found` |
| 422 | `validation error: ...` |
| 429 | `rate limit exceeded` |
| 500 | `internal server error` (never leaks details) |
