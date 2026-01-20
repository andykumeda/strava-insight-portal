# ActivityCopilot

A production-ready web application that lets users log in with Strava and ask natural-language questions about their activity data using LLMs.

## Features

### 🧠 Intelligent Querying
- **Natural Language**: Ask questions like "How many miles did I run in 2025 vs 2024?" or "Show me runs where I mentioned 'pain' in the notes."
- **Smart Context**: Dynamically switches between summary data (for fast aggregates) and detailed activity records (for specific searches) to optimize performance and cost.
- **Private Notes Search**: Supports searching your activity notes and descriptions for keywords (e.g., "race", "injury", "easy").
- **Enriched Activity Details**: Automatically pulls top segments and formats them with clickable links, exact times, and structured headers when you ask for details about a specific run.

### ⚡ Performance & Scale
- **Full History Access**: Fetches and caches your entire Strava activity history (thousands of activities).
- **Instant Comparisons**: Aggregate queries (e.g., monthly totals) are processed instantly using pre-computed summaries.
- **Async Architecture**: Fully asynchronous backend prevents blocking during large data syncs.
- **Robust Rate Handling**: Automatically handles Strava API rate limits (429 errors) with retries and exponential backoff.

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
- **Rich Formatting**: AI responses formatted with Markdown bullet points and bold text.

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

### 3. Run Locally
**Backend:**
```bash
source venv/bin/activate
uvicorn backend.main:app --port 8000 --reload
```

**MCP Server:**
```bash
source venv/bin/activate
python mcp-server/src/strava_http_server.py
```

**Frontend:**
```bash
cd frontend
npm run dev
```

## Project Status

See [PROJECT_STATUS.md](./PROJECT_STATUS.md) for the detailed roadmap and completed tasks.
See [FEATURE_REQUESTS.md](./FEATURE_REQUESTS.md) for known limitations and future ideas.
