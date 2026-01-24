# ActivityCopilot

A production-ready web application that lets users log in with Strava and ask natural-language questions about their activity data using LLMs.

## Features

### 🧠 Intelligent Querying
- **Natural Language**: Ask questions like "How many miles did I run in 2025 vs 2024?" or "Show me runs where I mentioned 'pain' in the notes."
- **Smart Context**: Dynamically switches between summary data (for fast aggregates) and detailed activity records (for specific searches) to optimize performance and cost.
- **Private Notes Search**: Supports searching your activity notes and descriptions for keywords (e.g., "race", "injury", "easy").
- **Enriched Activity Details**: Automatically pulls top segments, parses "similar activities" to find matched routes, and formats everything with clickable links.
- **Group Run Detection**: Identifies how many athletes you ran with and highlights social activities.
- **Deep Data Analysis**:
  - **Zone Analysis**: Power and Heart Rate zone distribution for deeper training insights.
  - **Gear Tracking**: Tracks mileage on specific shoes or bikes.
  - **Map Visualization**: Renders interactive maps for activities directly in the chat.

### ⚡ Performance & Scale
- **Full History Access**: Fetches and caches your entire Strava activity history (thousands of activities).
- **Instant Comparisons**: Aggregate queries (e.g., monthly totals) are processed instantly using pre-computed summaries.
- **Async Architecture**: Fully asynchronous backend prevents blocking during large data syncs.
- **Smart Hydration Priority**: Background sync smartly prioritizes high-value activities (Runs, Rides, Swims) and social events (Kudos/Comments), skipping less relevant data to maximize API quota efficiency.
- **On-Demand Hydration**: Querying specific historical dates automatically triggers an immediate background fetch for those activities if they aren't already hydrated.
- **Robust Rate Handling**: Custom-built `StravaRateLimiter` enforces strict daily (800) and 15-minute (80) limits with state persistence and automatic safety aborts.

### 🔒 Enterprise-Grade Security
- **Data Encryption**: Strava access tokens are encrypted at rest in the database using Fernet (AES).
- **Secure Auth**: Uses HTTP-only, secure, signed JWT cookies for session management.
- **Hardened API**: Strict CORS policies and rate limiting (`slowapi`) protect the backend.

### 🏃‍♂️ Advanced Segment Analytics
- **Best Time & PRs**: Authoritative "Best Time" retrieval directly from Strava (bypassing local cache for accuracy).
- **Leaderboards**: Query "Who has the CR?" or "What is my rank on segment X?" (Requires Strava Premium for full leaderboards).
- **History Lookup**: Ask "List all my previous times" to fetch your complete effort history for a specific segment.
- **Direct Link Support**: Paste a Strava Segment URL to get instant stats, even if you haven't synced that activity yet.

### 💻 Modern UI
- **Dark Mode**: Sleek, eye-friendly dark theme that automatically syncs with system preferences or can be toggled manually.
- **Command History**: Use Up/Down arrows to cycle through previous queries.
- **Async Input**: Type your next question while the previous one processes.
- **Sync Status**: Real-time progress indicator in the header showing total enrichment percentage (e.g., "75% Synced").
- **High-Visibility Links**: Activity and Segment links are rendered as distinct, clickable pills for better navigation.
- **Rich Formatting**: AI responses formatted with Markdown, including structured headers and interactive map embeds.

## Architecture

- **Backend**: Python FastAPI (Port 8000) - Handles auth, query processing, and LLM orchestration.
- **Frontend**: React + Vite + TypeScript + TailwindCSS (Port 5173) - Responsive chat interface.
- **Data Server (MCP)**: Python FastAPI (Port 8001) - Dedicated microservice for Strava data fetching, caching, and summarization.
- **Database**: SQLite (default) or PostgreSQL - Stores encrypted user tokens.
- **AI Integration**: OpenRouter (DeepSeek) or Google Gemini.

## Setup & Running

### Prerequisites
- Python 3.12+
- Node.js 18+
- Strava API Application (Client ID & Secret)
- OpenRouter API Key (recommended) or Gemini API Key

### 1. Backend Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

Create `backend/.env`:
```bash
DATABASE_URL=sqlite:///./strava_portal.db
SECRET_KEY=your_random_secret_key
STRAVA_CLIENT_ID=your_id
STRAVA_CLIENT_SECRET=your_secret
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_key
LLM_MODEL=deepseek/deepseek-chat
```

### 2. Frontend Setup
```bash
cd frontend
npm install
```

### 3. Run Locally (Recommended)
```bash
./start_services.sh
```

## Project Status
See [PROJECT_OVERVIEW.md](./PROJECT_OVERVIEW.md) for the detailed status, recent fixes, and future roadmap.
