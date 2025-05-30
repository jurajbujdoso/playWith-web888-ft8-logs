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
import os  # Added to import os for reading environment variables

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



# Retrieve the LimitTime from the environment variable if set, otherwise default to 1800
LIMIT_TIME = int(os.getenv('LimitTime', 1800))

STATION_CALLSIGN = (os.getenv('STATION_CALLSIGN', "N0CALL"))      #my callsign
MY_GRIDSQUARE    = (os.getenv('MY_GRIDSQUARE', "AA00AA"))         #my grid
ADIF_LOGS        = (os.getenv('ADIF_LOGS', "No"))                 #create adif file or not



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
            #Raw input: <14>Feb 26 05:37:01 web-888 : 2d:07:12:58.165 ..2345678....   2           FT8 DECODE: 7074.566 US5EAA KN78 -8 1012km Wed Feb 26 05:36:45 2025
            #New spot added: {'callsign': 'US5EAA', 'frequency': 7074.566, 'timestamp': 1740548205, 'coordinates': [48.0, 34.0], 'humantime': datetime.datetime(2025, 2, 26, 5, 36, 45, tzinfo=<UTC>)
            timestamp = datet.strptime(match.group(6), "%a %b %d %H:%M:%S %Y")
            timestamp = pytz.utc.localize(timestamp)
            unix_time = int(timestamp.timestamp())
            lat, lon = mh.to_location(match.group(3))
            locatorx =  match.group(3)
            
            return {
                'callsign': match.group(2),
                'frequency': float(match.group(1)),
                'timestamp': unix_time,
                'coordinates': [lat, lon],
                'humantime': timestamp,
                'locator': locatorx,
                'distance': match.group(5),
                'signal': match.group(4),
            }
        except Exception as e:
            debug_print(f"Parsing error: {str(e)}")
            return None
        

# frequency conversion
def frequency_to_band(frequency_khz):
    """Convert frequency in kHz to amateur radio band name"""
    freq_mhz = frequency_khz / 1000
    if 0.136 <= freq_mhz < 0.478: return '2190m'
    elif 0.478 <= freq_mhz < 2.0: return '630m'
    elif 1.8 <= freq_mhz < 2.0: return '160m'
    elif 3.5 <= freq_mhz < 4.0: return '80m'
    elif 5.1 <= freq_mhz < 5.45: return '60m'
    elif 7.0 <= freq_mhz < 7.3: return '40m'
    elif 10.1 <= freq_mhz < 10.15: return '30m'
    elif 14.0 <= freq_mhz < 14.35: return '20m'
    elif 18.068 <= freq_mhz < 18.168: return '17m'
    elif 21.0 <= freq_mhz < 21.45: return '15m'
    elif 24.89 <= freq_mhz < 24.99: return '12m'
    elif 28.0 <= freq_mhz < 29.7: return '10m'
    elif 50 <= freq_mhz < 54: return '6m'
    elif 144 <= freq_mhz < 148: return '2m'
    else: return 'Unknown'


        
        
#log in adif format for wsjt-x like processing        
def log_adi_entry(entry):
    """Append WSJT-X compatible ADIF entry to log file."""
    directory = './logs/'
    file_path = './logs/wsjtx_log.adi'

    # Ensure the directory exists
    os.makedirs(directory, exist_ok=True)

    # Convert frequency and time formats
    band = frequency_to_band(entry['frequency'])
    freq_mhz = entry['frequency'] / 1000
    qso_date = entry['humantime'].strftime('%Y%m%d')
    time_on = entry['humantime'].strftime('%H%M%S')

    # Build the ADIF record
    adif_entry = f"""<CALL:{len(entry['callsign'])}>{entry['callsign']}
<GRIDSQUARE:{len(entry['locator'])}>{entry['locator']}
<MODE:3>FT8
<RST_SENT:3>-00
<RST_RCVD:{len(entry['signal'])}>{entry['signal']}
<QSO_DATE:8>{qso_date}
<TIME_ON:6>{time_on}
<QSO_DATE_OFF:8>{qso_date}
<TIME_OFF:6>{time_on}
<BAND:{len(band)}>{band}
<FREQ:9>{freq_mhz:.6f}
<STATION_CALLSIGN:{len(STATION_CALLSIGN)}>{STATION_CALLSIGN}
<MY_GRIDSQUARE:{len(MY_GRIDSQUARE)}>{MY_GRIDSQUARE}
<COMMENT:{len(entry['distance'])}>Distance: {entry['distance']}
<EOR>\n"""

    # Append the record to the file
    with open(file_path, 'a', encoding='utf-8') as log_file:
        log_file.write(adif_entry)
        
        
        

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
                if ADIF_LOGS != "No":
                   log_adi_entry(entry)
                
                
                
                
                
                

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
        'timestamp': spot['timestamp'],
        'locator': spot['locator'],
        'distance':spot['distance'],
        'signal':spot['signal'],
        'uptime':(nowUnix - spot['timestamp'])
                  
    } for spot in active_spots if nowUnix - spot['timestamp'] <= LIMIT_TIME]
    return jsonify(spots)







def cleanup_spots():
    """Clears out spots that are older than the defined LIMIT_TIME."""
    nowUnix = int(time.time())
    with data_lock:
        active_spots[:] = [
            spot for spot in active_spots if nowUnix - spot['timestamp'] <= LIMIT_TIME
        ]
    debug_print(f"Cleared spots older than {LIMIT_TIME} sec. Remaining spots: {len(active_spots)}")
          
    
    
    

def schedule_cleanup():
    """Schedules the cleanup function to run every 30 seconds."""
    while True:
        time.sleep(30)  # Wait for 30 seconds
        cleanup_spots()
        
                

if __name__ == '__main__':
    print("--------------------------------------------------------------------")
    print("Syslog should send data to UDP port: 5140")
    print("  *** LimitTime: We show spots from last: "+str(LIMIT_TIME)+" sec")
    print("--------------------------------------------------------------------")
    
    # Start the UDP listener in a separate thread
    Thread(target=udp_listener, daemon=True).start()
    
    # Start the cleanup scheduler in a separate thread
    Thread(target=schedule_cleanup, daemon=True).start()
    
    app.run(host='0.0.0.0', port=5000)
