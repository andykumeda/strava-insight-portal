#!/bin/bash
set -e

# Configuration
# You can override these with environment variables
REMOTE_USER="${REMOTE_USER:-ubuntu}"
REMOTE_HOST="${REMOTE_HOST:-your-server-ip}"
REMOTE_PATH="${REMOTE_PATH:-/var/www/strava-insight-portal}"
SSH_PORT="${SSH_PORT:-22}" # Default to 22, override with 420
SSH_KEY="${SSH_KEY:-}" # Optional: path to private key

echo "Deploying to $REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH on port $SSH_PORT"

# 1. Build Frontend
echo "Building frontend..."
cd ../frontend
npm install
npm run build
cd ../deployment

# 2. Sync Frontend Files
echo "Syncing frontend files..."
rsync -avz --delete -e "ssh -p $SSH_PORT ${SSH_KEY:+-i $SSH_KEY}" \
    ../frontend/dist/ \
    "$REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH/html/"

# 3. Sync Application Code (Backend & Deployment)
echo "Syncing application code..."
rsync -avz -e "ssh -p $SSH_PORT ${SSH_KEY:+-i $SSH_KEY}" \
    --exclude 'venv' --exclude '__pycache__' --exclude '*.db' --exclude '.env' \
    ../backend \
    ../mcp-server \
    ../deployment \
    "$REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH/"

# 3.5 Install Dependencies
echo "Installing/Updating dependencies..."
ssh -p $SSH_PORT ${SSH_KEY:+-i $SSH_KEY} "$REMOTE_USER@$REMOTE_HOST" \
    "source $REMOTE_PATH/venv/bin/activate && pip install -r $REMOTE_PATH/backend/requirements.txt"

# 4. Reload Nginx (Optional - requires sudo permissions on remote user)
# echo "Reloading Nginx..."
# ssh ${SSH_KEY:+-i $SSH_KEY} "$REMOTE_USER@$REMOTE_HOST" "sudo systemctl reload nginx"

echo "Deployment complete!"
