
"""
MCP Tool Definitions for Strava Activity Copilot.
These tools are exposed to the LLM (Gemini/OpenAI) to enable the agent loop.
"""

# Tool Definitions (OpenAI/Gemini function calling compatible format)
STRAVA_TOOLS = [
    {
        "name": "get_activities_summary",
        "description": "Get a summary of activities grouped by year and month. Use this to find available date ranges or activity counts.",
        "parameters": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": "Optional year to filter by (e.g., 2025)"
                }
            },
            "required": []
        }
    },
    {
        "name": "search_activities",
        "description": "Search for activities using filters. Returns a list of matching activities with basic details.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search in activity name (e.g., 'Morning Run', 'Wilson')"
                },
                "after": {
                    "type": "string",
                    "description": "Filter activities after date (YYYY-MM-DD)"
                },
                "before": {
                    "type": "string",
                    "description": "Filter activities before date (YYYY-MM-DD)"
                },
                "activity_type": {
                    "type": "string",
                    "enum": ["Run", "Ride", "Swim", "Hike", "Walk"],
                    "description": "Filter by activity type"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 10, max 50)"
                }
            },
            # At least one filter is recommended but not strictly required by schema
            "required": []
        }
    },
    {
        "name": "get_activity_details",
        "description": "Get FULL detailed data for a specific activity, including splits, laps, same_routes, etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "activity_id": {
                    "type": "integer",
                    "description": "The unique ID of the activity (e.g., 123456789)"
                }
            },
            "required": ["activity_id"]
        }
    },
    {
        "name": "get_segment_details",
        "description": "Get details for a specific segment, including leaderboard and user's history.",
        "parameters": {
            "type": "object",
            "properties": {
                "segment_id": {
                    "type": "integer",
                    "description": "The unique ID of the segment"
                }
            },
            "required": ["segment_id"]
        }
    },
    {
        "name": "sync_activities",
        "description": "Force a synchronization of the latest activities from Strava. Use this when the user asks for 'today', 'latest', or 'morning' run and it is not found in the summary.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

def get_tool_definitions():
    """Return list of available tools."""
    return STRAVA_TOOLS
