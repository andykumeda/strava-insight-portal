import httpx
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .database import get_db
from .models import User, Token
from .config import settings
from .security import create_access_token

router = APIRouter()

MCP_SERVER_URL = settings.MCP_SERVER_URL

@router.post("/strava/start")
def start_strava_auth():
    """
    Returns the Strava OAuth URL. 
    Frontend should redirect the user to this URL.
    """
    if not settings.STRAVA_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Server misconfiguration: Missing STRAVA_CLIENT_ID")
    
    params = {
        "client_id": settings.STRAVA_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.REDIRECT_URI,
        "approval_prompt": "force",
        "scope": "activity:read_all,profile:read_all",
    }
    
    # Construct query string
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    auth_url = f"https://www.strava.com/oauth/authorize?{query_string}"
    
    return {"url": auth_url}

@router.get("/strava/callback")
async def strava_callback(code: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Handle Strava OAuth callback.
    Exchange code for tokens, create/update user, and redirect to frontend.
    """
    if not settings.STRAVA_CLIENT_ID or not settings.STRAVA_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Server misconfiguration")

    # Exchange code for token
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": settings.STRAVA_CLIENT_ID,
                "client_secret": settings.STRAVA_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Failed to exchange token: {response.text}")
        
    token_data = response.json()
    athlete_data = token_data.get("athlete", {})
    
    # Extract data
    strava_id = athlete_data.get("id")
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_at = token_data.get("expires_at")
    
    if not strava_id:
        raise HTTPException(status_code=400, detail="Invalid response from Strava")

    # DB Operations
    user = db.query(User).filter(User.strava_athlete_id == strava_id).first()
    if not user:
        user = User(
            strava_athlete_id=strava_id,
            name=f"{athlete_data.get('firstname')} {athlete_data.get('lastname')}",
            profile_picture=athlete_data.get("profile")
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # Update profile info if changed
        user.name = f"{athlete_data.get('firstname')} {athlete_data.get('lastname')}"
        user.profile_picture = athlete_data.get("profile")
        db.add(user)
        db.commit()

    # Save/Update Tokens
    token_entry = db.query(Token).filter(Token.user_id == user.id).first()
    if not token_entry:
        token_entry = Token(
            user_id=user.id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope="activity:read_all,profile:read_all"
        )
        db.add(token_entry)
    else:
        token_entry.access_token = access_token
        token_entry.refresh_token = refresh_token
        token_entry.expires_at = expires_at
        db.add(token_entry)
    
    db.commit()

    # Create a secure session token (JWT)
    session_token = create_access_token(data={"sub": str(user.id)})

    # Redirect to Frontend and set the secure cookie
    response = RedirectResponse(url=f"{settings.FRONTEND_URL}/?connected=true")
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=not settings.FRONTEND_URL.startswith("http://"), # True in prod, False for http://localhost
        samesite="Lax", # Lax is suitable for OAuth redirects
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )

    # Trigger background data fetch to warm up cache on the MCP server
    background_tasks.add_task(trigger_mcp_refresh, access_token)

    return response

async def trigger_mcp_refresh(access_token: str):
    """Fire-and-forget request to the MCP server to start caching activities."""
    async with httpx.AsyncClient() as client:
        try:
            # Use a short timeout as we don't need to wait for the full response
            await client.post(
                f"{MCP_SERVER_URL}/activities/refresh",
                headers={"X-Strava-Token": access_token},
                timeout=2.0
            )
        except httpx.ReadTimeout:
            # This is expected and okay. The request was sent.
            pass
        except Exception as e:
            # Log if the MCP server is down or rejects the request
            import logging
            logging.getLogger(__name__).error(f"Failed to trigger MCP refresh: {e}")

from .deps import get_current_user

@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "name": user.name,
        "strava_id": user.strava_athlete_id,
        "profile_picture": user.profile_picture,
        "connected": True
    }
