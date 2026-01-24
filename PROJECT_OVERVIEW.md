# Project Overview & Status

**Date**: January 23, 2026
**Current State**: ✅ Feature Complete & Production Ready

## 📋 Executive Summary
The ActivityCopilot is a robust, secure, and performant application allowing users to query their Strava data using LLMs. It features a React frontend, highly optimized FastAPI backend, and an asynchronous MCP server for Strava integration. The system is in a **Completed MVP** state with enterprise-grade security and modern infrastructure.

---

## 🚀 Accomplishments & Features
### Security & Infrastructure
- **Encryption:** Strava tokens are encrypted at rest (Fernet/AES) in the SQLite/PostgreSQL database.
- **Authentication:** Secure, HTTP-only, signed JWT cookies (`session_token`) replace legacy auth.
- **Migrations:** `Alembic` database migrations are fully configured.
- **Rate Limiting:** `slowapi` protects API endpoints.
- **Async Core:** Fully asynchronous MCP server (`httpx`) handles large data syncs without blocking.

### Smart Querying
- **Summary-First Logic:** Comparison queries (e.g., "runs in 2024 vs 2025") use lightweight summaries for sub-second responses.
- **Detail Enrichment:** Questions about "notes" or specific keywords automatically trigger a fetch of detailed activity data (including `private_note`) for relevant subsets.
- **Interactive UI:** Supports command history (Up/Down arrows) and async type-ahead.

### Segment History & Analysis (New!)
- **Full History**: Successfully paginates Strava API to fetch *all* efforts (verified 13+ years).
- **Rich Context**: Links efforts to specific activities with names (e.g. "Sunday Long Run") vs generic labels.
- **Accuracy**: Fixed caching bugs in MCP server that previously limited results to page 1 (~100 items).
- **Limitations**: Counts may be slightly lower than Strava website total due to hidden/private activities (API returns only viewable efforts).

---

## ⚠️ Known Limitations & Feature Requests

### 1. Full-History Private Note Search
**Status**: Limited Support
- **Issue**: The Strava List API does not return `private_note` or `description` fields.
- **Impact**: We cannot search *all* historical notes instantly without fetching details for every single activity.
- **Current Solution**: The system allows searching notes for **specific date ranges** or **recent activities** by dynamically fetching details on demand.

### 2. Recent Bug Fixes (Jan 23, 2026)
- **Segment Counts**: Fixed broken MCP caching that prevented pagination. Counts are now accurate (e.g. 126 vs 46).
- **Activity Names**: Updated `routes.py` to inject real activity names into segment effort lists.
- **Sync Status**: Fixed stalled "hydration" (sync) progress. The system now automatically resumes background enrichment when the dashboard status is polled.
- **Stability**: Resolved "Address already in use" errors with robust start script.

### 3. Future Roadmap
- [ ] **Background Sync**: Implement a detailed crawler to fetch full activity details (including notes) for the entire history into a local DB.
- [ ] **Data Export**: Allow users to export their joined/enriched data as JSON/CSV.
- [ ] **Saved Queries**: Allow saving complex comparison queries as dashboard widgets.

---

## 💡 How to Resume
1. **Start Services**: `./start_services.sh`
2. **Open App**: [https://activitycopilot.app](https://activitycopilot.app) or `http://localhost:5173`
3. **Check Status**: `curl http://localhost:8000/api/status`

## 🛠️ Tech Stack & Services
- **Backend Service**: `strava-backend.service` (Port 8000)
- **MCP Service**: `strava-mcp.service` (Port 8001)
- **Database**: SQLite (`strava_portal.db`) with Alembic migrations
- **Logs**: `sudo journalctl -u strava-backend.service -f`
