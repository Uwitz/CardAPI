#!/bin/bash
# Uwitz Cards — Production Deploy Script
# Run this ON THE SERVER after migration.
set -euo pipefail

CARD_API="/home/snyco/CardAPI"
BRANCH="${1:-master}"

echo "=== Uwitz Cards Deploy ==="
echo "Branch: $BRANCH"

# 1. Pull new code
echo "[1/6] Pulling latest code..."
cd "$CARD_API"
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

# 2. Update .env if new vars needed
echo "[2/6] Checking environment..."
if ! grep -q "ENTRA_CLIENT_SECRET" .env 2>/dev/null; then
  echo "  Adding new env vars to .env..."
  cat >> .env << 'ENVEOF'

# SSO (added during v3 upgrade)
ENTRA_CLIENT_ID=bf993ac3-0ca5-4172-9617-0e8851d5de1d
ENTRA_CLIENT_SECRET=CHANGE_ME
ENTRA_TENANT_ID=7625c8c5-0680-4ccc-8840-dc993791d475
IRYS_CLIENT_ID=CHANGE_ME
IRYS_CLIENT_SECRET=CHANGE_ME
ENVEOF
  echo "  WARNING: Update ENTRA_CLIENT_SECRET, IRYS_CLIENT_ID, IRYS_CLIENT_SECRET in .env"
fi

# 2b. Add compliance env vars if missing
if ! grep -q "SESSION_SECRET" .env 2>/dev/null; then
  echo "  Adding compliance env vars..."
  cat >> .env << 'ENVEOF'

# Compliance - generate with: python -c "import secrets; print(secrets.token_hex(32))"
SESSION_SECRET=CHANGE_ME_GENERATE_WITH_PYTHON_SECRETS_TOKEN_HEX_32

# Cookie consent version
COOKIE_CONSENT_VERSION=2026-09-07
ENVEOF
  echo "  WARNING: Update SESSION_SECRET in .env (generate with: python -c \"import secrets; print(secrets.token_hex(32))\")"
fi

# 3. Update MONGO_URI if using old format
if grep -q "MONGO_USER" .env 2>/dev/null && ! grep -q "MONGO_URI" .env 2>/dev/null; then
  echo "  Converting old MONGO_USER/PASS/HOST to MONGO_URI..."
  MONGO_USER=$(grep MONGO_USER .env | cut -d= -f2)
  MONGO_PASS=$(grep MONGO_PASS .env | cut -d= -f2)
  MONGO_HOST=$(grep MONGO_HOST .env | cut -d= -f2)
  MONGO_DB=$(grep MONGO_DB .env | cut -d= -f2)
  echo "MONGO_URI=mongodb://${MONGO_USER}:${MONGO_PASS}@${MONGO_HOST}" >> .env
  echo "MONGO_DB=${MONGO_DB:-cards}" >> .env
fi

# 4. Install deps + ensure upload dir
echo "[3/6] Installing dependencies..."
source venv/bin/activate
pip install -r requirements.txt 2>&1 | tail -3
mkdir -p static/uploads/cards

# 5. Create database indexes for compliance collections
echo "[4/6] Creating database indexes..."
python3 -c "
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv
load_dotenv()
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017')
MONGO_DB = os.getenv('MONGO_DB', 'cards')
client = AsyncIOMotorClient(MONGO_URI)
db = client[MONGO_DB]

async def create_indexes():
    # Consent records
    await db['consent_records'].create_index([('user_id', 1), ('consent_type', 1), ('timestamp', -1)])
    await db['consent_records'].create_index('timestamp')
    
    # Data subject requests
    await db['data_subject_requests'].create_index([('user_id', 1), ('created_at', -1)])
    await db['data_subject_requests'].create_index('status')
    
    # Breach records
    await db['breach_records'].create_index([('discovered_at', -1)])
    await db['breach_records'].create_index('severity')
    
    # Age verification
    await db['age_verification'].create_index('user_id', unique=True)
    await db['age_verification'].create_index('verified_at')
    
    # Email logs (add index if missing)
    await db['email_logs'].create_index([('to', 1), ('created_at', -1)])
    
    # Audit logs
    await db['audit_logs'].create_index([('actor_id', 1), ('timestamp', -1)])
    await db['audit_logs'].create_index('action')
    await db['audit_logs'].create_index('timestamp')
    
    # Stripe events
    await db['stripe_events'].create_index('_id', unique=True)
    
    # Cards indexes (ensure they exist)
    await db['cards'].create_index('card_id', unique=True)
    await db['cards'].create_index('owner_id')
    await db['cards'].create_index('status')
    await db['cards'].create_index('activation_token_hash')
    
    # Users indexes (ensure they exist)
    await db['users'].create_index('username', unique=True)
    await db['users'].create_index('email', unique=True)
    await db['users'].create_index('lockout_until')
    
    # Orders indexes
    await db['orders'].create_index([('user_id', 1), ('created_at', -1)])
    await db['orders'].create_index('status')
    await db['orders'].create_index('card_id')
    
    print('All indexes created successfully')

asyncio.run(create_indexes())
" || echo "  Warning: Index creation failed (may already exist)"

# 6. Update systemd
echo "[5/6] Updating systemd service..."
sudo cp deploy/cards.service /etc/systemd/system/cards.service 2>/dev/null || true
sudo systemctl daemon-reload

# 7. Restart
echo "[6/6] Restarting service..."
sudo systemctl restart cards.service
sleep 3

if sudo systemctl is-active --quiet cards.service; then
  echo ""
  echo "=== Deploy successful ==="
  echo "Service is running."
  echo "Health check: curl https://uwitz.cards/health"
  echo "Compliance endpoints: https://uwitz.cards/api/compliance/compliance-status"
else
  echo ""
  echo "=== DEPLOY FAILED ==="
  echo "Service not running. Check: sudo journalctl -u cards.service -n 50"
  echo "Rollback: cp -r /home/snyco/backups/cards-*/old-code/* \$CARD_API/"
fi
