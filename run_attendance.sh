#!/bin/bash
cd /home/keya/Desktop/keya-attendence-system
source venv/bin/activate

# Kill existing process on port 5005
fuser -k 5005/tcp 2>/dev/null
sleep 2

# Start server
python app.py &
SERVER_PID=$!

# Wait 10 seconds
sleep 10

# Open Chromium in normal mode (not kiosk)
chromium --new-window http://localhost:5005/user &

wait $SERVER_PID
