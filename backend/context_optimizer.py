"""
Smart context optimization for Gemini API.
Handles context limits, token counting, and intelligent data filtering.
"""
import json
import re
from typing import Dict, List, Any, Tuple, Optional
from datetime import datetime, timedelta
import dateparser


class ContextOptimizer:
    """
    Optimizes context sent to Gemini to:
    1. Prevent context limit errors
    2. Minimize token usage (cost)
    3. Ensure all historical data is accessible
    """
    
    # Token estimates (rough approximations)
    # Gemini 2.0 Flash has ~1M token context, but we want to stay well under
    MAX_CONTEXT_TOKENS = 30000  # Conservative limit for stability
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
        
        # Check for Month names or specific date formats FIRST
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
                    "what were", "what were the", "what was", "what about", "list", "tell me about"
                ]
                for word in noise_words:
                    clean_q = clean_q.replace(word, "").strip()
                
                # Strip distinct prefixes again just in case
                for prefix in ["show me activities on", "show matches for", "activities on", "what happened on", "records for"]:
                     if prefix in clean_q:
                        clean_q = clean_q.replace(prefix, "").strip()
                
                # Final cleanup of punctuation
                clean_q = clean_q.replace("?", "").replace(".", "").strip()

                print(f"ContextOptimizer: Attempting dateparser on cleaned '{clean_q}'", flush=True)
                
                if not clean_q:
                    print("ContextOptimizer: Query empty after cleaning", flush=True)
                    parsed = None
                else:
                    parsed = dateparser.parse(clean_q, settings={'PREFER_DATES_FROM': 'past', 'STRICT_PARSING': False})
                
                # Fallback: If dateparser fails but we see "yesterday" or "today", force it
                if not parsed:
                    if "yesterday" in clean_q:
                        print("ContextOptimizer: Fallback triggered for 'yesterday'")
                        parsed = datetime.now() - timedelta(days=1)
                    elif "today" in clean_q:
                        print("ContextOptimizer: Fallback triggered for 'today'")
                        parsed = datetime.now()

                
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
        try:
            parsed = dateparser.parse(self.question, settings={'PREFER_DATES_FROM': 'past'})
            if parsed:
                # If a single date, return that day
                return (parsed.replace(hour=0, minute=0, second=0), 
                       parsed.replace(hour=23, minute=59, second=59))
        except:
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
    
    def filter_by_keyword(self, activities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter activities by keywords found in quotes or after 'with'/'contains'."""
        question_lower = self.question.lower()
        
        # Extract potential keywords
        keywords = []
        
        # 1. Quoted strings (e.g., "pain", 'easy run')
        quotes = re.findall(r"['\"](.*?)['\"]", question_lower)
        keywords.extend(quotes)
        
        # 2. Simple "with <word>" detection could be too aggressive, stick to quotes or specific phrases for now.
        # But let's try to capture "mentions X" or "note says X" if not quoted.
        if not keywords:
            # Regex to catch "notes for X", "mentioning X", "contains X", "with X"
            # Handles optional prepositions like "for", "about", "that"
            match = re.search(r"(?:contain\w*|mention\w*|note\w*|desc\w*|say\w*|with)\s+(?:for|about|that)?\s*['\"]?(\w+)['\"]?", question_lower)
            if match:
                word = match.group(1)
                # Blacklist of common words that might follow these triggers but aren't keywords
                blacklist = ['activities', 'runs', 'rides', 'the', 'a', 'an', 'my', 'me', 'in', 'on']
                if word not in blacklist:
                    keywords.append(word)
        
        if not keywords:
            return activities
            
        print(f"ContextOptimizer: Filtering by keywords: {keywords}")
        filtered = []
        for activity in activities:
            # Check fields
            text_content = (
                str(activity.get('name', '')) + " " + 
                str(activity.get('private_note', '')) + " " + 
                str(activity.get('description', ''))
            ).lower()
            
            # Simple match: if ANY keyword is in the text
            if any(k in text_content for k in keywords):
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
        optimized = {
            "stats": self.stats,
        }
        
        # Determine what level of detail is needed
        date_range = self.parse_date_range()
        
        # Check if question explicitly needs a list or specific details
        needs_list = any(phrase in self.question for phrase in [
            'list', 'show', 'what did i do', 'details', 'specific', 'names', 'title', 'find', 'search', 'which'
        ])
        
        # Check if question just mentions an activity type (which can be answered by summary now)
        mentions_activity = any(phrase in self.question for phrase in [
            'activity', 'activities', 'run', 'ride', 'workout', 'training', 'walk', 'hike', 'swim'
        ])
        
        # Check if question is about aggregates (can use summaries)
        is_aggregate = any(phrase in self.question for phrase in [
            'total', 'sum', 'average', 'compare', 'statistics', 'stats',
            'how many', 'how much', 'total distance', 'total time', 'count'
        ])
        
        # Strategy: Use summaries for aggregates, details for specific queries
        # If it's an aggregate question ("how many runs"), we can use summary now that it has type breakdowns!
        # Force summary_only if it's an aggregate query and doesn't explicitly ask for a list.
        if is_aggregate and not needs_list:
            optimized["strategy"] = "summary_only"
            optimized["note"] = "Aggregates use monthly/yearly summaries"
            optimized["summary_by_year"] = self.by_year  # Include summary for this strategy
            print(f"ContextOptimizer: Chosen strategy: {optimized['strategy']} (Aggregates)")
            return optimized
        
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
        # This allows "find runs with 'pain'" to work over the entire history (or date range)
        # BEFORE we truncate for context limits.
        relevant_activities = self.filter_by_keyword(relevant_activities)
        
        # Estimate token usage
        base_tokens = self.estimate_tokens(optimized)
        activity_tokens = len(relevant_activities) * self.TOKENS_PER_ACTIVITY
        total_estimated = base_tokens + activity_tokens + self.TOKEN_OVERHEAD
        
        # If within limits, include all relevant activities
        if total_estimated < self.MAX_CONTEXT_TOKENS:
            optimized["relevant_activities"] = relevant_activities
            optimized["strategy"] = "full_details"
            optimized["activity_count"] = len(relevant_activities)
            optimized["estimated_tokens"] = total_estimated
            if relevant_activities:
                # print(f"ContextOptimizer: Full details. First activity sample: {relevant_activities[0]}")
                pass
            # NOTE: We intentionally DO NOT include summary_by_year here to avoid conflicting with actual data.
            return optimized
        
        # Too large - need to be smarter
        # Strategy 1: If asking about specific date, include that date only
        if 'on' in self.question or 'date' in self.question:
            # Try to extract specific date
            try:
                parsed_date = dateparser.parse(self.question)
                if parsed_date:
                    date_key = parsed_date.strftime("%Y-%m-%d")
                    if date_key in self.activities_by_date:
                        optimized["relevant_activities"] = [
                            {**act, 'date': date_key} 
                            for act in self.activities_by_date[date_key]
                        ]
                        optimized["strategy"] = "specific_date"
                        optimized["note"] = f"Showing activities for {date_key} only"
                        return optimized
            except:
                pass
        
        # Strategy 2: Limit to most recent N activities (within date range or all time)
        # Verify if we can fit a reasonable number of recent activities
        if True:  # Always try this strategy before falling back to summary-only
            
            # Sort by date (most recent first) and limit
            # Note: filter_by_keyword might have already reduced the list significantly!
            relevant_activities.sort(key=lambda x: x.get('start_time', ''), reverse=True)
            
            # Recalculate available tokens
            available_tokens = self.MAX_CONTEXT_TOKENS - base_tokens - self.TOKEN_OVERHEAD
            max_activities = available_tokens // self.TOKENS_PER_ACTIVITY
            
            # Only use this strategy if we can fit a meaningful amount (e.g. at least 50)
            # Otherwise we risk showing a confusingly small slice of history
            if max_activities > 50:
                optimized["relevant_activities"] = relevant_activities[:max_activities]
                optimized["strategy"] = "limited_recent"
                optimized["note"] = f"Showing {len(optimized['relevant_activities'])} most recent activities (limited by context size)"
                optimized["total_available"] = len(relevant_activities)
                
                # Debug logging for truncation
                print(f"ContextOptimizer: Truncating context. Showing top {max_activities} of {len(relevant_activities)} activities.")
                if optimized["relevant_activities"]:
                    first = optimized["relevant_activities"][0].get('date', 'unknown')
                    last = optimized["relevant_activities"][-1].get('date', 'unknown')
                    print(f"ContextOptimizer: Truncated range: {last} to {first}") # Reverse chronological
                    
                return optimized
        
        # Strategy 3: Use year summaries + recent activities
        # Include summaries for all years, but only recent detailed activities
        sorted_dates = sorted(self.activities_by_date.keys(), reverse=True)
        max_recent_days = 30  # Last 30 days of details
        recent_activities = []
        for date_str in sorted_dates[:max_recent_days]:
            for activity in self.activities_by_date[date_str]:
                activity['date'] = date_str
                recent_activities.append(activity)
        
        optimized["relevant_activities"] = recent_activities
        optimized["summary_by_year"] = self.by_year  # Include summary as backup
        optimized["strategy"] = "summary_plus_recent"
        optimized["note"] = "Using year summaries + recent 30 days of activities. For older data, summaries are available."
        optimized["recent_activity_count"] = len(recent_activities)
        
        return optimized

