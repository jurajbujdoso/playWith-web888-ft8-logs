# Backend (app.py) - refactored for Pythonic style
from flask import Flask, render_template, jsonify
from threading import Thread, Lock
import socket
import re
import maidenhead as mh
import pytz
import time
import argparse
from datetime import datetime as datet

app = Flask(__name__)

# Parse command-line arguments
parser = argparse.ArgumentParser()
parser.add_argument('mode', nargs='?', default='', help="Mode of operation, e.g., 'debug'")
args = parser.parse_args()

# Set debug mode based on the command-line argument
debug_mode = args.mode == 'debug'

def debug_print(message):
    """Prints the message if debug mode is enabled."""
    if debug_mode:
        print(message)

@app.route('/')
def index():
    return render_template('index.html')

data_lock = Lock()
active_spots = []

class FT8Processor:
    def __init__(self):
        self.log_pattern = re.compile(
            r'FT8 DECODE:\s+(\d+\.\d{3})\s+(\w+)\s+([A-R]{2}\d{2}[A-X]{0,2})\s+(-?\d+)\s+(\d+km)\s+([A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})'
        )

    def parse_line(self, line):
        """Parse a line of FT8 log and extract relevant information."""
        debug_print(f"Raw input: {line}")
        match = self.log_pattern.search(line)
        if not match:
            debug_print("No pattern match")
            return None
        
        try:
            timestamp = datet.strptime(match.group(6), "%a %b %d %H:%M:%S %Y")
            timestamp = pytz.utc.localize(timestamp)
            unix_time = int(timestamp.timestamp())
            lat, lon = mh.to_location(match.group(3))
            
            return {
                'callsign': match.group(2),
                'frequency': float(match.group(1)),
                'timestamp': unix_time,
                'coordinates': [lat, lon],
                'humantime': timestamp
            }
        except Exception as e:
            debug_print(f"Parsing error: {str(e)}")
            return None

def udp_listener():
    """Listen for incoming UDP packets and process FT8 log data."""
    processor = FT8Processor()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 5140))
    
    while True:
        data, addr = sock.recvfrom(1024)
        entry = processor.parse_line(data.decode('utf-8'))
        
        if entry:
            with data_lock:
                active_spots.append(entry)
                debug_print(f"New spot added: {entry}")

@app.route('/spots')
def get_spots():
    """Get active spots that have not expired."""
    nowUnix = int(time.time())
    debug_print("====================SPOTS=======================================")

    # Prepare data for the response
    spots = [{
        'coordinates': spot['coordinates'],
        'callsign': spot['callsign'],
        'frequency': spot['frequency'],
        'timestamp': spot['timestamp']
    } for spot in active_spots if nowUnix - spot['timestamp'] <= 1800]

    return jsonify(spots)

def cleanup_spots():
    """Clears out spots that are older than 30 minutes."""
    nowUnix = int(time.time())
    with data_lock:
        active_spots[:] = [
            spot for spot in active_spots if nowUnix - spot['timestamp'] <= 1800
        ]
    debug_print(f"Cleared spots older than 30 min. Remaining spots: {len(active_spots)}")

def schedule_cleanup():
    """Schedules the cleanup function to run every 30 seconds."""
    while True:
        time.sleep(30)  # Wait for 30 seconds
        cleanup_spots()

if __name__ == '__main__':
    debug_print("Syslog should send data to UDP port: 5140")
    
    # Start the UDP listener in a separate thread
    Thread(target=udp_listener, daemon=True).start()
    
    # Start the cleanup scheduler in a separate thread
    Thread(target=schedule_cleanup, daemon=True).start()
    
    app.run(host='0.0.0.0', port=5000)
