
import httpx
import asyncio
import json
import base64
import hashlib
from cryptography.fernet import Fernet

# Config
SECRET_KEY = "change_this_to_a_secure_random_key_in_production"
ENCRYPTED_TOKEN = "gAAAAABpcaQesQKP1ucfqd27n-VB9ecC81zxT6tkFLHEaaC2qHmhvzNspuWKGhwMw6thGBkkAz3-61fIQAhkAdrtYw7IqSSrk7_mZfKBMsDkke6uWByyhVbihFw5SQ6O00XUQw0mVeXW"

def decrypt_token():
    key = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest())
    f = Fernet(key)
    return f.decrypt(ENCRYPTED_TOKEN.encode()).decode()

async def main():
    try:
        token = decrypt_token()
        print(f"Decrypted Token: {token[:10]}...")
    except Exception as e:
        print(f"Decryption failed: {e}")
        return

    async with httpx.AsyncClient() as client:
        print("Fetching activities summary from MCP...")
        try:
            resp = await client.get("http://localhost:8001/activities/summary", headers={"X-Strava-Token": token})
            if resp.status_code != 200:
                print(f"Error: {resp.status_code} - {resp.text}")
                return

            data = resp.json()
            activities_map = data.get("activities_by_date", {})
            
            print(f"Found {len(activities_map)} days with activities.")
            
            print("\n--- Inspecting Activity on Jan 18, 2026 ---")
            found_jan18 = False
            for date_key, acts in activities_map.items():
                if date_key.startswith("2026-01-18"):
                    for act in acts:
                        found_jan18 = True
                        print(f"MATCH: [{act['start_time']}] {act['name']} (ID: {act['id']})")
                        print(f"   Route Match Count (Cache): {act.get('route_match_count')}")
                        print(f"   Hydrated?: {'hydrated_at' in act}")
                        
                        # Fetch live details to compare
                        try:
                            d_resp = await client.get(f"http://localhost:8001/activities/{act['id']}", headers={"X-Strava-Token": token})
                            if d_resp.status_code == 200:
                                d_data = d_resp.json()
                                sim = d_data.get('similar_activities')
                                print(f"   Live Similar Activities: {sim}")
                            else:
                                print(f"   Failed to fetch details: {d_resp.status_code}")
                        except Exception as e:
                            print(f"   Error: {e}")
            
            if not found_jan18:
                print("No activity found on 2026-01-18.")

        except Exception as e:
            print(f"Request failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
