
import dateparser
import logging
from datetime import datetime

# Configure logging to stdout
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_dates")

query = "what event was recorded last year on this day?"
print(f"Testing query: '{query}'")

try:
    parsed = dateparser.parse(query, settings={'PREFER_DATES_FROM': 'past'})
    print(f"Result: {parsed}")
except Exception as e:
    print(f"Error: {e}")

query2 = "jan 23 2025"
print(f"Testing query: '{query2}'")
try:
    parsed = dateparser.parse(query2)
    print(f"Result: {parsed}")
except Exception as e:
    print(f"Error: {e}")
