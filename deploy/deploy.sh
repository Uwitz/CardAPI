#!/bin/bash
# Uwitz Cards — Production Deploy Script
# Run this ON THE SERVER after migration.
set -euo pipefail

CARD_API="/home/snyco/CardAPI"
BRANCH="${1:-master}"

echo "=== Uwitz Cards Deploy ==="
echo "Branch: $BRANCH"

# 1. Pull new code
echo "[1/5] Pulling latest code..."
cd "$CARD_API"
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

# 2. Update .env if new vars needed
echo "[2/5] Checking environment..."
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
echo "[3/5] Installing dependencies..."
source venv/bin/activate
pip install -r requirements.txt 2>&1 | tail -3
mkdir -p static/uploads/cards

# 5. Update systemd
echo "[4/5] Updating systemd service..."
sudo cp deploy/cards.service /etc/systemd/system/cards.service 2>/dev/null || true
sudo systemctl daemon-reload

# 6. Restart
echo "[5/5] Restarting service..."
sudo systemctl restart cards.service
sleep 2

if sudo systemctl is-active --quiet cards.service; then
  echo ""
  echo "=== Deploy successful ==="
  echo "Service is running."
  echo "Health check: curl http://127.0.0.1:8000/health"
else
  echo ""
  echo "=== DEPLOY FAILED ==="
  echo "Service not running. Check: sudo journalctl -u cards.service -n 50"
  echo "Rollback: cp -r /home/snyco/backups/cards-*/old-code/* $CARD_API/"
fi
