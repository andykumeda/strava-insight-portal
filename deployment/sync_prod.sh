#!/bin/bash
set -e

# Configuration
WEB_ROOT="/var/www/activitycopilot"

echo "🚀 Starting Production Frontend Build & Sync..."

# 1. Build Frontend
echo "📦 Building frontend..."
if [ ! -d "frontend" ]; then
    echo "❌ Error: frontend directory not found. Please run this script from the project root."
    exit 1
fi

cd frontend
npm install
npm run build
cd ..

# 2. Ensure Web Root exists
echo "📁 Ensuring web root exists: $WEB_ROOT"
sudo mkdir -p "$WEB_ROOT"
sudo chown $USER:$USER "$WEB_ROOT"

# 3. Copy Frontend Files to Web Root
echo "🚚 Copying frontend files to $WEB_ROOT..."
cp -r frontend/dist/* "$WEB_ROOT/"

echo "✅ Frontend sync complete!"
echo "Backend is running from: $(pwd)"
echo ""
echo "Next steps:"
echo "1. Verify /home/andy/Dev/strava-activity-copilot/backend/.env is set up."
echo "2. Restart services: sudo systemctl restart strava-backend strava-mcp"
