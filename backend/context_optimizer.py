"""
Smart context optimization for Gemini API.
Handles context limits, token counting, and intelligent data filtering.
"""
import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import dateparser
from dateparser.search import search_dates


class ContextOptimizer:
    """
    Optimizes context sent to Gemini to:
    1. Prevent context limit errors
    2. Minimize token usage (cost)
    3. Ensure all historical data is accessible
    """
    
    # Token estimates (rough approximations)
    # Gemini 2.0 Flash has ~1M token context, but we want to stay well under
    MAX_CONTEXT_TOKENS = 150000  # 150k for Gemini 2.0 stability
    TOKEN_OVERHEAD = 500  # Prompt overhead
    
    # Token estimates per item
    TOKENS_PER_ACTIVITY = 50  # Condensed activity format
    TOKENS_PER_SUMMARY_ENTRY = 20
    TOKENS_PER_STATS_ENTRY = 30
    
    def __init__(self, question: str, activity_summary: Dict[str, Any], stats: Dict[str, Any]):
        self.question = question.lower()
        self.activity_summary = activity_summary
        self.stats = stats
        self.by_year = activity_summary.get("by_year", {})
        self.activities_by_date = activity_summary.get("activities_by_date", {})
        
    def estimate_tokens(self, data: Any) -> int:
        """Rough token estimation by JSON string length."""
        json_str = json.dumps(data, separators=(',', ':'))
        # Rough estimate: ~4 characters per token
        return len(json_str) // 4
    
    def parse_date_range(self) -> Optional[Tuple[datetime, datetime]]:
        """
        Parse natural language dates from question.
        Returns (start_date, end_date) or None if can't determine.
        """
        question_lower = self.question.lower()
        print(f"ContextOptimizer: Parsing date from '{self.question}'")
        
        # PRIORITY 0: Handle relative date phrases like "this month", "this week", "last month"
        now = datetime.now()
        
        if 'this month' in question_lower:
            start = datetime(now.year, now.month, 1)
            # End = last day of current month (approximated by next month - 1 day)
            if now.month == 12:
                end = datetime(now.year + 1, 1, 1) - timedelta(seconds=1)
            else:
                end = datetime(now.year, now.month + 1, 1) - timedelta(seconds=1)
            print(f"ContextOptimizer: Parsed 'this month' -> {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
            return (start, end)
        
        if 'last month' in question_lower:
            if now.month == 1:
                start = datetime(now.year - 1, 12, 1)
                end = datetime(now.year, 1, 1) - timedelta(seconds=1)
            else:
                start = datetime(now.year, now.month - 1, 1)
                end = datetime(now.year, now.month, 1) - timedelta(seconds=1)
            print(f"ContextOptimizer: Parsed 'last month' -> {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
            return (start, end)
            
        if 'this week' in question_lower:
            # Start of week (Monday)
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59)
            print(f"ContextOptimizer: Parsed 'this week' -> {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
            return (start, end)
            
        if 'last week' in question_lower:
            # Monday of last week
            start = now - timedelta(days=now.weekday() + 7)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
            print(f"ContextOptimizer: Parsed 'last week' -> {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
            return (start, end)
        
        # PRIORITY 1: Check for explicit years FIRST (e.g., "2025", "2024-2025")
        # This prevents dateparser from interpreting "2025" as a specific date like Jan 27, 2025
        years = re.findall(r'\b(20\d{2})\b', self.question)
        # Only use year regex if no specific month is mentioned (e.g. "January 2025" should use dateparser)
        month_names = ['january', 'february', 'march', 'april', 'may', 'june', 
                       'july', 'august', 'september', 'october', 'november', 'december']
        has_specific_month = any(m in question_lower for m in month_names)
        
        if years and not has_specific_month:
            years_int = [int(y) for y in years]
            start_year = min(years_int)
            end_year = max(years_int)
            print(f"ContextOptimizer: Parsed explicit years: {start_year}-{end_year}")
            return (
                datetime(start_year, 1, 1),
                datetime(end_year, 12, 31, 23, 59, 59)
            )
        
        # Check for Month names or specific date formats 
        # Also check for relative triggers like "on this day", "ago", "today"
        months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        triggers = ['on this day', 'ago', 'today', 'yesterday', 'tomorrow']
        
        has_trigger = any(t in question_lower for t in triggers)
        has_month = any(m in question_lower for m in months)
        
        print(f"ContextOptimizer: has_trigger={has_trigger}, has_month={has_month}")
        
        if has_trigger or has_month or '/' in self.question or '-' in self.question:
             # Handle "on this day" explicitly first
             if "on this day" in question_lower:
                 try:
                     now = datetime.now()
                     target_date = now
                     
                     if "last year" in question_lower:
                         target_date = now.replace(year=now.year - 1)
                     elif "2 years ago" in question_lower:
                         target_date = now.replace(year=now.year - 2)
                     elif "3 years ago" in question_lower:
                         target_date = now.replace(year=now.year - 3)
                     
                     start = target_date.replace(hour=0, minute=0, second=0)
                     end = target_date.replace(hour=23, minute=59, second=59)
                     print(f"ContextOptimizer: Parsed 'on this day': {start} to {end}")
                     return (start, end)
                 except Exception as e:
                     print(f"ContextOptimizer: 'on this day' parsing error: {e}")
             
             try:
                clean_q = question_lower
                
                # Check for "between X and Y" explicit range
                between_match = re.search(r'between\s+(.+?)\s+and\s+(.+)', clean_q)
                if between_match:
                    d1_str = between_match.group(1).strip()
                    d2_str = between_match.group(2).strip()
                    print(f"ContextOptimizer: Detected 'between' range: '{d1_str}' and '{d2_str}'")
                    
                    d1 = dateparser.parse(d1_str, settings={'PREFER_DATES_FROM': 'past', 'STRICT_PARSING': False})
                    d2 = dateparser.parse(d2_str, settings={'PREFER_DATES_FROM': 'past', 'STRICT_PARSING': False})
                    
                    if d1 and d2:
                        # Ensure chronological order
                        if d1 > d2:
                            d1, d2 = d2, d1
                        
                        start = d1.replace(hour=0, minute=0, second=0)
                        end = d2.replace(hour=23, minute=59, second=59)
                        print(f"ContextOptimizer: Parsed 'between' range: {start} to {end}")
                        return (start, end)
                
                # Cleanup possessives and noise words
                clean_q = clean_q.replace("'s", "").replace("’s", "")
                
                # Remove common non-date words that confuse dateparser
                noise_words = [
                    "report", "show me", "my", "activity", "activities", "stats", "summary",
                    "what were", "what were the", "what was", "what about", "list", "tell me about",
                    "what did i do", "what did i", "what was my", "what were my", "did i do", "did i",
                    "on", "at", "for", "the", "a"
                ]
                for word in noise_words:
                    # Use regex to match whole words only to avoid eating "monday" -> "mon"
                    clean_q = re.sub(rf'\b{re.escape(word)}\b', '', clean_q).strip()
                
                # Strip distinct prefixes again just in case
                for prefix in ["show me activities on", "show matches for", "activities on", "what happened on", "records for"]:
                     if prefix in clean_q:
                        clean_q = clean_q.replace(prefix, "").strip()
                
                # Final cleanup of punctuation
                clean_q = clean_q.replace("?", "").replace(".", "").strip()

                print(f"ContextOptimizer: Attempting dateparser on cleaned '{clean_q}'", flush=True)
                
                parsed = None
                if clean_q:
                    # Use search_dates to find dates embedded in text
                    found = search_dates(clean_q, settings={'PREFER_DATES_FROM': 'past', 'STRICT_PARSING': False})
                    
                    valid_date = None
                    if found:
                        # Filter out noise (e.g., "16th running", "time")
                        for match_text, date_obj in found:
                            match_lower = match_text.lower()
                            
                            # Ignore specific noise words
                            if match_lower in ['time', 'date', 'stats', 'runs']:
                                continue
                                
                            # Ignore bare ordinals if they look like edition numbers (e.g. "16th", "1st")
                            # unless they clearly look like part of a date context which dateparser usually captures as a longer string
                            # If the match is JUST "16th" and followed by "running/edition", ignore it.
                            # Ignore bare ordinals if they look like edition numbers (e.g. "16th", "1st")
                            # If the match is strictly digits + suffix, and dateparser didn't grab surrounding context (like "of January"),
                            # then it is almost certainly NOT a date intent in this context (usually an edition or rank).
                            is_ordinal = re.match(r'^\d+(st|nd|rd|th)$', match_lower)
                            if is_ordinal:
                                print(f"ContextOptimizer: Ignoring ordinal date '{match_text}' (aggressive filter)")
                                continue
                                    
                            # Determine if this looks valid
                            print(f"ContextOptimizer: Found potential date '{match_text}' -> {date_obj}")
                            valid_date = date_obj
                            break # Use first valid date
                            
                    if valid_date:
                        print(f"ContextOptimizer: Accepted date: {valid_date}")
                        parsed = valid_date
                    else:
                        # Fallback to direct parse just in case
                        parsed = dateparser.parse(clean_q, settings={'PREFER_DATES_FROM': 'past', 'STRICT_PARSING': False})
                
                # Fallback: If dateparser fails but we see relative time phrases
                if not parsed:
                    now = datetime.now()
                    if "this morning" in clean_q or "today" in clean_q:
                        parsed = now
                    elif "yesterday" in clean_q:
                        parsed = now - timedelta(days=1)
                    elif "a few days ago" in clean_q:
                        parsed = now - timedelta(days=3)
                    elif "last week" in clean_q:
                        parsed = now - timedelta(days=7)

                
                if parsed:
                    # If a single date, return that day
                    start = parsed.replace(hour=0, minute=0, second=0)
                    end = parsed.replace(hour=23, minute=59, second=59)
                    print(f"ContextOptimizer: Parsed specific date: {start} to {end}", flush=True)
                    return (start, end)
                else:
                    print("ContextOptimizer: dateparser returned None", flush=True)
             except Exception as e:
                print(f"ContextOptimizer: Date parsing failed: {e}")
                pass

        # Check for explicit years
        years = re.findall(r'\b(20\d{2})\b', self.question)
        if years:
            years_int = [int(y) for y in years]
            start_year = min(years_int)
            end_year = max(years_int)
            print(f"ContextOptimizer: Parsed years: {start_year}-{end_year}")
            return (
                datetime(start_year, 1, 1),
                datetime(end_year, 12, 31, 23, 59, 59)
            )
        
        # Check for "all time", "everything", "all activities"
        if any(phrase in question_lower for phrase in ['all time', 'everything', 'all activities', 'entire', 'complete']):
            # Return None to indicate all data
            return None
        
        # Check for "last year", "this year", etc.
        if 'last year' in question_lower:
            now = datetime.now()
            return (datetime(now.year - 1, 1, 1), datetime(now.year - 1, 12, 31, 23, 59, 59))
        
        if 'this year' in question_lower or 'current year' in question_lower:
            now = datetime.now()
            return (datetime(now.year, 1, 1), datetime(now.year, 12, 31, 23, 59, 59))
        
        # Check for "last N months/weeks/days"
        months_match = re.search(r'last (\d+) months?', question_lower)
        if months_match:
            months = int(months_match.group(1))
            end = datetime.now()
            start = end - timedelta(days=months * 30)
            return (start, end)
        
        weeks_match = re.search(r'last (\d+) weeks?', question_lower)
        if weeks_match:
            weeks = int(weeks_match.group(1))
            end = datetime.now()
            start = end - timedelta(weeks=weeks)
            return (start, end)
        
        days_match = re.search(r'last (\d+) days?', question_lower)
        if days_match:
            days = int(days_match.group(1))
            end = datetime.now()
            start = end - timedelta(days=days)
            return (start, end)
        
        # Try dateparser for specific dates
        # Try dateparser for specific dates (fallback)
        try:
            found = search_dates(self.question, settings={'PREFER_DATES_FROM': 'past'})
            if found:
                # Apply same filtering logic
                for match_text, date_obj in found:
                    match_lower = match_text.lower()
                    if match_lower in ['time', 'date']:
                        continue
                    if re.match(r'^\d+(st|nd|rd|th)$', match_lower):
                         continue
                    
                    parsed = date_obj
                    # If a single date, return that day
                    return (parsed.replace(hour=0, minute=0, second=0), 
                           parsed.replace(hour=23, minute=59, second=59))
        except Exception:
            pass
        
        # Default: return None (will use summary-only approach)
        return None
    
    def filter_activities_by_date_range(self, start_date: Optional[datetime], 
                                       end_date: Optional[datetime]) -> List[Dict[str, Any]]:
        """Filter activities by date range. Optimized to use string comparison."""
        if start_date is None and end_date is None:
            # All activities requested
            all_activities = []
            for date_str, activities in self.activities_by_date.items():
                for activity in activities:
                    activity['date'] = date_str
                    all_activities.append(activity)
            return all_activities
        
        filtered = []
        # Convert bounds to string YYYY-MM-DD for fast comparison
        start_str = start_date.strftime("%Y-%m-%d") if start_date else None
        end_str = end_date.strftime("%Y-%m-%d") if end_date else None
        
        print(f"ContextOptimizer: Filtering range {start_str} -> {end_str}")
        
        for date_str, activities in self.activities_by_date.items():
            # Fast string comparison (lexicographical order works for ISO dates)
            if start_str and date_str < start_str:
                continue
            if end_str and date_str > end_str:
                continue
            
            for activity in activities:
                activity['date'] = date_str
                filtered.append(activity)
        
        print(f"ContextOptimizer: Filtered {len(filtered)} activities from {len(self.activities_by_date)} days.")
        return filtered
    
    def calculate_relevance(self, activity: Dict[str, Any]) -> Tuple[int, str]:
        """
        Calculate relevance score for an activity against the query.
        Returns tuple (score, start_time) for sorting.
        """
        score = 0
        name = str(activity.get('name', '')).lower()
        note = str(activity.get('private_note', '')).lower()
        desc = str(activity.get('description', '')).lower()
        full_text = f"{name} {note} {desc}"
        
        # Keywords from query (excluding stop words)
        stop_words = {'what', 'was', 'the', 'list', 'all', 'segments', 'from', 'at', 'in', 'on', 'my', 'run', 'ride', 'how', 'many', 'activities', 'have', 'been', 'of', 'exactly', 'did', 'do'}
        question_words = [w.lower() for w in re.findall(r'\w+', self.question)]
        query_words = [w for w in question_words if w not in stop_words]
        
        for w in query_words:
            if w in full_text:
                score += 10
        
        # Distance matching score
        dist = activity.get('distance_miles', 0)
        for w in query_words:
            if w.replace('.', '', 1).isdigit():
                try:
                    target_dist = float(w)
                    diff = abs(dist - target_dist)
                    if diff < 0.01:
                        score += 200 # Exact match priority
                    elif diff < 0.1:
                        score += 100 # Near match
                    elif diff < 0.3:
                        score += 50
                except ValueError:
                    pass
                    
        # Recency Match (Critical for "my run today")
        try:
            start_str = activity.get('start_date', '') or activity.get('start_time', '')
            if start_str:
                act_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                now = datetime.now(act_dt.tzinfo)
                delta = now - act_dt
                if delta.total_seconds() < 86400: # Last 24 hours
                    score += 1000 # Absolute Priority
                elif delta.days < 7:
                    score += 100 # This week
        except Exception: pass
                    
        return (score, activity.get('start_time', ''))

    def filter_by_keyword(self, activities: List[Dict[str, Any]], date_range_applied: bool = False) -> List[Dict[str, Any]]:
        """Filter activities by keywords found in quotes or after 'with'/'contains'.
        
        Args:
            activities: List of activities to filter
            date_range_applied: If True, skip aggressive keyword extraction to avoid filtering by date components
        """
        question_lower = self.question.lower()
        
        # Extract potential keywords
        keywords = []
        
        # 1. Quoted strings (e.g., "pain", 'easy run')
        quotes = re.findall(r"['\"](.*?)['\"]", question_lower)
        keywords.extend(quotes)
        
        # 2. Simple "with <word>" detection
        if not keywords:
            match = re.search(r"(?:contain\w*|mention\w*|note\w*|desc\w*|say\w*|with)\s+(?:for|about|that)?\s*['\"]?(\w+)['\"]?", question_lower)
            if match:
                word = match.group(1)
                blacklist = ['activities', 'runs', 'rides', 'the', 'a', 'an', 'my', 'me', 'in', 'on']
                if word not in blacklist:
                    keywords.append(word)
        
        # 3. Aggressive extraction: ONLY if no date range was applied
        # This prevents date components (like "18", "2025") from being extracted as keywords
        if not keywords and not date_range_applied:
            # Avoid single stop words, but catch numbers and capitalized names
            words = re.findall(r'\b(?:\d+\.?\d*|[A-Z]\w+)\b', self.question)
            blacklist = ['The', 'What', 'How', 'List', 'Show', 'This', 'That', 'These', 'Those']
            for w in words:
                if w not in blacklist and len(w) >= 1:
                    keywords.append(w.lower())
        
        if not keywords:
            return activities
            
        print(f"ContextOptimizer: Filtering by keywords: {keywords}")
        filtered = []
        for activity in activities:
            # Include distance and other numeric fields in searchable text
            dist = activity.get('distance_miles', 0)
            elev = activity.get('elevation_feet', 0)
            
            text_content = (
                str(activity.get('name', '')) + " " + 
                str(activity.get('private_note', '')) + " " + 
                str(activity.get('description', '')) + " " +
                f"{dist} miles {elev} feet"
            ).lower()
            
            is_match = False
            for kw in keywords:
                # Handle numeric matches (e.g. "5" matches "5.02")
                if kw.isdigit() or (kw.replace('.', '', 1).isdigit() and kw.count('.') <= 1):
                    try:
                        f_kw = float(kw)
                        if abs(dist - f_kw) < 0.2: # 0.2 mile tolerance
                            is_match = True
                            break
                    except ValueError:
                        pass
                
                if kw in text_content:
                    is_match = True
                    break
            
            if is_match:
                filtered.append(activity)
                
        print(f"ContextOptimizer: Keyword filter reduced {len(activities)} to {len(filtered)} activities")
        return filtered

    def optimize_context(self) -> Dict[str, Any]:
        """
        Main optimization function.
        Returns optimized context that:
        1. Stays within token limits
        2. Includes all necessary data
        3. Uses summaries when appropriate
        """
        # Initial context (stats only)
        # Scrub statistical data of unwanted fields and convert units (meters -> miles/feet)
        scrubbed_stats = {}
        if self.stats:
            # key map: original_key -> (new_key, unit_conversion_factor)
            # distance (meters) -> miles (1/1609.34)
            # elevation (meters) -> feet (3.28084)
            # moving_time (seconds) -> kept as seconds but add human readable
            
            print(f"ContextOptimizer: Processing stats keys: {list(self.stats.keys())}")
            for key, val in self.stats.items():
                if key == 'athlete': continue # Too much detail
                
                # Rename "all_run_totals" -> "lifetime_run_totals" to be explicit
                new_key = key.replace("all_", "lifetime_").replace("recent_", "last_4_weeks_")
                
                if isinstance(val, dict):
                    new_val = val.copy()
                    # Convert distance
                    if 'distance' in new_val:
                        new_val['distance_miles'] = round(new_val.pop('distance', 0) / 1609.344, 1)
                    # Convert elevation
                    if 'elevation_gain' in new_val:
                        new_val['elevation_feet'] = round(new_val.pop('elevation_gain', 0) * 3.28084, 0)
                    # Format time
                    if 'moving_time' in new_val:
                        m, s = divmod(new_val['moving_time'], 60)
                        h, m = divmod(m, 60)
                        new_val['moving_time_display'] = f"{h}h {m}m"
                        
                    scrubbed_stats[new_key] = new_val
                else:
                    scrubbed_stats[new_key] = val
        
        optimized = {
            "lifetime_stats": scrubbed_stats,
        }
        
        def scrub_activity(act):
            """Remove fields that encourage bad LLM habits (maps, etc)"""
            exclude = ['map', 'polyline', 'summary_polyline', 'resource_state', 'external_id', 'upload_id']
            act_copy = {k: v for k, v in act.items() if k not in exclude}
            
            # Scrub map/localhost keywords from any string field
            for key, val in act_copy.items():
                if isinstance(val, str) and val:
                    val = re.sub(r'https?://(?:localhost|127\.0\.0\.1|8001).*?(?=\s|$|\"|\))', '[REMOVED]', val)
                    val = re.sub(r'View Interactive Map', '[REMOVED]', val)
                    act_copy[key] = val
            return act_copy

        
        # Determine what level of detail is needed
        date_range = self.parse_date_range()
        
        # Get filtered activities (Date Range)
        if date_range:
            start_date, end_date = date_range
            relevant_activities = self.filter_activities_by_date_range(start_date, end_date)
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"ContextOptimizer: Date range {start_date} to {end_date} matched {len(relevant_activities)} activities")
        else:
            # All activities requested
            relevant_activities = self.filter_activities_by_date_range(None, None)
            
        # Apply Keyword Filtering (Content)
        # This allows "find runs with 'pain'" or "how many runs mention '16th'" to work over history
        # Pass date_range_applied=True if we already filtered by date to prevent date components from being keywords
        pre_keyword_count = len(relevant_activities)
        relevant_activities = self.filter_by_keyword(relevant_activities, date_range_applied=(date_range is not None))
        has_keywords = len(relevant_activities) < pre_keyword_count
        
        # ========== SUPERLATIVE QUERY HANDLING (CODE-LEVEL, NOT PROMPT) ==========
        # Detect queries like "longest run", "fastest mile", "shortest hike" and pre-filter
        # to return ONLY the single best match. This is deterministic, not LLM-dependent.
        question_lower = self.question.lower()
        
        # Check if asking for runs specifically
        wants_runs = any(w in question_lower for w in ['run', 'running', 'trail run'])
        
        # Filter to only Run types if asking about runs
        if wants_runs and relevant_activities:
            run_activities = [a for a in relevant_activities if a.get('type') in ['Run', 'TrailRun', 'VirtualRun']]
            if run_activities:
                relevant_activities = run_activities
        
        superlative_result = None
        
        if 'longest' in question_lower and relevant_activities:
            # Longest = max distance - use distance_miles (from summary endpoint) OR distance (from cache)
            def get_distance(a):
                if 'distance_miles' in a:
                    return a.get('distance_miles', 0) or 0
                return (a.get('distance', 0) or 0) / 1609.344  # Convert meters to miles if raw
            
            sorted_by_dist = sorted(relevant_activities, key=get_distance, reverse=True)
            print(f"ContextOptimizer: DEBUG - Top 5 by distance:")
            for a in sorted_by_dist[:5]:
                print(f"  - {a.get('name')}: {get_distance(a):.2f} mi, type={a.get('type')}")
            
            superlative_result = max(relevant_activities, key=get_distance)
            print(f"ContextOptimizer: Superlative 'longest' -> returning single activity: {superlative_result.get('name')}")
            
        elif 'shortest' in question_lower and relevant_activities:
            # Shortest = min distance (excluding 0)
            non_zero = [a for a in relevant_activities if (a.get('distance') or 0) > 0]
            if non_zero:
                superlative_result = min(non_zero, key=lambda x: x.get('distance', 0))
                print(f"ContextOptimizer: Superlative 'shortest' -> returning single activity: {superlative_result.get('name')}")
                
        elif 'fastest' in question_lower and relevant_activities:
            # Fastest = highest average speed (distance / moving_time)
            with_speed = [a for a in relevant_activities if (a.get('distance') or 0) > 0 and (a.get('moving_time') or 0) > 0]
            if with_speed:
                superlative_result = max(with_speed, key=lambda x: x.get('distance', 0) / x.get('moving_time', 1))
                print(f"ContextOptimizer: Superlative 'fastest' -> returning single activity: {superlative_result.get('name')}")
                
        elif 'slowest' in question_lower and relevant_activities:
            # Slowest = lowest average speed
            with_speed = [a for a in relevant_activities if (a.get('distance') or 0) > 0 and (a.get('moving_time') or 0) > 0]
            if with_speed:
                superlative_result = min(with_speed, key=lambda x: x.get('distance', 0) / x.get('moving_time', 1))
                print(f"ContextOptimizer: Superlative 'slowest' -> returning single activity: {superlative_result.get('name')}")
                
        elif ('most elevation' in question_lower or 'highest elevation' in question_lower or 'biggest climb' in question_lower) and relevant_activities:
            # Most elevation = max elevation gain
            superlative_result = max(relevant_activities, key=lambda x: x.get('total_elevation_gain', 0) or 0)
            print(f"ContextOptimizer: Superlative 'most elevation' -> returning single activity: {superlative_result.get('name')}")
        
        # If we found a superlative match, return ONLY that activity
        if superlative_result:
            # Convert distance to miles for display consistency
            scrubbed = {k: v for k, v in superlative_result.items() if k not in ['map', 'polyline', 'summary_polyline']}
            if 'distance' in scrubbed:
                scrubbed['distance_miles'] = round(scrubbed['distance'] / 1609.344, 2)
            if 'total_elevation_gain' in scrubbed:
                scrubbed['elevation_feet'] = round(scrubbed['total_elevation_gain'] * 3.28084, 0)
            
            optimized["relevant_activities"] = [scrubbed]
            optimized["strategy"] = "superlative_single_result"
            optimized["note"] = f"Pre-filtered to single best match for superlative query"
            optimized["activity_count"] = 1
            # Remove lifetime stats to avoid confusion
            if "lifetime_stats" in optimized:
                del optimized["lifetime_stats"]
            return optimized

        # Check if question explicitly needs a list or specific details
        # BUT exclude 'show' if it's combined with 'summary' (e.g., 'show my summary')
        question_lower = self.question.lower()
        has_summary_word = 'summary' in question_lower or 'summari' in question_lower
        
        list_phrases = ['list', 'what did i do', 'details', 'specific', 'names', 'title', 'find', 'search', 'which', 'what run']
        needs_list = any(phrase in question_lower for phrase in list_phrases)
        # 'show' only triggers needs_list if NOT asking for a summary
        if 'show' in question_lower and not has_summary_word:
            needs_list = True
        
        # Check if question is about aggregates (can use summaries)
        is_aggregate = any(phrase in question_lower for phrase in [
            'total', 'sum', 'summary', 'average', 'compare', 'statistics', 'stats',
            'how many', 'how much', 'total distance', 'total time', 'count', 'summarize', 'monthly', 'yearly', 'weekly'
        ])
        
        # Strategy: Use summaries for aggregates, details for specific queries
        # If it's an aggregate question ("how many runs"), we can use summary now that it has type breakdowns!
        # Force summary_only if it's an aggregate query and doesn't explicitly ask for a list AND no keywords were used.
        # If keywords were used (has_keywords), we MUST show the filtered list so LLM can count them.
        if is_aggregate and not needs_list and not has_keywords:
            optimized["strategy"] = "summary_only"
            optimized["note"] = "Aggregates use monthly/yearly summaries"
            optimized["summary_by_year"] = self.by_year  # Include summary for this strategy
            # CRITICAL: Remove lifetime_stats for yearly summaries to prevent LLM confusion
            if "lifetime_stats" in optimized:
                del optimized["lifetime_stats"]
            print(f"ContextOptimizer: Chosen strategy: {optimized['strategy']} (Aggregates, lifetime_stats removed)")
            return optimized
        
        # Estimate token usage
        base_tokens = self.estimate_tokens(optimized)
        activity_tokens = len(relevant_activities) * self.TOKENS_PER_ACTIVITY
        total_estimated = base_tokens + activity_tokens + self.TOKEN_OVERHEAD
        
        # If within limits, include all relevant activities
        if total_estimated < self.MAX_CONTEXT_TOKENS:
            optimized["relevant_activities"] = [scrub_activity(act) for act in relevant_activities]
            optimized["strategy"] = "full_details"
            optimized["activity_count"] = len(relevant_activities)
            optimized["estimated_tokens"] = total_estimated
            return optimized
        
        # Too large - need to be smarter
        # Strategy 1: If asking about a specific day or month, try to tighten the filter
        month_keywords = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december',
                         'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        has_month = any(m in self.question.lower() for m in month_keywords)
        
        if has_month or 'on' in self.question.lower() or 'date' in self.question.lower():
            try:
                # Try search_dates as it's more robust for sentences
                found = search_dates(self.question, settings={'PREFER_DATES_FROM': 'past'})
                if found:
                    match_text, date_obj = found[0]
                    date_key = date_obj.strftime("%Y-%m-%d")
                    # If it's a specific day, show only that day (high priority)
                    if date_key in self.activities_by_date:
                        optimized["relevant_activities"] = [
                            {**scrub_activity(act), 'date': date_key} 
                            for act in self.activities_by_date[date_key]
                        ]
                        optimized["strategy"] = "tight_date_filter"
                        optimized["note"] = f"Filtered strictly for {date_key} to stay within limits"
                        return optimized
            except Exception:
                pass
        
        # Strategy 2: Limit to most recent N activities (within date range or all time)
        # Verify if we can fit a reasonable number of recent activities
        if True:  # Always try this strategy before falling back to summary-only
            
            # Sort by date (most recent first) and limit
            # Note: filter_by_keyword might have already reduced the list significantly!
            # Sort by Relevance Score + Date
            # This ensures "Angeles Crest" (matches query) floats to top even if old!
            relevant_activities.sort(key=self.calculate_relevance, reverse=True)
            
            # Recalculate available tokens
            available_tokens = self.MAX_CONTEXT_TOKENS - base_tokens - self.TOKEN_OVERHEAD
            max_activities = available_tokens // self.TOKENS_PER_ACTIVITY
            
            # Only use this strategy if we can fit a meaningful amount (e.g. at least 50)
            # Otherwise we risk showing a confusingly small slice of history
            # Limit to what we can fit, prioritized by relevance score
            if max_activities > 0:
                optimized["relevant_activities"] = [scrub_activity(act) for act in relevant_activities[:max_activities]]
                optimized["strategy"] = "limited_recent"
                optimized["note"] = f"Showing {len(optimized['relevant_activities'])} most relevant activities"
                return optimized
        
        # Strategy 3: Use year summaries + recent activities
        # Include summaries for all years, but only recent detailed activities
        sorted_dates = sorted(self.activities_by_date.keys(), reverse=True)
        max_recent_days = 30  # Last 30 days of details
        recent_activities = []
        for date_str in sorted_dates[:max_recent_days]:
            for activity in self.activities_by_date[date_str]:
                activity_copy = scrub_activity(activity)
                activity_copy['date'] = date_str
                recent_activities.append(activity_copy)
        
        optimized["relevant_activities"] = recent_activities
        optimized["summary_by_year"] = self.by_year  # Include summary as backup
        optimized["strategy"] = "summary_plus_recent"
        optimized["note"] = "Using year summaries + recent 30 days of activities. For older data, summaries are available."
        optimized["recent_activity_count"] = len(recent_activities)
        
        return optimized

