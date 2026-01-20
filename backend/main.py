import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from .limiter import limiter
from .database import engine, Base
from .auth import router as auth_router
from .routes import router as api_router

# Create tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Strava Insight Portal")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Strava-Token"],
)

# Include routers
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(api_router, prefix="/api", tags=["api"])

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import logging
    import traceback
    error_msg = f"Global Exception: {str(exc)}\n{traceback.format_exc()}"
    # Print to stdout/stderr to ensure systemd captures it
    print(error_msg)
    logging.getLogger("uvicorn.error").error(error_msg)
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error. Check logs for traceback."},
    )

@app.get("/")
def read_root():
    return {"message": "Strava Insight Portal API is running"}
