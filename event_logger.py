import sys
from datetime import datetime

event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
stream = sys.argv[2] if len(sys.argv) > 2 else "unknown"
 
with open("event_log.txt", "a", encoding="utf-8") as f:
    f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {event} | {stream}\n") 