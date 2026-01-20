import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./strava_portal.db"
    
    # Strava API
    STRAVA_CLIENT_ID: str = ""
    STRAVA_CLIENT_SECRET: str = ""
    REDIRECT_URI: str = "http://localhost:8000/api/auth/strava/callback"
    
    # URLs
    FRONTEND_URL: str = "http://localhost:5173"
    MCP_SERVER_URL: str = "http://localhost:8001"
    
    # Security
    SECRET_KEY: str = "change_this_to_a_secure_random_key_in_production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    
    # LLM
    LLM_PROVIDER: str = "openrouter" # openrouter, gemini, openai
    LLM_MODEL: str = "google/gemini-flash-1.5"
    OPENROUTER_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
