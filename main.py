import os
import json
import datetime
import logging
import requests
from flask import Flask, request, jsonify
import gspread
from google.oauth2 import service_account

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Telnyx configuration
TELNYX_API_KEY = os.environ.get('TELNYX_API_KEY')
TELNYX_API_URL = "https://api.telnyx.com/v2"

# Initialize Google Sheets
GOOGLE_CREDS = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')

sheets_client = None
if GOOGLE_CREDS and SPREADSHEET_ID:
    try:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS),
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        sheets_client = gspread.authorize(creds)
        logger.info("Google Sheets connected")
    except Exception as e:
        logger.error(f"Google Sheets setup failed: {e}")

# Store active calls
active_calls = {}

def telnyx_api_request(method, endpoint, data=None):
    """Make a request to Telnyx API"""
    headers = {
        'Authorization': f'Bearer {TELNYX_API_KEY}',
        'Content-Type': 'application/json'
    }
    url = f"{TELNYX_API_URL}{endpoint}"
    
    try:
        if method == 'POST':
            response = requests.post(url, headers=headers, json=data)
        elif method == 'GET':
            response = requests.get(url, headers=headers)
        
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Telnyx API error: {e}")
        return None
    """Log call data to Google Sheets"""
    if not sheets_client or not SPREADSHEET_ID:
        logger.warning("Google Sheets not configured")
        return
    
    try:
        sheet = sheets_client.open_by_key(SPREADSHEET_ID).sheet1
        row = [
            data.get('timestamp', datetime.datetime.utcnow().isoformat()),
            data.get('call_id', ''),
            data.get('from', ''),
            data.get('to', ''),
            data.get('status', ''),
            data.get('duration', ''),
            data.get('transcription', ''),
            data.get('result', '')
        ]
        sheet.append_row(row)
        logger.info(f"Logged to sheet: {row}")
    except Exception as e:
        logger.error(f"Failed to log to sheet: {e}")

@app.route('/')
def index():
    return jsonify({
        'status': 'running',
        'message': 'AI Call Screener Active',
        'sheets': 'connected' if sheets_client else 'not connected',
        'phone': '+1 480 786 8280'
    })

@app.route('/webhooks/telnyx', methods=['POST'])
def handle_telnyx_webhook():
    """Main webhook handler for all Telnyx events"""
    try:
        webhook_data = request.get_json()
        event_type = webhook_data.get('data', {}).get('event_type')
        call_data = webhook_data.get('data', {}).get('payload', {})
        
        logger.info(f"Received event: {event_type}")
        logger.info(f"Call data: {json.dumps(call_data, indent=2)}")
        
        if event_type == 'call.initiated':
            return handle_incoming_call(call_data)
        elif event_type == 'call.answered':
            return handle_call_answered(call_data)
        elif event_type == 'call.hangup':
            return handle_call_hangup(call_data)
        elif event_type == 'call.recording.saved':
            return handle_recording_saved(call_data)
        else:
            logger.info(f"Unhandled event type: {event_type}")
            return '', 200
            
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500

def handle_incoming_call(call_data):
    """Handle new incoming call"""
    call_id = call_data.get('call_control_id')
    from_number = call_data.get('from')
    to_number = call_data.get('to')
    
    logger.info(f"Incoming call from {from_number} to {to_number}")
    
    # Store call info
    active_calls[call_id] = {
        'from': from_number,
        'to': to_number,
        'start_time': datetime.datetime.utcnow(),
        'status': 'ringing'
    }
    
    # Log initial call
    log_to_sheet({
        'call_id': call_id,
        'from': from_number,
        'to': to_number,
        'status': 'incoming'
    })
    
    # Answer the call
    try:
        answer_data = {
            'command_id': str(call_id),
            'webhook_url': f'https://flask-production-2806.up.railway.app/webhooks/telnyx'
        }
        result = telnyx_api_request('POST', f'/calls/{call_id}/actions/answer', answer_data)
        if result:
            logger.info(f"Answered call {call_id}")
        else:
            logger.error(f"Failed to answer call {call_id}")
    except Exception as e:
        logger.error(f"Failed to answer call: {e}")
    
    return '', 200

def handle_call_answered(call_data):
    """Handle call answered event"""
    call_id = call_data.get('call_control_id')
    
    if call_id in active_calls:
        active_calls[call_id]['status'] = 'answered'
        active_calls[call_id]['answered_time'] = datetime.datetime.utcnow()
    
    try:
        # Start recording
        record_data = {
            'command_id': str(call_id),
            'format': 'mp3',
            'channels': 'single'
        }
        telnyx_api_request('POST', f'/calls/{call_id}/actions/record_start', record_data)
        
        # Play greeting
        speak_data = {
            'command_id': str(call_id),
            'payload': "Hello, you've reached Anthony Barragan. Please state your name and reason for calling, and I'll get back to you shortly.",
            'voice': 'female',
            'language': 'en-US'
        }
        telnyx_api_request('POST', f'/calls/{call_id}/actions/speak', speak_data)
        
        # Wait a moment then end call
        # For MVP, let's just record for 30 seconds then hangup
        import threading
        def end_call():
            import time
            time.sleep(30)  # Record for 30 seconds
            try:
                speak_end = {
                    'command_id': str(call_id),
                    'payload': "Thank you for your call. I'll review your message and get back to you.",
                    'voice': 'female'
                }
                telnyx_api_request('POST', f'/calls/{call_id}/actions/speak', speak_end)
                time.sleep(5)
                
                hangup_data = {'command_id': str(call_id)}
                telnyx_api_request('POST', f'/calls/{call_id}/actions/hangup', hangup_data)
            except Exception as e:
                logger.error(f"Error ending call: {e}")
        
        threading.Thread(target=end_call, daemon=True).start()
        
    except Exception as e:
        logger.error(f"Failed to handle answered call: {e}")
    
    return '', 200

def handle_call_hangup(call_data):
    """Handle call ended"""
    call_id = call_data.get('call_control_id')
    
    if call_id in active_calls:
        call_info = active_calls[call_id]
        call_info['status'] = 'ended'
        call_info['end_time'] = datetime.datetime.utcnow()
        
        # Calculate duration
        if 'answered_time' in call_info:
            duration = (call_info['end_time'] - call_info['answered_time']).total_seconds()
        else:
            duration = 0
        
        # Log final call status
        log_to_sheet({
            'call_id': call_id,
            'from': call_info['from'],
            'to': call_info['to'],
            'status': 'completed',
            'duration': f"{duration:.1f} seconds",
            'result': 'recorded'
        })
        
        # Clean up
        del active_calls[call_id]
    
    return '', 200

def handle_recording_saved(call_data):
    """Handle recording saved event"""
    call_id = call_data.get('call_control_id')
    recording_url = call_data.get('recording_urls', {}).get('mp3')
    
    logger.info(f"Recording saved for call {call_id}: {recording_url}")
    
    # Log recording URL
    log_to_sheet({
        'call_id': call_id,
        'status': 'recording_saved',
        'transcription': f"Recording URL: {recording_url}"
    })
    
    # TODO: In the next iteration, we'll download and transcribe this
    
    return '', 200

@app.route('/health')
def health():
    """Health check endpoint for Railway"""
    return jsonify({
        'status': 'healthy',
        'active_calls': len(active_calls),
        'sheets_connected': sheets_client is not None
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)