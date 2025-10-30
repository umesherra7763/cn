"""
LAN-Based Communication Client (Complete - All 5 Modules)

This client application implements the frontend for a multi-user communication
suite. It connects to the server and handles:
- Module 1: Video Conferencing (UDP)
- Module 2: Audio Conferencing (UDP)
- Module 3: Screen Sharing (TCP)
- Module 4: Group Text Chat (TCP)
- Module 5: File Sharing (TCP)

It features a "single window" UI that organizes all modules and runs
multiple background threads for:
- TCP Command Networking (NetworkClient)
- TCP File Transfers (FileTransferThread)
- TCP Screen Capture (ScreenCaptureThread)
- UDP Audio Capture (AudioCaptureThread)
- UDP Audio Playback (AudioPlayerThread)
- UDP Video Capture (VideoCaptureThread)
- UDP Video Playback (VideoPlayerThread)

Required Libraries:
- PySide6
- numpy
- mss
- pillow
- pyaudio
- opencv-python
"""

import sys
import asyncio
import struct
import json
import logging
import threading
import base64
import os
import time
from enum import IntEnum
from collections import deque
import socket

# --- Import PySide6 ---
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTextEdit, QListWidget, QListWidgetItem, QSplitter,
    QLabel, QStackedWidget, QFormLayout, QMessageBox, QTabWidget,
    QGridLayout, QSizePolicy, QFileDialog, QToolButton, QProgressBar
)
from PySide6.QtCore import (
    QObject, QThread, Signal, Qt, Slot, QSize, QFileInfo, QTimer
)
from PySide6.QtGui import (
    QColor, QIcon, QPixmap, QPainter, QImage, QImageReader
)

# --- Import Media Libraries ---
try:
    import pyaudio
except ImportError:
    print("PyAudio not found. Audio module will be disabled. Install with 'pip install pyaudio'")
    pyaudio = None

try:
    import cv2
except ImportError:
    print("OpenCV not found. Video module will be disabled. Install with 'pip install opencv-python'")
    cv2 = None

try:
    from mss import mss
    from PIL import Image
except ImportError:
    print("MSS or Pillow not found. Screen sharing will be disabled. Install with 'pip install mss pillow'")
    mss = None
    Image = None

try:
    import numpy as np
except ImportError:
    print("Numpy not found. Audio module will be disabled. Install with 'pip install numpy'")
    np = None


# --- Protocol Constants (must match server.py) ---
HOST = '192.168.144.48'  # Connect to localhost by default. CHANGE TO SERVER'S LAN IP
TCP_COMMAND_PORT = 5000
TCP_FILE_PORT = 5001
UDP_AUDIO_PORT = 5002  # Server's audio port
UDP_VIDEO_PORT = 5003  # Server's video port

LOG_LEVEL = logging.DEBUG

MAGIC_NUMBER = 0xDEADBEEF
PROTOCOL_VERSION = 0x0100  # v1.0
HEADER_FORMAT = '!IHHQI'  # Magic, Version, Type, SessionID, PayloadLen
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
UDP_HEADER_FORMAT = '!Q'  # SessionID
UDP_HEADER_SIZE = struct.calcsize(UDP_HEADER_FORMAT)

# --- Message Types (Must match server.py) ---
class MessageType(IntEnum):
    # Control
    AUTHENTICATION_REQUEST = 1
    AUTHENTICATION_SUCCESS = 2
    AUTHENTICATION_FAILURE = 3
    HEARTBEAT = 10
    
    # Chat (Module 4)
    SEND_TEXT_MESSAGE = 100
    BROADCAST_TEXT_MESSAGE = 101
    
    # Presence
    PRESENCE_UPDATE = 200
    
    # Screen Sharing (Module 3 - TCP)
    SCREEN_SHARE_START_REQUEST = 300
    SCREEN_SHARE_STOP_REQUEST = 301
    NOTIFY_SCREEN_SHARE_STARTED = 302
    NOTIFY_SCREEN_SHARE_STOPPED = 303
    SCREEN_SHARE_DATA_FRAME = 304
    
    # File Sharing (Module 5 - TCP)
    FILE_TRANSFER_NOTIFY_REQUEST = 400
    FILE_TRANSFER_NOTIFY_BROADCAST = 401
    FILE_DOWNLOAD_REQUEST = 402  # This is a client->server request
    FILE_UPLOAD_START_RESPONSE = 404 # Server->client: OK to upload
    FILE_DOWNLOAD_START_RESPONSE = 406 # Server->client: OK to download
    FILE_TRANSFER_ERROR = 407

    # Audio/Video (UDP)
    SET_UDP_PORT_REQUEST = 500 # FIX: This was the wrong value before
    SET_UDP_PORT_SUCCESS = 501
    SET_MEDIA_STATE_REQUEST = 502

# --- SVG Icon Definitions ---
SVG_ICONS = {
    "mic_on": """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line></svg>""",
    "mic_off": """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="1" y1="1" x2="23" y2="23"></line><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"></path><path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line></svg>""",
    "video_on": """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="23 7 16 12 23 17 23 7"></polygon><rect x="2" y="5" width="14" height="14" rx="2" ry="2"></rect></svg>""",
    "video_off": """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 16v1a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2m5.66 0H14a2 2 0 0 1 2 2v3.34l1 1L23 7v10"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>""",
    "screen_share": """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7"></path><path d="M16 3l5 5h-5V3z"></path><path d="m10 14-3 3 3 3"></path><path d="m14 14 3 3-3 3"></path></svg>""",
    "hang_up": """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>""",
    "upload": """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>"""
}

def create_svg_icon(svg_data, color="#dcddde"):
    """Creates a QIcon from an SVG string."""
    svg_bytes = svg_data.replace("currentColor", color).encode('utf-8')
    pixmap = QPixmap()
    pixmap.loadFromData(svg_bytes)
    return QIcon(pixmap)

# --- Modern UI Dark Theme (QSS) ---
MODERN_STYLE_SHEET = """
    /* Main window and base widget */
    QMainWindow, QWidget {
        background-color: #36393f; /* Dark grey background */
        color: #dcddde; /* Light grey text */
        font-family: 'Inter', 'Segoe UI', Arial, sans-serif;
        font-size: 14px;
        border: none;
    }
    /* ... (rest of QSS from previous step) ... */
    QStackedWidget, QWidget#LoginPage, QWidget#MainConferencePage {
        background-color: #36393f;
    }
    QLabel { color: #b9bbbe; }
    QLabel#StatusLabel { color: #b9bbbe; }
    QLabel#StatusLabel[error="true"] { color: #f04747; }
    QLabel#StageLabel, QLabel#VideoLabel, QLabel#ScreenShareDisplay {
        background-color: #202225;
        color: #8e9297;
        font-size: 18px;
        border-radius: 8px;
    }
    QLabel#PresenterLabel {
        color: #fff;
        font-size: 16px;
        font-weight: 500;
        background-color: #2f3136;
        padding: 8px;
        border-radius: 8px;
    }
    QLineEdit {
        background-color: #202225;
        border: 1px solid #2b2d31;
        border-radius: 5px;
        padding: 8px;
        color: #dcddde;
    }
    QLineEdit:focus { border-color: #5865f2; }
    QLineEdit#ChatInput { border-radius: 8px; padding: 10px; }
    QTextEdit {
        background-color: #2f3136;
        border: 1px solid #2b2d31;
        border-radius: 5px;
        color: #dcddde;
        padding: 5px;
    }
    QTabWidget::pane {
        border: 1px solid #2b2d31;
        border-radius: 5px;
        background-color: #2f3136;
    }
    QTabBar::tab {
        background-color: #2f3136;
        color: #b9bbbe;
        padding: 10px 15px;
        font-weight: 500;
        border-top-left-radius: 5px;
        border-top-right-radius: 5px;
    }
    QTabBar::tab:selected {
        background-color: #36393f;
        color: #ffffff;
        border-bottom: 2px solid #5865f2;
    }
    QTabBar::tab:!selected { margin-top: 3px; }
    QListWidget {
        background-color: #2f3136;
        border: none;
        padding: 5px;
        outline: 0;
    }
    QListWidget::item { padding: 8px 10px; color: #b9bbbe; }
    QListWidget::item:hover { background-color: #40444b; border-radius: 3px; }
    QPushButton {
        background-color: #5865f2;
        color: #ffffff;
        border: none;
        border-radius: 5px;
        padding: 10px 15px;
        font-weight: 500;
    }
    QPushButton:hover { background-color: #4e5adf; }
    QPushButton:pressed { background-color: #4752c4; }
    QPushButton:disabled { background-color: #4f545c; color: #96989d; }
    QPushButton#UploadButton { background-color: #4f545c; }
    QPushButton#UploadButton:hover { background-color: #5865f2; }
    QWidget#ControlToolbar {
        background-color: #2f3136;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }
    QToolButton {
        background-color: #40444b;
        color: #dcddde;
        border: none;
        border-radius: 5px; /* Rounded square */
        padding: 10px;
        font-weight: 500;
    }
    QToolButton:hover { background-color: #4f545c; }
    QToolButton:pressed { background-color: #5865f2; }
    QToolButton:checkable:checked { background-color: #5865f2; }
    QToolButton#HangUpButton { background-color: #f04747; }
    QToolButton#HangUpButton:hover { background-color: #d84040; }
    QSplitter::handle { background-color: #2b2d31; width: 1px; }
    QSplitter::handle:hover { background-color: #5865f2; }
    QScrollBar:vertical {
        background: #2f3136;
        width: 10px;
        margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #202225;
        min-height: 20px;
        border-radius: 5px;
    }
    QProgressBar {
        border: 1px solid #2b2d31;
        border-radius: 5px;
        background-color: #2f3136;
        text-align: center;
        color: #dcddde;
    }
    QProgressBar::chunk {
        background-color: #43b581;
        border-radius: 5px;
    }
"""

# --- Logging Setup ---
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s [%(levelname)s] (%(threadName)s) %(message)s',
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# --- Helper Function ---
def get_free_port():
    """Finds and returns a free UDP port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port

# --- Model Layer (NetworkClient - Module 4, 3, 5) ---

class NetworkClient(QThread):
    """
    Runs all TCP network I/O in a separate thread to keep the
    GUI responsive. Handles Chat, Presence, and all *commands*
    for Screen Sharing, File Sharing, Audio, and Video.
    """
    # Signals to communicate with the GUI (Controller)
    connection_status = Signal(str)
    authentication_success = Signal(str, int) # message, session_id
    authentication_failure = Signal(str)
    
    # Chat signals
    message_received = Signal(str, str) # username, message
    
    # Presence signals
    presence_update = Signal(list) # list of usernames
    
    # Screen share signals
    screen_share_started = Signal(str) # username
    screen_share_stopped = Signal()
    screen_share_data_received = Signal(str) # base64 image data
    
    # File share signals
    file_notify_received = Signal(str, str, int, str) # file_id, filename, size, uploader_name
    file_upload_approved = Signal(str, str) # file_id, filename
    file_download_approved = Signal(str, str) # file_id, filename
    
    # Audio/Video signals
    udp_ports_confirmed = Signal()

    def __init__(self, host, port):
        super().__init__()
        self._host = host
        self._port = port
        self._loop = None
        self._reader = None
        self._writer = None
        self.session_id = None
        self._running = True
        self._message_queue = asyncio.Queue()
        self._heartbeat_timer = None
        QImageReader.setAllocationLimit(0) # Suppress warnings

    def run(self):
        """The main entry point for the QThread."""
        log.info("NetworkClient (TCP) thread started.")
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self.main_loop())
        except Exception as e:
            log.error(f"Critical error in network thread: {e}", exc_info=True)
        finally:
            if self._loop and self._loop.is_running():
                self._loop.stop()
            log.info("NetworkClient (TCP) thread finished.")

    async def main_loop(self):
        """The main asyncio loop for this thread."""
        try:
            self.connection_status.emit("Connecting...")
            log.info(f"Attempting to connect to {self._host}:{self._port}")
            
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=10.0
            )
            log.info("TCP Command connection successful.")
            self.connection_status.emit("Connected. Authenticating...")

            receiver_task = asyncio.create_task(self.receive_loop())
            sender_task = asyncio.create_task(self.sender_loop())
            
            await asyncio.gather(receiver_task, sender_task)
            
        except asyncio.TimeoutError:
            log.error("Connection timed out.")
            self.connection_status.emit("Connection failed: Timeout.")
        except ConnectionRefusedError:
            log.error("Connection refused.")
            self.connection_status.emit("Connection failed: Refused.")
        except Exception as e:
            log.error(f"Error in main_loop: {e}", exc_info=True)
            self.connection_status.emit(f"Error: {e}")
        finally:
            if self._heartbeat_timer:
                self._heartbeat_timer.cancel()
            if self._writer and not self._writer.is_closing():
                self._writer.close()
                await self._writer.wait_closed()
            self.connection_status.emit("Disconnected.")
            
    async def sender_loop(self):
        """Pulls messages from the queue and sends them to the server."""
        while self._running:
            try:
                msg_type, payload = await self._message_queue.get()
                
                if msg_type == MessageType.HEARTBEAT:
                    # Don't stop loop if queue is empty, just send heartbeat
                    payload_bytes = b''
                    payload_len = 0
                else:
                    payload_bytes = json.dumps(payload).encode('utf-8')
                    payload_len = len(payload_bytes)

                header = struct.pack(
                    HEADER_FORMAT, MAGIC_NUMBER, PROTOCOL_VERSION,
                    msg_type.value, self.session_id or 0, payload_len
                )
                
                self._writer.write(header)
                if payload_len > 0:
                    self._writer.write(payload_bytes)
                await self._writer.drain()
                
                log.debug(f"Sent {msg_type.name} to server ({payload_len} bytes)")
                
            except ConnectionError as e:
                log.warning(f"ConnectionError in sender_loop: {e}.")
                self._running = False
                break
            except Exception as e:
                log.error(f"Error in sender_loop: {e}", exc_info=True)
                self._running = False
                break
                
    async def receive_loop(self):
        """Reads and dispatches messages from the server."""
        while self._running:
            try:
                # 1. Read header
                header_bytes = await self._reader.readexactly(HEADER_SIZE)
                if not header_bytes: break
                
                # 2. Unpack header
                magic, ver, msg_type_val, session_id, payload_len = struct.unpack(HEADER_FORMAT, header_bytes)
                
                # 3. Validate header
                if magic != MAGIC_NUMBER:
                    log.error("Invalid magic number from server. Disconnecting.")
                    break
                
                try:
                    msg_type = MessageType(msg_type_val)
                except ValueError:
                    log.error(f"Unknown message type {msg_type_val} from server.")
                    if payload_len > 0: # Discard unknown payload
                         await self._reader.readexactly(payload_len)
                    continue

                # 4. Read payload
                payload_data = {}
                if payload_len > 0:
                    payload_bytes = await self._reader.readexactly(payload_len)
                    if not payload_bytes: break
                    try:
                        payload_data = json.loads(payload_bytes.decode('utf-8'))
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        log.error(f"Failed to decode payload from server: {e}")
                        continue
                
                log.debug(f"Received {msg_type.name} from server ({payload_len} bytes)")
                
                # 5. Route message to GUI via signals
                if msg_type == MessageType.AUTHENTICATION_SUCCESS:
                    self.session_id = payload_data.get('session_id')
                    self.authentication_success.emit(
                        payload_data.get('message', 'Login successful!'),
                        self.session_id
                    )
                    # Start the heartbeat timer
                    self._heartbeat_timer = self._loop.create_task(self.heartbeat_task())
                
                elif msg_type == MessageType.AUTHENTICATION_FAILURE:
                    self.authentication_failure.emit(payload_data.get('message', 'Login failed.'))
                
                elif msg_type == MessageType.BROADCAST_TEXT_MESSAGE:
                    self.message_received.emit(
                        payload_data.get('username', 'Server'),
                        payload_data.get('text', '')
                    )
                
                elif msg_type == MessageType.PRESENCE_UPDATE:
                    self.presence_update.emit(payload_data.get('users', []))

                elif msg_type == MessageType.NOTIFY_SCREEN_SHARE_STARTED:
                    self.screen_share_started.emit(payload_data.get('username', 'Someone'))

                elif msg_type == MessageType.NOTIFY_SCREEN_SHARE_STOPPED:
                    self.screen_share_stopped.emit()
                    
                elif msg_type == MessageType.SCREEN_SHARE_DATA_FRAME:
                    self.screen_share_data_received.emit(payload_data.get('image_data', ''))

                elif msg_type == MessageType.FILE_TRANSFER_NOTIFY_BROADCAST:
                    self.file_notify_received.emit(
                        payload_data.get('file_id', ''),
                        payload_data.get('filename', 'unknown'),
                        payload_data.get('size', 0),
                        payload_data.get('uploader_name', 'Unknown')
                    )
                
                elif msg_type == MessageType.FILE_UPLOAD_START_RESPONSE:
                    self.file_upload_approved.emit(
                        payload_data.get('file_id'),
                        payload_data.get('filename')
                    )
                
                elif msg_type == MessageType.FILE_DOWNLOAD_START_RESPONSE:
                    self.file_download_approved.emit(
                        payload_data.get('file_id'),
                        payload_data.get('filename')
                    )
                
                elif msg_type == MessageType.SET_UDP_PORT_SUCCESS:
                    self.udp_ports_confirmed.emit()

                elif msg_type == MessageType.HEARTBEAT:
                    pass # Server just checking, no action needed

                else:
                    log.warning(f"Unhandled message type {msg_type.name} from server.")

            except asyncio.IncompleteReadError:
                log.info("Server closed the connection.")
                self._running = False
            except ConnectionError as e:
                log.warning(f"Connection error in receive_loop: {e}")
                self._running = False
            except Exception as e:
                log.error(f"Error in receive_loop: {e}", exc_info=True)
                self._running = False
                
    async def heartbeat_task(self):
        """Sends a heartbeat to the server every 10 seconds."""
        while self._running:
            try:
                self.post_message(MessageType.HEARTBEAT, {})
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                log.info("Heartbeat task cancelled.")
                break
            except Exception as e:
                log.error(f"Error in heartbeat task: {e}")
                break

    def post_message(self, msg_type: MessageType, payload: dict):
        """Thread-safe method for the GUI to send a message."""
        if self._loop and self._running:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._message_queue.put((msg_type, payload)), 
                    self._loop
                )
            except Exception as e:
                log.error(f"Failed to post message to queue: {e}")
        else:
            log.error("Network thread not running. Cannot send message.")

    def stop(self):
        """Stops the network thread."""
        log.info("Stopping network thread...")
        self._running = False
        if self._loop and self._loop.is_running():
            # Post a dummy message to wake up the sender loop
            self.post_message(MessageType.HEARTBEAT, {}) 
            # Schedule the loop to stop from its own thread
            self._loop.call_soon_threadsafe(self._loop.stop)
        self.wait() # Wait for the thread to finish

# --- Module 3: Screen Capture Thread ---
if mss:
    class ScreenCaptureThread(QThread):
        """
        Captures the screen at a fixed interval, compresses it,
        and emits the data for the NetworkClient to send over TCP.
        """
        frame_ready = Signal(str) # base64-encoded JPEG data
        
        def __init__(self, quality=50, resize_factor=0.5):
            super().__init__()
            self._running = True
            self.quality = quality
            self.resize_factor = resize_factor
            self.monitor = mss().monitors[1] # [0] is all monitors, [1] is primary
            log.info(f"Screen capture initialized for monitor: {self.monitor}")

        def run(self):
            log.info("Screen capture thread started.")
            with mss() as sct:
                while self._running:
                    try:
                        # 1. Capture screen
                        sct_img = sct.grab(self.monitor)
                        
                        # 2. Convert to PIL Image
                        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                        
                        # 3. Resize
                        new_size = (int(img.width * self.resize_factor), int(img.height * self.resize_factor))
                        img = img.resize(new_size, Image.LANCZOS)
                        
                        # 4. Compress to JPEG
                        buffer = sys.modules['io'].BytesIO()
                        img.save(buffer, format="JPEG", quality=self.quality)
                        
                        # 5. Base64 encode
                        jpeg_bytes = buffer.getvalue()
                        base64_data = base64.b64encode(jpeg_bytes).decode('utf-8')
                        
                        # 6. Emit signal
                        self.frame_ready.emit(base64_data)
                        
                        # Frame rate control (approx 15 FPS)
                        time.sleep(1 / 15) 
                        
                    except Exception as e:
                        log.error(f"Error in screen capture loop: {e}", exc_info=True)
                        time.sleep(1) # Avoid fast fail

            log.info("Screen capture thread stopped.")

        def stop(self):
            log.info("Stopping screen capture thread...")
            self._running = False
            self.wait()

# --- Module 5: File Transfer Thread ---
class FileTransferThread(QThread):
    """
    Handles a single file upload or download in a separate thread
    on the dedicated file transfer port (5001).
    """
    transfer_complete = Signal(str) # file_id
    transfer_error = Signal(str, str) # file_id, error_message
    transfer_progress = Signal(int) # percentage
    
    def __init__(self, mode, host, port, file_id, filepath):
        super().__init__()
        self.mode = mode # "UPLOAD" or "DOWNLOAD"
        self.host = host
        self.port = port
        self.file_id = file_id
        self.filepath = filepath # Local path
        self._running = True

    def run(self):
        log.info(f"Starting file transfer thread: {self.mode} {self.file_id}")
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self.host, self.port))
            
            # Send the header: [Mode (1 byte)][File_ID (36 bytes)]
            # Always send fixed 37 bytes: 1 byte mode + 36 ASCII ID
            header = f"{self.mode[0]}{self.file_id}".encode('ascii', 'ignore')[:37].ljust(37, b' ')
            s.sendall(header)

            
            if self.mode == "UPLOAD":
                self.run_upload(s)
            elif self.mode == "DOWNLOAD":
                self.run_download(s)
                
        except ConnectionRefusedError:
            log.error(f"File server connection refused at {self.host}:{self.port}")
            self.transfer_error.emit(self.file_id, "Connection refused.")
        except Exception as e:
            log.error(f"File transfer error during {self.mode} for {self.file_id}: {e}", exc_info=True)
            self.transfer_error.emit(self.file_id, str(e))
        finally:
            if s:
                s.close()
            self.transfer_progress.emit(0) # Clear progress bar
            log.info(f"File transfer thread for {self.file_id} finished.")

    def run_upload(self, sock):
        try:
            with open(self.filepath, 'rb') as f:
                file_size = os.path.getsize(self.filepath)
                bytes_sent = 0

                # 1. Send total file size (8 bytes) - THIS IS THE CRITICAL FIX
                sock.sendall(struct.pack('!Q', file_size))

                while self._running:
                    chunk = f.read(65536) # 64KB chunks
                    if not chunk:
                        break
                    
                    # 2. Send chunk size (4 bytes) - THIS IS THE CRITICAL FIX
                    sock.sendall(struct.pack('!I', len(chunk)))
                    
                    # 3. Send chunk data
                    sock.sendall(chunk)

                    bytes_sent += len(chunk)
                    progress = int((bytes_sent / file_size) * 100)
                    self.transfer_progress.emit(progress)
            
            if self._running:
                # 4. Wait for server acknowledgment
                ack = sock.recv(1)
                if ack == b'1':
                    self.transfer_complete.emit(self.file_id)
                elif ack == b'0':
                    self.transfer_error.emit(self.file_id, "Server reported an error during upload.")
                else:
                    self.transfer_error.emit(self.file_id, f"Unexpected server response: {ack}")

                    
        except FileNotFoundError:
            self.transfer_error.emit(self.file_id, "Local file not found.")
        except Exception as e:
            self.transfer_error.emit(self.file_id, f"Upload error: {e}")

    def run_download(self, sock):
        try:
            # 1. Read total file size (8 bytes)
            file_size_bytes = sock.recv(8)
            if len(file_size_bytes) < 8:
                raise ConnectionError("Server disconnected while sending file size.")
            file_size = struct.unpack('!Q', file_size_bytes)[0]
            bytes_received = 0

            with open(self.filepath, 'wb') as f:
                while self._running and bytes_received < file_size:
                    # 2. Read chunk size (4 bytes)
                    chunk_size_bytes = sock.recv(4)
                    if len(chunk_size_bytes) < 4:
                        raise ConnectionError("Server disconnected while sending chunk size.")
                    chunk_size = struct.unpack('!I', chunk_size_bytes)[0]
                    
                    # 3. Read chunk data (in a loop to ensure all bytes are received)
                    chunk_data = b''
                    while len(chunk_data) < chunk_size:
                        to_read = chunk_size - len(chunk_data)
                        packet = sock.recv(min(to_read, 65536))
                        if not packet:
                            raise ConnectionError("Server disconnected while sending chunk data.")
                        chunk_data += packet
                    
                    f.write(chunk_data)
                    bytes_received += len(chunk_data)

                    if file_size > 0:
                        progress = int((bytes_received / file_size) * 100)
                        self.transfer_progress.emit(progress)
            
            if self._running:
                # 4. Send acknowledgment
                sock.sendall(b'1')
                self.transfer_complete.emit(self.file_id)
                
        except Exception as e:
            self.transfer_error.emit(self.file_id, f"Download error: {e}")

    def stop(self):
        self._running = False
        
# --- Module 2: Audio Threads ---
if pyaudio and np:
    class AudioCaptureThread(QThread):
        """
        Captures audio from the microphone, adds a session ID header,
        and sends it over a UDP socket.
        """
        audio_error = Signal(str)
        
        CHUNK = 4096
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 44100
        
        def __init__(self, host, port, session_id):
            super().__init__()
            self.host = host
            self.port = port
            self.session_id_bytes = struct.pack(UDP_HEADER_FORMAT, session_id)
            self._running = True
            self.p = None
            self.stream = None
            self.sock = None

        def run(self):
            log.info(f"Audio capture thread started, sending to {self.host}:{self.port}")
            try:
                self.p = pyaudio.PyAudio()
                self.stream = self.p.open(format=self.FORMAT,
                                        channels=self.CHANNELS,
                                        rate=self.RATE,
                                        input=True,
                                        frames_per_buffer=self.CHUNK)
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            except Exception as e:
                log.error(f"Failed to initialize PyAudio: {e}")
                self.audio_error.emit(f"Failed to open microphone: {e}")
                return

            while self._running:
                try:
                    data = self.stream.read(self.CHUNK)
                    packet = self.session_id_bytes + data
                    self.sock.sendto(packet, (self.host, self.port))
                except IOError as e:
                    if self._running: # Don't log error if we're stopping
                        log.error(f"Audio capture IOError: {e}")
                except Exception as e:
                    if self._running:
                        log.error(f"Error in audio capture loop: {e}", exc_info=True)

            # --- Cleanup ---
            try:
                if self.sock:
                    self.sock.close()
                if self.stream:
                    self.stream.stop_stream()
                    self.stream.close()
                if self.p:
                    self.p.terminate()
            except Exception as e:
                log.error(f"Error during audio capture cleanup: {e}")
            log.info("Audio capture thread stopped.")

        def stop(self):
            log.info("Stopping audio capture thread...")
            self._running = False
            self.wait()

    class AudioPlayerThread(QThread):
        """
        Listens on a UDP port, receives mixed audio, and plays it.
        """
        audio_error = Signal(str)
        
        CHUNK = 4096
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 44100

        def __init__(self, listen_port):
            super().__init__()
            self.listen_port = listen_port
            self._running = True
            self.p = None
            self.stream = None
            self.sock = None

        def run(self):
            log.info(f"Audio player thread started, listening on UDP port {self.listen_port}")
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.bind(('', self.listen_port))
                self.sock.settimeout(1.0) # 1 second timeout
                
                self.p = pyaudio.PyAudio()
                self.stream = self.p.open(format=self.FORMAT,
                                        channels=self.CHANNELS,
                                        rate=self.RATE,
                                        output=True,
                                        frames_per_buffer=self.CHUNK)
            except Exception as e:
                log.error(f"Failed to initialize PyAudio output: {e}")
                self.audio_error.emit(f"Failed to open speakers: {e}")
                if self.sock: self.sock.close()
                return

            while self._running:
                try:
                    data, _ = self.sock.recvfrom(self.CHUNK * 2) # Double chunk size for buffer
                    if self._running:
                        self.stream.write(data)
                except socket.timeout:
                    continue # Just loop again
                except Exception as e:
                    if self._running:
                        log.error(f"Error in audio player loop: {e}", exc_info=True)
            
            # --- Cleanup ---
            try:
                if self.sock:
                    self.sock.close()
                if self.stream:
                    self.stream.stop_stream()
                    self.stream.close()
                if self.p:
                    self.p.terminate()
            except Exception as e:
                log.error(f"Error during audio player cleanup: {e}")
            log.info("Audio player thread stopped.")

        def stop(self):
            log.info("Stopping audio player thread...")
            self._running = False
            self.wait()

# --- Module 1: Video Threads ---
if cv2:
    class VideoCaptureThread(QThread):
        """
        Captures video from the webcam, compresses it, adds a header,
        and sends it over a UDP socket.
        """
        video_error = Signal(str)
        local_frame_ready = Signal(QImage) # FIX: Signal for local preview
        
        WIDTH = 640
        HEIGHT = 480
        QUALITY = 30 # JPEG quality
        
        def __init__(self, host, port, session_id):
            super().__init__()
            self.host = host
            self.port = port
            self.session_id_bytes = struct.pack(UDP_HEADER_FORMAT, session_id)
            self._running = True
            self.cap = None
            self.sock = None

        def run(self):
            log.info(f"Video capture thread started, sending to {self.host}:{self.port}")
            try:
                self.cap = cv2.VideoCapture(0)
                if not self.cap.isOpened():
                    raise IOError("Cannot open webcam")
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.WIDTH)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.HEIGHT)
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            except Exception as e:
                log.error(f"Failed to initialize OpenCV: {e}")
                self.video_error.emit(f"Failed to open webcam: {e}")
                return

            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), self.QUALITY]

            while self._running:
                try:
                    ret, frame = self.cap.read()
                    if not ret:
                        log.warning("Failed to grab frame from webcam")
                        time.sleep(0.1)
                        continue
                    
                    # 1. Emit local frame for preview
                    # Convert from BGR (OpenCV) to RGB (Qt)
                    rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h, w, ch = rgb_image.shape
                    bytes_per_line = ch * w
                    qt_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
                    self.local_frame_ready.emit(qt_image.copy()) # Emit a copy
                    
                    # 2. Resize and encode for network
                    frame = cv2.resize(frame, (self.WIDTH, self.HEIGHT))
                    ret, buffer = cv2.imencode('.jpg', frame, encode_param)
                    if not ret:
                        log.warning("Failed to encode JPEG")
                        continue
                    
                    # 3. Send over UDP
                    packet = self.session_id_bytes + buffer.tobytes()
                    
                    # Check packet size (UDP limit is ~65k)
                    if len(packet) > 65000:
                        log.warning(f"Video packet too large ({len(packet)} bytes). Skipping.")
                        continue
                        
                    self.sock.sendto(packet, (self.host, self.port))
                    
                    # ~15 FPS
                    time.sleep(1/15)

                except Exception as e:
                    if self._running:
                        log.error(f"Error in video capture loop: {e}", exc_info=True)
            
            # --- Cleanup ---
            try:
                if self.sock:
                    self.sock.close()
                if self.cap:
                    self.cap.release()
            except Exception as e:
                log.error(f"Error during video capture cleanup: {e}")
            log.info("Video capture thread stopped.")

        def stop(self):
            log.info("Stopping video capture thread...")
            self._running = False
            self.wait()

    class VideoPlayerThread(QThread):
        """
        Listens on a UDP port, receives video frames from other users,
        decodes them, and emits a QImage for the GUI to render.
        """
        video_error = Signal(str)
        remote_frame_ready = Signal(int, QImage) # session_id, QImage
        
        def __init__(self, listen_port):
            super().__init__()
            self.listen_port = listen_port
            self._running = True
            self.sock = None

        def run(self):
            log.info(f"Video player thread started, listening on UDP port {self.listen_port}")
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.bind(('', self.listen_port))
                self.sock.settimeout(1.0)
            except Exception as e:
                log.error(f"Failed to bind video player socket: {e}")
                self.video_error.emit(f"Failed to bind video port {self.listen_port}: {e}")
                return

            while self._running:
                try:
                    data, _ = self.sock.recvfrom(65536) # Max UDP packet size
                    
                    # 1. Unpack header
                    session_id = struct.unpack(UDP_HEADER_FORMAT, data[:UDP_HEADER_SIZE])[0]
                    
                    # 2. Get JPEG data
                    jpeg_bytes = data[UDP_HEADER_SIZE:]
                    
                    # 3. Decode JPEG
                    # Use QImage for direct decoding, it's faster than OpenCV
                    image = QImage()
                    if not image.loadFromData(jpeg_bytes, "JPEG"):
                        log.warning(f"Failed to decode JPEG from user {session_id}")
                        continue
                        
                    # 4. Emit signal
                    self.remote_frame_ready.emit(session_id, image)

                except socket.timeout:
                    continue # Just loop again
                except Exception as e:
                    if self._running:
                        log.error(f"Error in video player loop: {e}", exc_info=True)
            
            # --- Cleanup ---
            if self.sock:
                self.sock.close()
            log.info("Video player thread stopped.")

        def stop(self):
            log.info("Stopping video player thread...")
            self._running = False
            self.wait()

# --- View/Controller Layer (GUI) ---

class LoginPage(QWidget):
    """View for the login screen."""
    login_attempt = Signal(str, str,str) # host,username, password

    def __init__(self):
        super().__init__()
        self.setObjectName("LoginPage") # For styling
        # --- ADD SERVER IP INPUT ---
        self.server_ip_input = QLineEdit()
        self.server_ip_input.setPlaceholderText("e.g., 192.168.1.10 or localhost")
        # Try to get default from HOST if defined, else use 127.0.0.1
        default_host = HOST if 'HOST' in globals() and HOST else '127.0.0.1'
        self.server_ip_input.setText(default_host)
        # --- END ADD ---        
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Username (e.g., 'testuser')")
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password (e.g., 'pass123')")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        
        self.login_button = QPushButton("Login")
        self.status_label = QLabel("Please log in to connect to the server.")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("StatusLabel")

        form_layout = QFormLayout()
        form_layout.addRow("Server IP:", self.server_ip_input)
        form_layout.addRow("Username:", self.username_input)
        form_layout.addRow("Password:", self.password_input)
        form_layout.addRow(self.login_button)
        form_layout.addRow(self.status_label)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(15)
        
        form_widget = QWidget()
        form_widget.setLayout(form_layout)
        form_widget.setMaximumWidth(350)

        main_layout = QHBoxLayout()
        main_layout.addStretch()
        main_layout.addWidget(form_widget)
        main_layout.addStretch()
        self.setLayout(main_layout)

        self.login_button.clicked.connect(self.on_login_click)
        self.password_input.returnPressed.connect(self.on_login_click)
        
    def on_login_click(self):
        host = self.server_ip_input.text().strip() # <-- GET HOST
        username = self.username_input.text().strip()
        password = self.password_input.text()
        if host and username and password: # <-- CHECK HOST
            self.login_button.setEnabled(False)
            self.status_label.setText("Attempting connect & login...")
            self.status_label.setProperty("error", False)
            self.login_attempt.emit(host, username, password) # <-- EMIT HOST
        else:
            self.set_status("Server IP, Username, and Password cannot be empty.", is_error=True)
            
    def set_status(self, text, is_error=False):
        self.login_button.setEnabled(True)
        self.status_label.setText(text)
        self.status_label.setProperty("error", is_error)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)


class MainConferencePage(QWidget):
    """
    The main 'single window' view that holds all 5 modules.
    Acts as the 'View' in the MVC pattern.
    """
    # Signals for the controller
    send_message = Signal(str) # chat message text
    share_screen_toggled = Signal(bool) # checked
    upload_file_requested = Signal()
    download_file_requested = Signal(str) # file_id
    hang_up_requested = Signal()
    video_toggled = Signal(bool)
    mic_toggled = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setObjectName("MainConferencePage")
        self.user_video_widgets = {} # {session_id: QLabel}
        
        # --- Create Layouts ---
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(main_splitter, 1) # Give splitter stretch factor

        # --- 1. Main Stage (Left Panel) ---
        stage_widget = QWidget()
        stage_layout = QVBoxLayout(stage_widget)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        self.stage_stack = QStackedWidget()
        stage_layout.addWidget(self.stage_stack)
        
        # 1a. Video Grid Page (Module 1)
        self.video_grid_page = QWidget()
        self.video_grid_layout = QGridLayout(self.video_grid_page)
        
        # Add "You" video placeholder
        self.local_video_widget = self.create_video_placeholder("You (Camera Off)")
        self.local_video_widget.setObjectName("VideoLabel")
        self.video_grid_layout.addWidget(self.local_video_widget, 0, 0)
        
        # Add spacer widgets to keep grid organized
        self.video_grid_layout.setRowStretch(3, 1)
        self.video_grid_layout.setColumnStretch(3, 1)

        self.stage_stack.addWidget(self.video_grid_page)

        # 1b. Screen Share Page (Module 3)
        self.screen_share_page = QWidget()
        screen_share_layout = QVBoxLayout(self.screen_share_page)
        screen_share_layout.setContentsMargins(0, 0, 0, 0)
        
        self.presenter_label = QLabel("Receiving screen from...")
        self.presenter_label.setObjectName("PresenterLabel")
        self.presenter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.presenter_label.hide()
        
        self.screen_share_display = QLabel("No one is presenting.")
        self.screen_share_display.setObjectName("ScreenShareDisplay")
        self.screen_share_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen_share_display.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        screen_share_layout.addWidget(self.presenter_label)
        screen_share_layout.addWidget(self.screen_share_display, 1)
        self.stage_stack.addWidget(self.screen_share_page)
        
        self.stage_stack.setCurrentWidget(self.video_grid_page) # Default
        main_splitter.addWidget(stage_widget)

        # --- 2. Side Panel (Right) ---
        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_panel.setMinimumWidth(300)
        side_panel.setMaximumWidth(400)
        
        self.tab_widget = QTabWidget()
        side_layout.addWidget(self.tab_widget)
        
        # 2a. Chat Tab (Module 4)
        chat_tab = QWidget()
        chat_layout = QVBoxLayout(chat_tab)
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Type your message...")
        self.chat_input.setObjectName("ChatInput")
        chat_layout.addWidget(self.chat_display, 1)
        chat_layout.addWidget(self.chat_input)
        chat_layout.setContentsMargins(5, 5, 5, 5) # Padding inside the tab
        chat_tab.setLayout(chat_layout)
        self.tab_widget.addTab(chat_tab, "Chat")

        # 2b. Users Tab (Presence)
        users_tab = QWidget()
        users_layout = QVBoxLayout(users_tab)
        self.user_list_widget = QListWidget()
        users_layout.addWidget(self.user_list_widget)
        users_layout.setContentsMargins(5, 5, 5, 5)
        users_tab.setLayout(users_layout)
        self.tab_widget.addTab(users_tab, "Users (0)")
        
        # 2c. Files Tab (Module 5)
        files_tab = QWidget()
        files_layout = QVBoxLayout(files_tab)
        self.file_list_widget = QListWidget()
        self.file_list_widget.setToolTip("Double-click a file to download")
        self.upload_button = QPushButton("Upload File")
        self.upload_button.setObjectName("UploadButton")
        self.upload_button.setIcon(create_svg_icon(SVG_ICONS["upload"]))
        files_layout.addWidget(self.file_list_widget, 1)
        files_layout.addWidget(self.upload_button)
        files_layout.setContentsMargins(5, 5, 5, 5)
        files_tab.setLayout(files_layout)
        self.tab_widget.addTab(files_tab, "Files")

        main_splitter.addWidget(side_panel)
        main_splitter.setSizes([700, 300]) # Initial split

        # --- 3. Control Toolbar (Bottom) ---
        toolbar_widget = QWidget()
        toolbar_widget.setObjectName("ControlToolbar")
        toolbar_layout = QHBoxLayout(toolbar_widget)
        toolbar_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        toolbar_layout.setSpacing(15)
        toolbar_layout.setContentsMargins(20, 10, 20, 10)
        
        # Mic Button (Module 2)
        self.mic_button = self.create_toolbar_button(SVG_ICONS["mic_on"], "Mic", "Mute", True)
        self.mic_button.setChecked(False) # Start muted
        self.on_mic_toggled(False) # Set initial icon
        
        # Video Button (Module 1)
        self.video_button = self.create_toolbar_button(SVG_ICONS["video_on"], "Video", "Stop Video", True)
        self.video_button.setChecked(False) # Start with video off
        self.on_video_toggled(False) # Set initial icon

        # Screen Share Button (Module 3)
        self.share_button = self.create_toolbar_button(SVG_ICONS["screen_share"], "Share", "Share Screen", True)

        # Hang Up Button
        self.hangup_button = self.create_toolbar_button(SVG_ICONS["hang_up"], "Hang Up", "Hang Up", False)
        self.hangup_button.setObjectName("HangUpButton")
        
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.mic_button)
        toolbar_layout.addWidget(self.video_button)
        toolbar_layout.addWidget(self.share_button)
        toolbar_layout.addStretch(2)
        toolbar_layout.addWidget(self.hangup_button)
        toolbar_layout.addStretch()
        
        main_layout.addWidget(toolbar_widget)

        # --- Connect View signals to internal slots/emitters ---
        self.chat_input.returnPressed.connect(self.on_send_click)
        self.upload_button.clicked.connect(self.upload_file_requested)
        self.file_list_widget.itemDoubleClicked.connect(self.on_file_double_clicked)
        self.hangup_button.clicked.connect(self.hang_up_requested)
        self.share_button.toggled.connect(self.share_screen_toggled)
        self.video_button.toggled.connect(self.on_video_toggled)
        self.mic_button.toggled.connect(self.on_mic_toggled)
        
    def create_toolbar_button(self, icon_data, text, tooltip, is_checkable):
        """Helper to create styled QToolButtons for the toolbar."""
        button = QToolButton()
        button.setIcon(create_svg_icon(icon_data))
        button.setIconSize(QSize(24, 24))
        button.setText(text)
        button.setToolTip(tooltip)
        button.setCheckable(is_checkable)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setFixedSize(QSize(70, 70))
        return button

    def create_video_placeholder(self, text):
        """Helper to create a styled placeholder label."""
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("VideoLabel") # Use specific ID for styling
        label.setMinimumSize(320, 240)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Set black background
        label.setStyleSheet("background-color: #202225; color: #8e9297; font-size: 16px; border-radius: 8px;")
        return label

    # --- Internal View Slots ---
    def on_send_click(self):
        """Emits the send_message signal when user presses Enter."""
        text = self.chat_input.text().strip()
        if text:
            self.send_message.emit(text)
            self.chat_input.clear()
    
    @Slot(QListWidgetItem)
    def on_file_double_clicked(self, item):
        """Emits the download_file_requested signal."""
        file_id = item.data(Qt.ItemDataRole.UserRole)
        log.info(f"Requesting to download file: {file_id}")
        self.download_file_requested.emit(file_id)

    @Slot(bool)
    def on_mic_toggled(self, checked):
        """Internal slot to update mic button UI and emit signal."""
        if checked:
            self.mic_button.setIcon(create_svg_icon(SVG_ICONS["mic_on"]))
            self.mic_button.setToolTip("Mute")
        else:
            self.mic_button.setIcon(create_svg_icon(SVG_ICONS["mic_off"], "#f04747"))
            self.mic_button.setToolTip("Unmute")
        self.mic_toggled.emit(checked) # Notify controller
            
    @Slot(bool)
    def on_video_toggled(self, checked):
        """Internal slot to update video button UI and emit signal."""
        if checked:
            self.video_button.setIcon(create_svg_icon(SVG_ICONS["video_on"]))
            self.video_button.setToolTip("Stop Video")
            self.local_video_widget.setText("Starting camera...")
        else:
            self.video_button.setIcon(create_svg_icon(SVG_ICONS["video_off"], "#f04747"))
            self.video_button.setToolTip("Start Video")
            self.local_video_widget.setText("You (Camera Off)")
            self.local_video_widget.setPixmap(QPixmap()) # Clear pixmap
            self.local_video_widget.setStyleSheet("background-color: #202225; color: #8e9297; font-size: 16px; border-radius: 8px;")
        self.video_toggled.emit(checked) # Notify controller

    # --- Public Slots for Controller ---

    @Slot(str, str)
    def add_chat_message(self, username, text):
        """Slot to add a new message to the display."""
        username_color = "#e6c07b" # Gold
        if username == "You":
            username_color = "#61afef" # Blue
        elif username == "System":
            username_color = "#98c379" # Green
            
        message_html = f"""
        <div style="margin-bottom: 5px;">
            <span style="color: {username_color}; font-weight: 600;">{username}</span>:
            <span style="color: #dcddde;">{text}</span>
        </div>
        """
        self.chat_display.append(message_html)

    @Slot(list)
    def update_user_list(self, usernames):
        """Slot to update the list of online users."""
        self.user_list_widget.clear()
        self.user_list_widget.addItems(usernames)
        self.tab_widget.setTabText(1, f"Users ({len(usernames)})") # Update tab count
        
    @Slot(str, str, int, str)
    def add_file_to_list(self, file_id, filename, size, uploader_name):
        """Slot to add a new file to the file list."""
        if size < 1024: size_str = f"{size} B"
        elif size < 1024*1024: size_str = f"{size/1024:.1f} KB"
        else: size_str = f"{size/(1024*1024):.1f} MB"
            
        item_text = f"{filename} ({size_str})\nUploaded by: {uploader_name}"
        item = QListWidgetItem(item_text)
        item.setData(Qt.ItemDataRole.UserRole, file_id) # Store file_id
        self.file_list_widget.addItem(item)

    @Slot(str)
    def set_presenter(self, username):
        """Switch to screen share view."""
        self.presenter_label.setText(f"Receiving screen from {username}")
        self.presenter_label.show()
        self.screen_share_display.setText("") # Clear placeholder
        self.stage_stack.setCurrentWidget(self.screen_share_page)
        
        if username != "You (Presenting)":
            self.share_button.setDisabled(True)
            self.share_button.setToolTip("Someone else is presenting")
            self.share_button.setToolTip("Stop Sharing")
        else:
            # We are the presenter
            self.share_button.setToolTip("Stop Sharing")

    @Slot()
    def stop_presenting(self):
        """Switch back to video grid view."""
        self.presenter_label.hide()
        self.screen_share_display.setText("No one is presenting.")
        self.screen_share_display.setPixmap(QPixmap()) # Clear the image
        self.stage_stack.setCurrentWidget(self.video_grid_page)
        
        self.share_button.setDisabled(False)
        self.share_button.setToolTip("Share Screen")
        if self.share_button.isChecked():
            self.share_button.setChecked(False)

    @Slot(str)
    def update_screen_share_image(self, base64_data):
        """Decode base64 image data and display it."""
        if not base64_data: return
        try:
            image_data = base64.b64decode(base64_data)
            image = QImage()
            if not image.loadFromData(image_data, "JPEG"): return
            pixmap = QPixmap.fromImage(image)
            self.screen_share_display.setPixmap(pixmap.scaled(
                self.screen_share_display.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
        except Exception as e:
            log.error(f"Error decoding/displaying screen share frame: {e}")
            
    @Slot(QImage)
    def update_local_video_frame(self, image):
        """Display local webcam frame in the 'You' box."""
        pixmap = QPixmap.fromImage(image)
        self.local_video_widget.setText("") # Clear "Camera Off" text
        self.local_video_widget.setPixmap(pixmap.scaled(
            self.local_video_widget.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        ))
        
    @Slot(int, QImage)
    def update_remote_video_frame(self, session_id, image):
        """Display a remote user's video frame in their box."""
        if session_id in self.user_video_widgets:
            widget = self.user_video_widgets[session_id]
            widget.setText("") # Clear placeholder text
            pixmap = QPixmap.fromImage(image)
            widget.setPixmap(pixmap.scaled(
                widget.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
        else:
        # User widget doesn't exist yet, but the frame is here.
        # This can happen if the video packet arrives *before* the presence update.
        # We will just log this. The presence handler is responsible for creating widgets.
            log.warning(f"Received video frame for unknown session_id {session_id}. Waiting for presence update.")
            pass
            
    def add_video_widget(self, session_id, username):
        """Adds a new video widget to the grid for a new user."""
        if session_id in self.user_video_widgets:
            return # Already exists
            
        log.info(f"Adding video widget for {username} (ID: {session_id})")
        widget = self.create_video_placeholder(username)
        self.user_video_widgets[session_id] = widget
        
        # Add to grid
        # FIX: Corrected grid logic
        count = len(self.user_video_widgets) # 0-based index of new widget
        row = (count // 2) + 1 # Start on row 1 (0 is for local)
        col = count % 2
        self.video_grid_layout.addWidget(widget, row, col)

    def remove_video_widget(self, session_id):
        """Removes a video widget when a user leaves and re-grids the others."""
        if session_id in self.user_video_widgets:
            log.info(f"Removing video widget for ID: {session_id}")

            # 1. Clear all *remote* widgets from the grid layout
            for widget in self.user_video_widgets.values():
                self.video_grid_layout.removeWidget(widget)

            # 2. Remove the departing user's widget from our dictionary and delete it
            widget_to_delete = self.user_video_widgets.pop(session_id)
            widget_to_delete.deleteLater()

            # 3. Re-add all remaining remote widgets to the grid in order
            i = 0
            for session_id_key, widget_item in self.user_video_widgets.items():
                count = i # 0-based index
                row = (count // 2) + 1 # Start on row 1 (row 0 is for local video)
                col = count % 2
                self.video_grid_layout.addWidget(widget_item, row, col)
                i += 1

        else:
            log.warning(f"Tried to remove video widget for non-existent ID: {session_id}")

class MainWindow(QMainWindow):
    """
    The main application window. Acts as the primary
    Controller, managing views and all background threads.
    """
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("LAN-Based Communication Client")
        self.setGeometry(100, 100, 1200, 800)
        
        self.app.setStyleSheet(MODERN_STYLE_SHEET)
        
        # --- Page Management ---
        self.stack = QStackedWidget()
        self.login_page = LoginPage()
        self.conference_page = MainConferencePage()
        
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.conference_page)
        
        self.setCentralWidget(self.stack)
        self.stack.setCurrentWidget(self.login_page)

        # --- Status Bar ---
        self.status_bar = self.statusBar()
        self.status_progress = QProgressBar()
        self.status_progress.setRange(0, 100)
        self.status_progress.setValue(0)
        self.status_progress.setMaximumWidth(200)
        self.status_progress.setTextVisible(True)
        self.status_progress.setFormat("%p%")
        self.status_bar.addPermanentWidget(self.status_progress)
        self.status_progress.hide()
        self.status_bar.showMessage("Ready. Please log in.")
        
        # --- Model/Thread References ---
        self.session_id = None
        self.network_client = None # Will be created on login
        self.server_host = "" # Will store the host IP entered by user
        self.login_payload = {} # To store login details for sending *after* connect
        self.screen_capture_thread = None
        self.file_transfer_threads = {} # {file_id: thread}
        self.audio_capture_thread = None
        self.audio_player_thread = None
        self.video_capture_thread = None
        self.video_player_thread = None
        
        # --- UDP Port Management ---
        self.my_audio_port = get_free_port()
        self.my_video_port = get_free_port()
        log.info(f"Reserved UDP ports: Audio={self.my_audio_port}, Video={self.my_video_port}")
        
        # --- Controller State ---
        self.known_users = {} # {session_id: username}
        self.known_files = {} # {file_id: {filename, ...}}
        
        # --- Connect Model Signals to Controller Slots ---
        # self.network_client.connection_status.connect(self.on_connection_status)
        # self.network_client.authentication_success.connect(self.on_auth_success)
        # self.network_client.authentication_failure.connect(self.on_auth_failure)
        
        # self.network_client.message_received.connect(self.conference_page.add_chat_message)
        # self.network_client.presence_update.connect(self.on_presence_update)
        
        # self.network_client.screen_share_started.connect(self.conference_page.set_presenter)
        # self.network_client.screen_share_stopped.connect(self.conference_page.stop_presenting)
        # self.network_client.screen_share_data_received.connect(self.conference_page.update_screen_share_image)
        
        # self.network_client.file_notify_received.connect(self.on_file_notify)
        # self.network_client.file_upload_approved.connect(self.on_file_upload_approved)
        # self.network_client.file_download_approved.connect(self.on_file_download_approved)

        # --- Connect View Signals to Controller Slots ---
        self.login_page.login_attempt.connect(self.on_login_attempt)
        
        self.conference_page.send_message.connect(self.on_send_chat_message)
        self.conference_page.share_screen_toggled.connect(self.on_share_screen_toggled)
        self.conference_page.upload_file_requested.connect(self.on_upload_file)
        self.conference_page.download_file_requested.connect(self.on_download_file)
        self.conference_page.hang_up_requested.connect(self.close) # Main window's close
        
        self.conference_page.video_toggled.connect(self.on_video_toggled)
        self.conference_page.mic_toggled.connect(self.on_mic_toggled)

        # Start the network thread
        # self.network_client.start()
        
    # --- Controller Slots for Model Signals ---
    
    @Slot(str)
    def on_connection_status(self, status):
        """Update status bar based on network state."""
        self.status_bar.showMessage(status)
        if "Connecting" in status or "Authenticating" in status:
            self.login_page.set_status(status)
        elif "failed" in status.lower() or "Error" in status:
            self.login_page.set_status(status, is_error=True)

    @Slot(str, int)
    def on_auth_success(self, message, session_id):
        """Controller logic on successful login."""
        self.status_bar.showMessage(message)
        self.session_id = session_id
        
        # --- Start all media player threads ---
        self.start_media_players()
        
        # --- Tell server our UDP ports ---
        self.network_client.post_message(MessageType.SET_UDP_PORT_REQUEST, {
            'audio_port': self.my_audio_port,
            'video_port': self.my_video_port
        })
        
        self.stack.setCurrentWidget(self.conference_page)
        self.conference_page.add_chat_message("System", message)
        
    @Slot(str)
    def on_auth_failure(self, message):
        """Controller logic on failed login."""
        self.login_page.set_status(message, is_error=True)

    @Slot(list)
    def on_presence_update(self, user_list_dicts):
        """Handle user list update from server (list of {'id': id, 'username': name})."""

        # 1. Create a simple list of usernames for the side panel
        usernames = [user['username'] for user in user_list_dicts]
        self.conference_page.update_user_list(usernames)

        # 2. Update our internal map
        new_known_users = {user['id']: user['username'] for user in user_list_dicts}

        # 3. Find users who are NEW or who JOINED
        for session_id, username in new_known_users.items():
            if session_id == self.session_id: # Don't add ourself to the video grid
                continue
            if session_id not in self.known_users:
                # This is a new user
                log.info(f"User joined: {username} (ID: {session_id})")
                self.conference_page.add_video_widget(session_id, username)

        # 4. Find users who LEFT
        for session_id in list(self.known_users.keys()): # Use list() to allow modification
            if session_id not in new_known_users:
                # This user left
                if session_id == self.session_id: continue # Should not happen
                log.info(f"User left: {self.known_users[session_id]} (ID: {session_id})")
                self.conference_page.remove_video_widget(session_id)

        # 5. Store the new map for the next comparison
        self.known_users = new_known_users
        
    @Slot(str, str, int, str)
    def on_file_notify(self, file_id, filename, size, uploader_name):
        """A new file is available. Add to UI and local registry."""
        if file_id not in self.known_files:
            self.known_files[file_id] = {'filename': filename, 'size': size}
            self.conference_page.add_file_to_list(file_id, filename, size, uploader_name)

    @Slot(str, str)
    def on_file_upload_approved(self, file_id, filename):
        """Server confirmed our file. Find the local path and start the upload thread."""

        # --- THIS IS THE FIX ---
        # 1. Find the temporary local path we stored using the filename
        temp_file_key = f"local_{filename}"
        if temp_file_key not in self.known_files:
            log.error(f"Cannot start upload for {file_id}. Original local path for '{filename}' not found.")
            return

        filepath = self.known_files[temp_file_key]['local_path']

        # 2. Now, create the *real* file entry with the real file_id
        # and store the local path there.
        if file_id not in self.known_files:
            # This file might also be known from a broadcast, so check first
            self.known_files[file_id] = {}
        self.known_files[file_id]['local_path'] = filepath

        # 3. Clean up the temporary entry
        del self.known_files[temp_file_key]
        # --- END OF FIX ---

        log.info(f"Server approved upload for {file_id}. Starting transfer...")
        self.status_progress.setValue(0)
        self.status_progress.show()

        thread = FileTransferThread("UPLOAD", self.server_host, TCP_FILE_PORT, file_id, filepath)
        thread.transfer_complete.connect(self.on_transfer_complete)
        thread.transfer_error.connect(self.on_transfer_error)
        thread.transfer_progress.connect(self.status_progress.setValue)

        self.file_transfer_threads[file_id] = thread
        log.info(f"Starting UPLOAD thread for file_id {file_id} to {self.server_host}:{TCP_FILE_PORT}") # <-- ADD THIS
        thread.start()
    @Slot(str, str, int, str)
    def on_file_notify(self, file_id, filename, size, uploader_name):
        """
        Called when the server broadcasts that a new file is available.
        Updates the file list widget in the UI.
        """
        log.info(f"New file available: {filename} ({size} bytes) from {uploader_name}")

        # Create display text for the list
        display_text = f"{filename} ({size/1024:.1f} KB) - by {uploader_name}"

        # Create list item and store file_id for reference
        item = QListWidgetItem(display_text)
        item.setData(Qt.UserRole, file_id)

        # Add to the file list widget on the conference page
        try:
            self.conference_page.file_list_widget.addItem(item)
        except AttributeError:
            log.warning("File list widget not found in conference_page.")

        
        
    @Slot(str, str)
    def on_file_download_approved(self, file_id, filename):
        """Server confirmed our download. Start the download thread."""
        local_save_path = self.known_files[file_id].get('local_save_path')
        if not local_save_path:
            log.error(f"Cannot start download for {file_id}, save path unknown.")
            return
            
        log.info(f"Server approved download for {file_id}. Starting transfer...")
        self.status_progress.setValue(0)
        self.status_progress.show()
        
        thread = FileTransferThread("DOWNLOAD", self.server_host, TCP_FILE_PORT, file_id, local_save_path)
        thread.transfer_complete.connect(self.on_transfer_complete)
        thread.transfer_error.connect(self.on_transfer_error)
        thread.transfer_progress.connect(self.status_progress.setValue)
        
        self.file_transfer_threads[file_id] = thread
        log.info(f"Starting DOWNLOAD thread for file_id {file_id} from {self.server_host}:{TCP_FILE_PORT}")
        thread.start()

    # --- Controller Slots for View Signals ---
    
    
    @Slot(str, str, str)
    def on_login_attempt(self, host, username, password):
        """Controller logic to create/start network client and store login details."""

        # Store login details & host
        self.server_host = host # Store the host for other threads
        self.login_payload = {'username': username, 'password': password}

        # --- Gracefully handle switching servers ---
        if self.network_client and self.network_client.isRunning():
            if self.network_client._host == host:
                # Same server, just re-sending login attempt
                log.info(f"Re-attempting login to {host} as {username}")
                self.network_client.post_message(MessageType.AUTHENTICATION_REQUEST, self.login_payload)
                return
            else:
                # Different server! Stop everything first.
                log.info(f"Switching server connection from {self.network_client._host} to {host}")
                self.stop_all_threads() # Stop network and media threads
                # Clear UI elements from old session
                self.conference_page.user_list_widget.clear()
                self.conference_page.file_list_widget.clear()
                self.conference_page.chat_display.clear()
                for widget in self.conference_page.user_video_widgets.values():
                    self.conference_page.video_grid_layout.removeWidget(widget)
                    widget.deleteLater()
                self.conference_page.user_video_widgets.clear()
                self.known_users.clear()
                self.known_files.clear()
        # --- End server switching logic ---

        log.info(f"Attempting new connection to {host}:{TCP_COMMAND_PORT}...")
        self.status_bar.showMessage(f"Connecting to {host}...")

        # 1. Create the new network client
        self.network_client = NetworkClient(self.server_host, TCP_COMMAND_PORT)
        # --- Connect Model Signals to Controller Slots ---
        self.network_client.connection_status.connect(self.on_connection_status)
        self.network_client.authentication_success.connect(self.on_auth_success)
        self.network_client.authentication_failure.connect(self.on_auth_failure)

        self.network_client.message_received.connect(self.conference_page.add_chat_message)
        self.network_client.presence_update.connect(self.on_presence_update)

        self.network_client.screen_share_started.connect(self.conference_page.set_presenter)
        self.network_client.screen_share_stopped.connect(self.conference_page.stop_presenting)
        self.network_client.screen_share_data_received.connect(self.conference_page.update_screen_share_image)

        self.network_client.file_notify_received.connect(self.on_file_notify)
        self.network_client.file_upload_approved.connect(self.on_file_upload_approved)
        self.network_client.file_download_approved.connect(self.on_file_download_approved)

        # 2. Connect ALL signals again (required for new instance)


        # 3. Add temporary signal to send login *after* connection established
        self.network_client.connection_status.connect(self.on_initial_connection_for_login)

        # 4. Start the new network client thread
        self.network_client.start()

    @Slot(str)
    def on_initial_connection_for_login(self, status):
        """
        Special slot connected temporarily to connection_status.
        Waits for the 'Connected. Authenticating...' signal, then sends
        the stored login credentials. Disconnects itself afterwards.
        """
        if "Connected. Authenticating..." in status:
            log.info("Connection successful, sending login credentials...")
            # Now that TCP connection is up, send the stored login payload
            if self.login_payload:
                self.network_client.post_message(MessageType.AUTHENTICATION_REQUEST, self.login_payload)
            else:
                log.warning("Login requested but no payload was stored.")
            # Disconnect this temporary slot - it's done its job
            try:
                self.network_client.connection_status.disconnect(self.on_initial_connection_for_login)
            except RuntimeError: # Already disconnected maybe
                pass
        elif "failed" in status.lower() or "Error" in status or "Disconnected" in status:
            log.warning(f"Connection attempt failed or disconnected before login could be sent ({status}).")
            # Disconnect this temporary slot
            try:
                if self.network_client: # Check if client exists
                    self.network_client.connection_status.disconnect(self.on_initial_connection_for_login)
            except RuntimeError: # Already disconnected maybe
                pass
            # Re-enable login button via login page status update
            self.login_page.set_status(f"Connection Failed: {status}", is_error=True)    

    @Slot(str)
    def on_send_chat_message(self, text):
        """Controller logic to send chat message to model."""
        payload = {'text': text}
        self.network_client.post_message(MessageType.SEND_TEXT_MESSAGE, payload)
        self.conference_page.add_chat_message("You", text)

    @Slot(bool)
    def on_share_screen_toggled(self, checked):
        """User clicked the 'Share Screen' button."""
        if not mss:
            self.conference_page.add_chat_message("System", "Screen sharing libraries not found.")
            return
            
        if checked:
            log.info("Requesting to start screen share...")
            self.network_client.post_message(MessageType.SCREEN_SHARE_START_REQUEST, {})
            self.conference_page.set_presenter("You (Presenting)")
            
            # Start the capture thread
            self.screen_capture_thread = ScreenCaptureThread()
            self.screen_capture_thread.frame_ready.connect(self.on_screen_frame_ready)
            self.screen_capture_thread.start()
            self.conference_page.add_chat_message("System", "You started sharing your screen.")
            
        else:
            log.info("Requesting to stop screen share...")
            if self.screen_capture_thread:
                self.screen_capture_thread.stop()
                self.screen_capture_thread = None
            
            self.network_client.post_message(MessageType.SCREEN_SHARE_STOP_REQUEST, {})
            self.conference_page.add_chat_message("System", "You stopped sharing your screen.")
            # UI will be updated when server confirms with NOTIFY_SCREEN_SHARE_STOPPED
            
    @Slot(str)
    def on_screen_frame_ready(self, base64_data):
        """A new screen frame is ready. Send it to the server."""
        payload = {'image_data': base64_data}
        self.network_client.post_message(MessageType.SCREEN_SHARE_DATA_FRAME, payload)

    @Slot()
    def on_upload_file(self):
        """User clicked 'Upload File', show file dialog."""
        filepath, _ = QFileDialog.getOpenFileName(self, "Select File to Share")
        if filepath:
            file_info = QFileInfo(filepath)
            filename = file_info.fileName()
            size = file_info.size()

            log.info(f"Notifying server of file: {filename} ({size} bytes)")

            # --- THIS IS THE FIX ---
            # 1. Store the local path, keyed by the FILENAME.
            # We'll retrieve this when the server approves the upload.
            self.known_files[f"local_{filename}"] = {'local_path': filepath}

            # 2. Send notification to server
            payload = {'filename': filename, 'size': size}
            self.network_client.post_message(MessageType.FILE_TRANSFER_NOTIFY_REQUEST, payload)

            self.conference_page.add_chat_message("System", f"Notifying server about '{filename}'...")

    @Slot(str)
    def on_download_file(self, file_id):
        """User double-clicked a file. Show 'Save As' dialog."""
        if file_id not in self.known_files:
            log.error(f"Unknown file_id {file_id} requested for download.")
            return
            
        filename = self.known_files[file_id]['filename']
        
        # FIX: Correctly get save path *before* sending request
        save_path, _ = QFileDialog.getSaveFileName(self, "Save File As...", filename)
        
        if save_path:
            # Store the path
            self.known_files[file_id]['local_save_path'] = save_path
            
            # Now, tell the server we want this file
            payload = {'file_id': file_id}
            self.network_client.post_message(MessageType.FILE_DOWNLOAD_REQUEST, payload)
            self.conference_page.add_chat_message("System", f"Requesting to download '{filename}'...")
        else:
            log.info("File download cancelled by user.")

    # --- Media Thread Management ---
    def start_media_players(self):
        """Start the UDP listeners for audio and video."""
        if pyaudio and np:
            log.info(f"Starting audio player on port {self.my_audio_port}")
            self.audio_player_thread = AudioPlayerThread(self.my_audio_port)
            self.audio_player_thread.audio_error.connect(self.on_media_error)
            self.audio_player_thread.start()
        else:
            log.warning("Audio libraries not found. Audio playback disabled.")

        if cv2:
            log.info(f"Starting video player on port {self.my_video_port}")
            self.video_player_thread = VideoPlayerThread(self.my_video_port)
            self.video_player_thread.video_error.connect(self.on_media_error)
            self.video_player_thread.remote_frame_ready.connect(
                self.conference_page.update_remote_video_frame
            )
            self.video_player_thread.start()
        else:
            log.warning("OpenCV not found. Video playback disabled.")
            
    @Slot(bool)
    def on_video_toggled(self, checked):
        """User toggled video button."""
        if not cv2:
            self.conference_page.add_chat_message("System", "OpenCV library not found. Video disabled.")
            return
            
        if checked:
            if not self.video_capture_thread:
                log.info("Starting video capture...")
                self.video_capture_thread = VideoCaptureThread(
                    self.server_host, UDP_VIDEO_PORT, self.session_id
                )
                self.video_capture_thread.video_error.connect(self.on_media_error)
                # FIX: Connect local preview signal
                self.video_capture_thread.local_frame_ready.connect(
                    self.conference_page.update_local_video_frame
                )
                self.video_capture_thread.start()
                self.conference_page.add_chat_message("System", "Your video is now on.")
        else:
            if self.video_capture_thread:
                log.info("Stopping video capture...")
                self.video_capture_thread.stop()
                self.video_capture_thread = None
                self.conference_page.add_chat_message("System", "Your video is now off.")

        # Tell the server our state
        self.network_client.post_message(MessageType.SET_MEDIA_STATE_REQUEST, {
            'media_type': 'video',
            'state': checked
        })

    @Slot(bool)
    def on_mic_toggled(self, checked):
        """User toggled mic button."""
        if not pyaudio or not np:
            self.conference_page.add_chat_message("System", "PyAudio/Numpy not found. Audio disabled.")
            return
            
        if checked:
            if not self.audio_capture_thread:
                log.info("Starting audio capture...")
                self.audio_capture_thread = AudioCaptureThread(
                    self.server_host, UDP_AUDIO_PORT, self.session_id
                )
                self.audio_capture_thread.audio_error.connect(self.on_media_error)
                self.audio_capture_thread.start()
                self.conference_page.add_chat_message("System", "Your microphone is now on.")
        else:
            if self.audio_capture_thread:
                log.info("Stopping audio capture...")
                self.audio_capture_thread.stop()
                self.audio_capture_thread = None
                self.conference_page.add_chat_message("System", "You are now muted.")
                
        # Tell the server our state
        self.network_client.post_message(MessageType.SET_MEDIA_STATE_REQUEST, {
            'media_type': 'audio',
            'state': checked
        })

    @Slot(str)
    def on_media_error(self, error_message):
        """Show media-related errors to the user."""
        log.error(f"Media Error: {error_message}")
        self.conference_page.add_chat_message("System", f"Media Error: {error_message}")
        # TODO: Disable the relevant button
        
    # --- File Transfer Callbacks ---
    @Slot(str)
    def on_transfer_complete(self, file_id):
        log.info(f"File transfer complete for {file_id}")
        self.status_progress.hide()
        self.status_bar.showMessage(f"File transfer '{self.known_files[file_id]['filename']}' complete.", 5000)
        self.cleanup_file_thread(file_id)

    @Slot(str, str)
    def on_transfer_error(self, file_id, error_message):
        log.error(f"File transfer error for {file_id}: {error_message}")
        self.status_progress.hide()
        self.status_bar.showMessage(f"File transfer error: {error_message}", 5000)
        self.cleanup_file_thread(file_id)
        
    def cleanup_file_thread(self, file_id):
        if file_id in self.file_transfer_threads:
            self.file_transfer_threads[file_id].quit() # Stop thread
            self.file_transfer_threads[file_id].wait() # Wait for it
            del self.file_transfer_threads[file_id]

    def stop_all_threads(self):
        """Gracefully stop all running threads before exit."""
        log.info("Stopping all background threads...")
        if self.network_client and self.network_client.isRunning():
            self.network_client.stop()
        if self.screen_capture_thread:
            self.screen_capture_thread.stop()
        if self.audio_capture_thread:
            self.audio_capture_thread.stop()
        if self.audio_player_thread:
            self.audio_player_thread.stop()
        if self.video_capture_thread:
            self.video_capture_thread.stop()
        if self.video_player_thread:
            self.video_player_thread.stop()
        for thread in self.file_transfer_threads.values():
            thread.stop()
            thread.wait()
        log.info("All threads stopped.")

    def closeEvent(self, event):
        """Ensure all threads are stopped on exit."""
        self.stop_all_threads()
        event.accept()

def main():
    # Set thread name for main GUI thread
    threading.current_thread().name = "MainGUIThread"
    
    app = QApplication(sys.argv)
    
    # Check for missing libraries
    missing = []
    if not pyaudio: missing.append("PyAudio (Audio)")
    if not np: missing.append("Numpy (Audio)")
    if not cv2: missing.append("opencv-python (Video)")
    if not mss: missing.append("mss (Screen Share)")
    if not Image: missing.append("Pillow (Screen Share)")
    
    if missing:
        msg = "The following libraries are missing and their features will be disabled:\n\n"
        msg += "\n".join(missing)
        msg += "\n\nPlease install them using 'pip install ...'"
        QMessageBox.warning(None, "Missing Libraries", msg)

    window = MainWindow(app) # Pass the app instance
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
