# Project Overview & Status

**Last Updated**: January 25, 2026
**Current State**: ✅ Feature Complete & Production Ready

## 📋 Executive Summary
ActivityCopilot is a robust, secure, and performant application allowing users to query their Strava data using LLMs. It features a React frontend, highly optimized FastAPI backend, and an asynchronous MCP (Model Context Protocol) server for Strava integration. The system is in **Production** state with enterprise-grade security and modern infrastructure.

---

## 🚀 Architecture

### System Components
| Component | Port | Description |
|-----------|------|-------------|
| **Frontend** | 443 (nginx) | React + Vite app served via nginx |
| **Backend** | 8000 | FastAPI server handling auth, queries, LLM calls |
| **MCP Server** | 8001 | Strava API proxy with caching & rate limiting |

### MCP Server Endpoints (37 total)
The MCP server provides comprehensive Strava API coverage:

**Activities**: `/activities/recent`, `/activities/all`, `/activities/summary`, `/activities/search`, `/activities/{id}`, `/activities/{id}/map`, `/activities/{id}/streams`, `/activities/{id}/laps`, `/activities/{id}/comments`, `/activities/{id}/kudos`, `/activities/{id}/zones`

**Segments**: `/segments/{id}`, `/segments/{id}/efforts`, `/segments/{id}/leaderboard`, `/segments/{id}/streams`, `/segments/starred`, `/segment_efforts/{id}/streams`

**Routes**: `/routes`, `/routes/{id}`, `/routes/{id}/streams`, `/routes/{id}/export_gpx`, `/routes/{id}/export_tcx`

**Clubs**: `/clubs`, `/clubs/{id}`, `/clubs/{id}/activities`, `/clubs/{id}/members`, `/clubs/{id}/admins`

**Athlete**: `/athlete/stats`, `/athlete/zones`

**Gear**: `/gear/{id}`

**Write Operations**: `POST /activities`, `PUT /activities/{id}`, `PUT /athlete`, `PUT /segments/{id}/starred`

---

## 🚀 Recent Changes (Jan 25, 2026)

### API Quota Optimization
- **Disabled Background Hydration**: Removed all 4 automatic hydration triggers that were consuming 25+ API calls on startup
- **Rate Limit Updated**: Changed from 80/15m safety limit to official 100/15m
- **On-Demand Enrichment**: Activity details now fetched only when explicitly queried
- **Loading Message**: Frontend shows "Fetching activity details... this may take a moment" during queries

### Oldest-First Optimization
New `/activities/search` endpoint with intelligent fetching:
```
GET /activities/search?oldest_first=true&search_name=marathon&activity_type=Run
```
- Uses `after` parameter to fetch chronologically (oldest first)
- Enables early stopping for "first occurrence" queries
- Reduces API calls from 25 to 1-3 for historical searches

### Feature Parity
Added 18 new endpoints for full Strava API coverage:
- Activity streams, laps, comments, kudos
- Athlete zones
- Club details, activities, members, admins
- Route details, streams, TCX export
- Segment streams, effort streams
- Star/unstar segments
- Create/update activities
- Update athlete

### Bug Fixes
- Fixed `NameError` crashes from unescaped f-string curly braces in LLM prompts
- Fixed frontend loading indicator styling

---

## ⚠️ Known Limitations

### 1. Segment Search Requires Enriched Data
- **Issue**: Segment data is only available for activities that have been enriched
- **Current Solution**: Query by date first (e.g., "segments from my run yesterday"), which enriches the activity on-demand

### 2. Strava API Rate Limits
- **Limit**: 100 requests per 15 minutes, 1000 per day
- **Mitigation**: Rate limiter prevents exceeding limits; queries show remaining quota

### 3. Full-History Private Note Search
- **Issue**: Strava List API does not return `private_note` or `description` fields
- **Impact**: Cannot search all historical notes without fetching full details for every activity
- **Solution**: Search notes for specific date ranges or use on-demand enrichment

---

## 💡 How to Resume

1. **Start Services**: `./start_services.sh`
2. **Open App**: [https://activitycopilot.app](https://activitycopilot.app) or `http://localhost:5173`
3. **Check Status**: `curl http://localhost:8000/api/status`
4. **View Logs**: `tail -f backend_new.log mcp_new.log`

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|------------|
| **Frontend** | React, Vite, TailwindCSS |
| **Backend** | FastAPI, SQLAlchemy, Alembic |
| **MCP Server** | FastAPI, httpx (async), custom rate limiter |
| **Database** | SQLite (`strava_portal.db`) |
| **LLM** | OpenRouter API (configurable model) |
| **Auth** | OAuth 2.0, encrypted tokens (Fernet/AES), HTTP-only JWT cookies |
| **Deployment** | nginx reverse proxy, systemd optional |

---

## 📁 Project Structure

```
strava-activity-copilot/
├── backend/           # FastAPI backend (auth, queries, LLM)
├── frontend/          # React + Vite frontend
├── mcp-server/        # Strava MCP proxy server
├── deployment/        # nginx configs, systemd units
├── alembic/           # Database migrations
├── start_services.sh  # Service startup script
├── strava_cache.json  # Activity cache (auto-persisted)
└── rate_limit_state.json  # Rate limiter state
```

---

## 🎯 Future Roadmap
- [ ] **Per-User Rate Limiting**: Key rate limiter by athlete_id for true multi-tenancy
- [ ] **Scheduled Sync**: Optional overnight hydration for pre-cached data
- [ ] **Data Export**: Allow users to export enriched data as JSON/CSV
- [ ] **Saved Queries**: Save complex comparison queries as dashboard widgets
