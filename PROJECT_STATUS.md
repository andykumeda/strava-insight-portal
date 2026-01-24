# Project Status & Roadmap

**Last Updated:** January 19, 2026

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

---

## ⚠️ Known Limitations

### Full-History Private Note Search
**Status**: Limited Support
- **Issue**: The Strava List API does not return `private_note` or `description` fields.
- **Impact**: We cannot search *all* historical notes instantly without fetching details for every single activity.
- **Current Solution**: The system allows searching notes for **specific date ranges** or **recent activities** by dynamically fetching details on demand.

### Segment History & Analysis
**Status**: ✅ Completed
- **Full History**: Successfully paginates Strava API to fetch *all* efforts (verified 13+ years).
- **Rich Context**: Links efforts to specific activities with names (e.g. "Morning Run") vs generic labels.
- **Accuracy**: Fixed caching bugs that previously limited results to page 1 (~100 items).
- **Limitations**: Counts may be slightly lower than Strava website total due to hidden/private activities (API returns only viewable efforts).

---

## 🛠️ Future Maintenance

### Medium Priority
- **Caching Strategy**: Move from in-memory `ACTIVITY_CACHE` to Redis or DiskCache for persistence across restarts.
- **Code Quality**: Add comprehensive type hints and unit tests (pytest).

### Low Priority
- **Monitoring**: Add structured logging and Prometheus metrics.
- **Documentation**: Expose Swagger UI `/docs` publicly if needed.

---

## 📚 Reference: Environment Setup
Ensure your `.env` includes:
```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_key
LLM_MODEL=deepseek/deepseek-chat
ALLOWED_ORIGINS=http://localhost:5173
LOG_LEVEL=INFO
DATABASE_URL=sqlite:///./strava_portal.db
SECRET_KEY=your_secure_key
```
