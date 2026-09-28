# Product

## What
Uwitz Cards is a SaaS platform for NFC business cards. Users create, manage, and distribute physical NFC cards that link to digital profiles — vCards, dynamic redirect links, or corporate team pages.

## Users
1. **Individuals** — professionals who want a smart NFC card with their contact info, social links, or a custom landing page. Self-serve, pay monthly/yearly.
2. **Corporate admins** — team managers who order cards in bulk, assign them to employees, and manage the organization's card fleet via a dashboard.
3. **Super admin** — internal operator managing all users, cards, orders, and subscriptions across the platform.

Both individual and corporate users are first-class; the product serves both equally.

## How
1. User registers (email/password or Microsoft Entra SSO) → lands on dashboard.
2. Creates a card — chooses type (Social/vCard, TagLink/redirect, Corporate), fills in profile data, uploads assets.
3. Cards are assigned physical NFC chip IDs (NTAG 215); the platform links chip → digital profile.
4. User can view/edit cards, track scans/analytics, manage subscription, place orders for physical cards.
5. Corporate admins see team-wide dashboards, bulk order workflows, and employee card management.
6. Payments via Stripe (MYR), subscriptions with tier gating.

## Architecture
- **Backend:** Python/FastAPI, MongoDB (motor async driver), Pydantic v2 models
- **Frontend:** Jinja2 server-rendered templates, HTMX for interactivity, vanilla CSS (no framework)
- **Auth:** Microsoft Entra ID (SSO) + email/password with bcrypt, session cookies, CSRF protection
- **Payments:** Stripe (3 tiers: Social, TagLink, Corporate — yearly/monthly)
- **Email:** SMTP2Go transactional
- **Storage:** Irys for media uploads
- **Deployment:** Uvicorn behind reverse proxy, systemd service on prod server

## Key Surfaces
- `/` — Landing page (marketing, conversion)
- `/dashboard` — Main dashboard with stats
- `/dashboard/cards` — Card management (list, create, edit)
- `/dashboard/orders` — Order management
- `/dashboard/subscriptions` — Subscription/billing
- `/dashboard/corporate` — Corporate admin panel
- `/dashboard/logistics` — Logistics admin (card inventory, shipping)
- `/dashboard/admin` — Super admin (users, all cards, all orders)
- `/auth/register`, `/auth/login` — Authentication
- `/legal/terms`, `/legal/privacy` — Legal pages
- `/api/webhooks/stripe` — Stripe webhook receiver

## Design Tokens
- **Background:** #09090A (bg-0) through #222225 (bg-4)
- **Accent:** Red #DA2A1C (red-500) — primary brand color
- **Fonts:** Geist Mono (display/headings/hero), Geist (body/labels)
- **Text:** Warm off-white #F3F1EF (fg-0), muted #BDB9B6 (fg-1)
- **Borders:** Subtle dark hairlines (#202023 → #3D3D42)
- **Surfaces:** Dark cards with subtle borders, no heavy shadows
- **Motion:** Custom ease-out curves, 90-400ms durations
- **Radii:** Pill buttons (999px), rounded cards (14px), medium inputs (9px)

## Pricing
- **Social:** RM 79/year — individual vCard profile
- **TagLink:** RM 15/month — dynamic redirect link
- **Corporate:** RM 299/year — team management, bulk cards, analytics
