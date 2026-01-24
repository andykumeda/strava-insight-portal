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
- **Async Core:** Fully asynchronous MCP server (`httpx`) handles large data syncs with custom adaptive throttling (90 req / 15m).

### Features & Power Tools
- **Segment History & Analysis**: Successfully paginates Strava API to fetch *all* efforts (verified 13+ years).
- **GPX Export Support**: Users can now request and download Strava Routes as GPX files directly from the chat UI.
- **Smart Prioritization**: Recent activities (+500 score) and specific name matches (+200 score) are prioritized for detailed hydration.

---

## ⚠️ Known Limitations & Feature Requests

### 1. Full-History Private Note Search
**Status**: Limited Support
- **Issue**: The Strava List API does not return `private_note` or `description` fields.
- **Impact**: We cannot search *all* historical notes instantly without fetching details for every single activity.
- **Current Solution**: The system allows searching notes for **specific date ranges** or **recent activities** by dynamically fetching details on demand.

### 2. Recent Bug Fixes (Jan 23, 2026)
- **GPX Export Repair**: Fixed a series of backend proxy issues (`NameError`, `AsyncIteratorError`, `NoneType` headers) to ensure robust file serving.
- **Segment History Pagination**: Fixed broken MCP caching that previously limited results to page 1.
- **Sync Status**: Optimized speed (2s base sleep, bumped capacity). Progress is auto-saved to disk.
- **Systemd Removal**: Decommissioned unintended system services to restore full manual control via `start_services.sh`.
- **Auth Recovery**: Restored missing `.env` config and fixed redirect URI mismatches.

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
