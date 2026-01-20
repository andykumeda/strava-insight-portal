#!/bin/bash

# Run this script on the remote server to set up directories and dependencies.
# Usage: sudo ./setup_remote.sh [user]
# Defaults to 'andy' if no user provided.

TARGET_USER="${1:-andy}"
APP_DIR="/var/www/strava-insight-portal"

echo "Setting up server for user: $TARGET_USER"

# 1. Install Dependencies
echo "Installing dependencies..."
apt-get update
apt-get install -y python3-venv python3-pip nginx acl

# 2. Create Directory Structure
echo "Creating directory structure at $APP_DIR..."
mkdir -p "$APP_DIR/html"
mkdir -p "$APP_DIR/backend"
mkdir -p "$APP_DIR/mcp-server"

# 3. Set Permissions
# Give TARGET_USER ownership of the directory
chown -R "$TARGET_USER:$TARGET_USER" "$APP_DIR"
# Ensure the user can write to it (for rsync)
chmod -R 755 "$APP_DIR"

# 4. Create Virtual Environment
echo "Creating Python virtual environment..."
# Run as the target user so permissions are correct
su - "$TARGET_USER" -c "python3 -m venv $APP_DIR/venv"
su - "$TARGET_USER" -c "$APP_DIR/venv/bin/pip install -r $APP_DIR/backend/requirements.txt" || echo "Warning: requirements.txt not found yet. Run deploy first, then re-run pip install."

echo "Setup complete! Now run the deployment script from your local machine."
echo "After deployment configures files, run this to finish setup:"
echo "  sudo cp $APP_DIR/deployment/*.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now strava-backend strava-mcp"
echo "  sudo cp $APP_DIR/deployment/nginx.conf /etc/nginx/sites-available/strava-insight"
echo "  sudo ln -sf /etc/nginx/sites-available/strava-insight /etc/nginx/sites-enabled/"
echo "  sudo systemctl restart nginx"
