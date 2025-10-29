"""
LAN-Based Communication Server (Complete - All 5 Modules)

This server application implements the backend for a multi-user communication
suite operating exclusively over a LAN. It handles:
- Module 1: Video Conferencing (UDP)
- Module 2: Audio Conferencing (UDP)
- Module 3: Screen Sharing (TCP)
- Module 4: Group Text Chat (TCP)
- Module 5: File Sharing (TCP)

It runs four independent asyncio servers on different ports:
- TCP Command Server (Port 5000): Handles login, chat, presence, and commands.
- TCP File Server (Port 5001): Handles high-speed file transfers.
- UDP Audio Server (Port 5002): Handles audio stream mixing.
- UDP Video Server (Port 5003): Handles video stream forwarding (SFU).

Required Libraries:
- PySide6 (for the optional UI)
- numpy (for audio mixing)
"""

import sys
import asyncio
import struct
import json
import logging
import threading
import os
import uuid
from enum import IntEnum
from collections import defaultdict

import numpy as np
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QTextEdit, QListWidget, QSplitter, QLabel
)
from PySide6.QtCore import QObject, QThread, Signal, Qt, Slot
from PySide6.QtGui import QColor

# --- Configuration ---
HOST = '192.168.100.2'  # Listen on all available network interfaces
TCP_COMMAND_PORT = 5000
TCP_FILE_PORT = 5001
UDP_AUDIO_PORT = 5002
UDP_VIDEO_PORT = 5003
LOG_LEVEL = logging.DEBUG
HEARTBEAT_INTERVAL = 10  # seconds
CLIENT_TIMEOUT = 30  # seconds
UPLOADS_DIR = "uploads"  # Directory to store shared files

# --- Protocol Constants ---
MAGIC_NUMBER = 0xDEADBEEF
PROTOCOL_VERSION = 0x0100  # v1.0
HEADER_FORMAT = '!IHHQI'  # Magic, Version, Type, SessionID, PayloadLen
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
UDP_HEADER_FORMAT = '!Q'  # SessionID
UDP_HEADER_SIZE = struct.calcsize(UDP_HEADER_FORMAT)

# --- Message Types (Must match client.py) ---
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
    SCREEN_SHARE_DATA_FRAME = 304  # Renamed from _BROADCAST
    
    # File Sharing (Module 5 - TCP)
    FILE_TRANSFER_NOTIFY_REQUEST = 400
    FILE_TRANSFER_NOTIFY_BROADCAST = 401
    FILE_DOWNLOAD_REQUEST = 402
    FILE_UPLOAD_START_REQUEST = 403
    FILE_UPLOAD_START_RESPONSE = 404
    FILE_DOWNLOAD_START_REQUEST = 405
    FILE_DOWNLOAD_START_RESPONSE = 406
    FILE_TRANSFER_ERROR = 407
    
    # Audio/Video (UDP)
    SET_UDP_PORT_REQUEST = 500
    SET_UDP_PORT_SUCCESS = 501
    SET_MEDIA_STATE_REQUEST = 502

# --- Server Admin UI ---
# This optional UI runs in the main thread and displays logs/users
# from the asyncio threads.

class QLogHandler(QObject, logging.Handler):
    """
    A custom logging handler that emits a signal
    for each log message, which the GUI can connect to.
    """
    log_signal = Signal(str, str)  # levelname, message

    def __init__(self):
        QObject.__init__(self)
        logging.Handler.__init__(self)
        self.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] (%(threadName)s) %(message)s',
            '%H:%M:%S'
        ))

    def emit(self, record):
        """Overrides the default emit to send a signal."""
        try:
            msg = self.format(record)
            self.log_signal.emit(record.levelname, msg)
        except Exception:
            self.handleError(record)

# Define a global logger
log = logging.getLogger(__name__)
log.setLevel(LOG_LEVEL)
log_handler = QLogHandler()
log.addHandler(log_handler)
# Also log to console
log.addHandler(logging.StreamHandler(sys.stdout))


class ServerWindow(QMainWindow):
    """The main GUI window for the server admin panel."""
    
    # Signal to update the user list from the asyncio thread
    update_user_list_signal = Signal(list)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle(f"LAN Communication Server (Listening on {HOST})")
        self.setGeometry(100, 100, 800, 600)

        # Apply dark mode stylesheet
        self.app.setStyleSheet("""
            QWidget {
                background-color: #36393f;
                color: #dcddde;
                font-family: 'Inter', 'Segoe UI', Arial, sans-serif;
            }
            QMainWindow {
                border: 1px solid #2b2d31;
            }
            QListWidget, QTextEdit {
                background-color: #2f3136;
                border: 1px solid #2b2d31;
                border-radius: 5px;
                padding: 5px;
            }
            QLabel {
                font-weight: 600;
                color: #b9bbbe;
                padding-left: 5px;
            }
            QSplitter::handle {
                background-color: #2b2d31;
            }
        """)

        # Main layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        
        # Splitter to divide users and logs
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left panel: Connected Users
        user_panel = QWidget()
        user_layout = QVBoxLayout(user_panel)
        user_layout.setContentsMargins(0, 0, 0, 0)
        user_label = QLabel("CONNECTED USERS (0)")
        self.user_list_widget = QListWidget()
        user_layout.addWidget(user_label)
        user_layout.addWidget(self.user_list_widget)
        splitter.addWidget(user_panel)

        # Right panel: Server Logs
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_label = QLabel("SERVER LOG")
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        log_layout.addWidget(log_label)
        log_layout.addWidget(self.log_display)
        splitter.addWidget(log_panel)
        
        splitter.setSizes([200, 600])  # Initial sizes
        main_layout.addWidget(splitter)
        self.setCentralWidget(main_widget)

        # --- Connect signals ---
        log_handler.log_signal.connect(self.add_log_message)
        self.update_user_list_signal.connect(self.update_user_list)
        self.user_label = user_label  # Store for later update

    @Slot(str, str)
    def add_log_message(self, levelname, message):
        """Appends a log message to the text display."""
        # Color-code the log levels
        color = "#dcddde"  # Default (light grey)
        if levelname == "WARNING":
            color = "#faa61a"  # Yellow/Orange
        elif levelname == "ERROR" or levelname == "CRITICAL":
            color = "#f04747"  # Red
        elif levelname == "INFO":
            color = "#43b581"  # Green
            
        html_message = f'<span style="color: {color};">{message}</span>'
        self.log_display.append(html_message)
        
        # Auto-scroll to the bottom
        self.log_display.verticalScrollBar().setValue(
            self.log_display.verticalScrollBar().maximum()
        )

    @Slot(list)
    def update_user_list(self, user_list):
        """Clears and repopulates the user list widget."""
        self.user_list_widget.clear()
        self.user_list_widget.addItems(user_list)
        self.user_label.setText(f"CONNECTED USERS ({len(user_list)})")

# --- Core Server Logic (Asyncio) ---

class ServerProtocol:
    """Base class for our UDP protocols to share state."""
    def __init__(self, server_state):
        self.server_state = server_state

class AudioProtocol(ServerProtocol, asyncio.DatagramProtocol):
    """
    Handles Module 2: Audio Conferencing (UDP).
    Receives audio chunks from all clients and mixes them.
    """
    def __init__(self, server_state):
        super().__init__(server_state)
        self.transport = None
        self.chunk_size = 4096  # Should match client
        self.dtype = np.int16  # Should match client

    def connection_made(self, transport):
        self.transport = transport
        log.info(f"Audio Server (UDP) started on port {UDP_AUDIO_PORT}")

    def datagram_received(self, data, addr):
        try:
            # 1. Extract session ID
            session_id = struct.unpack(UDP_HEADER_FORMAT, data[:UDP_HEADER_SIZE])[0]
            
            # 2. Get client info
            client = self.server_state.get_client_by_id(session_id)
            if not client:
                # log.warning(f"Audio data from unknown session ID {session_id}")
                return
            
            # 3. Store the audio chunk
            audio_data = data[UDP_HEADER_SIZE:]
            if len(audio_data) == self.chunk_size * 2: # 2 bytes per int16
                client['audio_chunk'] = np.frombuffer(audio_data, dtype=self.dtype)
            else:
                # log.warning(f"Received malformed audio packet from {client['username']}")
                pass
                
        except Exception as e:
            log.error(f"Error in AudioProtocol.datagram_received: {e}", exc_info=True)

    async def broadcast_mixed_audio(self):
        """
        Continuously mixes audio from all active clients and broadcasts
        a custom mix (N-1) to each client that excludes their own audio.
        This runs in its own asyncio task.
        """
        log.info("Starting N-1 audio mixing loop...")

        # Create a reusable silent chunk
        silent_chunk_bytes = np.zeros(self.chunk_size, dtype=self.dtype).tobytes()

        while True:
            try:
                # 1. Get audio chunks from all *unmuted* clients
                chunks_by_id = {}
                clients_to_send = []

                all_clients = self.server_state.get_all_clients()

                for client in all_clients:
                    if client.get('audio_port') and client.get('is_unmuted'):
                        chunk = client.get('audio_chunk')
                        if chunk is not None:
                            chunks_by_id[client['id']] = chunk
                            client['audio_chunk'] = None # Consume the chunk

                    if client.get('audio_port'):
                        clients_to_send.append(client)

                if not clients_to_send:
                    await asyncio.sleep(0.02) # Sleep briefly
                    continue

                # 2. Iterate through each *destination* client
                for client_to in clients_to_send:
                    chunks_for_this_client = []

                    # 3. Gather all chunks *except* the client's own
                    for sender_id, chunk in chunks_by_id.items():
                        if sender_id != client_to['id']:
                            chunks_for_this_client.append(chunk)

                    # 4. Mix the (N-1) chunks
                    if not chunks_for_this_client:
                        # No one else is talking, send silence
                        mixed_bytes = silent_chunk_bytes
                    else:
                        # --- Audio Mixing Logic ---
                        mixed_f = np.zeros(self.chunk_size, dtype=np.float32)
                        for chunk in chunks_for_this_client:
                            mixed_f += chunk.astype(np.float32)

                        mixed_f /= len(chunks_for_this_client)
                        np.clip(mixed_f, -32768, 32767, out=mixed_f)
                        mixed_data = mixed_f.astype(self.dtype)
                        mixed_bytes = mixed_data.tobytes()
                        # --- End Mixing ---

                    # 5. Send the custom mix
                    try:
                        addr = (client_to['addr'][0], client_to['audio_port'])
                        self.transport.sendto(mixed_bytes, addr)
                    except Exception as e:
                        log.warning(f"Error sending audio mix to {client_to['username']}: {e}")

                # Adjust sleep time to match audio chunk interval (approx)
                # CHUNK / RATE = 4096 / 44100 = ~0.09s
                await asyncio.sleep(0.05) # Mix and send ~20x per second

            except Exception as e:
                log.error(f"Error in audio mixing loop: {e}", exc_info=True)
                await asyncio.sleep(1) # Avoid fast fail loop

class VideoProtocol(ServerProtocol, asyncio.DatagramProtocol):
    """
    Handles Module 1: Video Conferencing (UDP).
    Receives video frames and forwards them to other clients (SFU).
    """
    def __init__(self, server_state):
        super().__init__(server_state)
        self.transport = None
        
    def connection_made(self, transport):
        self.transport = transport
        log.info(f"Video Server (UDP) started on port {UDP_VIDEO_PORT}")
        
    def datagram_received(self, data, addr):
        try:
            # 1. Extract session ID
            session_id = struct.unpack(UDP_HEADER_FORMAT, data[:UDP_HEADER_SIZE])[0]
            
            # 2. Get client info
            client_from = self.server_state.get_client_by_id(session_id)
            if not client_from:
                # log.warning(f"Video data from unknown session ID {session_id}")
                return
                
            # Don't forward if client has video off
            if not client_from.get('is_video_on'):
                return

            # 3. Get the raw video frame (which is just JPEG bytes)
            video_frame = data[UDP_HEADER_SIZE:]
            
            # 4. Create the packet to broadcast
            # Packet: [Sender_Session_ID (8 bytes)][JPEG_Frame_Data (...)]
            packet_to_send = data # We can just re-use the received packet
            
            # 5. Forward to all *other* clients who have video on
            all_clients = self.server_state.get_all_clients()
            for client_to in all_clients:
                if client_to['id'] != client_from['id'] and client_to.get('video_port'):
                    addr_to = (client_to['addr'][0], client_to['video_port'])
                    self.transport.sendto(packet_to_send, addr_to)
                    
        except Exception as e:
            log.error(f"Error in VideoProtocol.datagram_received: {e}", exc_info=True)

class FileTransferProtocol(asyncio.Protocol):
    """
    Handles Module 5: File Sharing (TCP) on port 5001.
    
    This protocol implements a state machine to handle the
    size-prefixed chunking protocol used by the client.
    
    Client Upload Protocol:
    1. Client sends header: [Mode (1 byte 'U')][File_ID (36 bytes)]
    2. Client sends file size: [File Size (8 bytes, !Q)]
    3. Client sends in a loop: [Chunk Size (4 bytes, !I)][Chunk Data (...)]
    4. Server sends ack: [b'1']
    
    Client Download Protocol:
    1. Client sends header: [Mode (1 byte 'D')][File_ID (36 bytes)]
    2. Server sends file size: [File Size (8 bytes, !Q)]
    3. Server sends in a loop: [Chunk Size (4 bytes, !I)][Chunk Data (...)]
    4. Client sends ack: [b'1']
    """
    def _init_(self, server_state):
        self.server_state = server_state
        self.transport = None
        self.client_info = "Unknown"
        self.buffer = b''
        self._running = True

        # --- FIX: Added state machine variables ---
        self.state = "WAIT_HEADER"
        self.file_id = None
        self.file_info = None
        self.file_handle = None
        
        # For UPLOAD
        self.file_size = 0
        self.bytes_received = 0
        self.expected_chunk_size = 0
        
        # For DOWNLOAD
        self.download_task = None
        
        log.debug("FileTransferProtocol initialized")

    def connection_made(self, transport):
        self.transport = transport
        self.client_info = transport.get_extra_info('peername')
        log.info(f"File connection from {self.client_info} on port {TCP_FILE_PORT}")
        # Set a 5-second timeout to receive the header
        self.timeout_handle = asyncio.get_running_loop().call_later(
            5, self.check_timeout
        )

    def check_timeout(self):
        if self.state == "WAIT_HEADER":
            log.warning(f"File connection from {self.client_info} timed out waiting for header.")
            self.transport.close()

    def connection_lost(self, exc):
        if self.state == "UPLOADING" and self.file_handle:
            self.file_handle.close()
            if self.bytes_received < self.file_size:
                log.warning(f"File upload for {self.file_id} was incomplete.")
                # TODO: Delete the partial file
        
        if self.download_task:
            self.download_task.cancel()
            
        self._running = False
        log.debug(f"File connection closed from {self.client_info}")

    def data_received(self, data):
        self.timeout_handle.cancel() # Received data, cancel timeout
        if not self._running:
            return
            
        try:
            self.buffer += data
            # --- FIX: Process buffer as a state machine ---
            while self._running and self.buffer:
                
                if self.state == "WAIT_HEADER":
                    if len(self.buffer) < 37:
                        break # Not enough data for header
                    
                    header = self.buffer[:37]
                    self.buffer = self.buffer[37:]
                    mode = header[:1].decode('utf-8')
                    self.file_id = header[1:].decode('utf-8')
                    
                    self.file_info = self.server_state.get_file_info(self.file_id)
                    if not self.file_info:
                        log.error(f"File ID {self.file_id} not found for {self.client_info}")
                        self.transport.close()
                        return

                    if mode == "U":
                        log.info(f"Receiving file '{self.file_info['filename']}' ({self.file_id}) from {self.client_info}")
                        self.state = "UPLOAD_WAIT_FILESIZE"
                    
                    elif mode == "D":
                        log.info(f"Sending file '{self.file_info['filename']}' ({self.file_id}) to {self.client_info}")
                        self.state = "DOWNLOADING"
                        self.download_task = asyncio.create_task(self.start_download())
                    
                    else:
                        log.error(f"Invalid file transfer mode '{mode}' from {self.client_info}")
                        self.transport.close()
                        return
                
                elif self.state == "UPLOAD_WAIT_FILESIZE":
                    if len(self.buffer) < 8:
                        break # Not enough data
                    
                    self.file_size = struct.unpack('!Q', self.buffer[:8])[0]
                    self.buffer = self.buffer[8:]
                    self.bytes_received = 0
                    
                    if self.file_size != self.file_info['size']:
                        log.error(f"File size mismatch for {self.file_id}. Expected {self.file_info['size']}, client says {self.file_size}")
                        self.transport.close()
                        return
                        
                    self.file_handle = open(self.file_info['filepath'], 'wb')
                    self.state = "UPLOAD_WAIT_CHUNK_SIZE"
                
                elif self.state == "UPLOAD_WAIT_CHUNK_SIZE":
                    if len(self.buffer) < 4:
                        break # Not enough data
                    
                    self.expected_chunk_size = struct.unpack('!I', self.buffer[:4])[0]
                    self.buffer = self.buffer[4:]
                    self.state = "UPLOAD_WAIT_CHUNK_DATA"
                
                elif self.state == "UPLOAD_WAIT_CHUNK_DATA":
                    if len(self.buffer) < self.expected_chunk_size:
                        break # Not enough data
                    
                    chunk_data = self.buffer[:self.expected_chunk_size]
                    self.buffer = self.buffer[self.expected_chunk_size:]
                    
                    self.file_handle.write(chunk_data)
                    self.bytes_received += len(chunk_data)
                    
                    if self.bytes_received >= self.file_size:
                        # --- UPLOAD COMPLETE ---
                        self.file_handle.close()
                        self.file_handle = None
                        self.transport.write(b'1') # Send acknowledgment
                        log.info(f"File upload complete for {self.file_id} from {self.client_info}")
                        # Notify command server to broadcast
                        self.server_state.finalize_file_upload(self.file_id)
                        self.state = "COMPLETE"
                        self.transport.close() # Close connection
                        
                    else:
                        # Wait for next chunk
                        self.state = "UPLOAD_WAIT_CHUNK_SIZE"
                
                elif self.state == "DOWNLOADING":
                    # Waiting for download task to complete
                    # Any data received here is unexpected
                    log.warning(f"Unexpected data from {self.client_info} during download.")
                    self.buffer = b'' # Discard
                    break
                    
                elif self.state == "DOWNLOAD_WAIT_ACK":
                    if len(self.buffer) >= 1:
                        if self.buffer.startswith(b'1'):
                            log.info(f"Client acknowledged download for {self.file_id}")
                        self.transport.close()
                        break
                    
                elif self.state == "COMPLETE":
                    self._running = False
                    break
                    
                else:
                    break # No valid state, wait for more data

        except Exception as e:
            log.error(f"Error in FileTransferProtocol.data_received: {e}", exc_info=True)
            if self.file_handle:
                self.file_handle.close()
            self.transport.close()
            self._running = False

    async def start_download(self):
        """
        --- FIX: This method now sends data in the
        [size][chunk_size][chunk]... protocol.
        """
        filepath = self.file_info['filepath']
        try:
            file_size = os.path.getsize(filepath)
            
            # 1. Send file size (8 bytes)
            self.transport.write(struct.pack('!Q', file_size))
            await self.transport.drain()
            
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(65536) # 64KB chunks
                    if not chunk:
                        break
                    
                    # 2. Send chunk size (4 bytes)
                    self.transport.write(struct.pack('!I', len(chunk)))
                    
                    # 3. Send chunk
                    self.transport.write(chunk)
                    
                    await self.transport.drain() # Wait for buffer to clear
            
            # 4. Wait for client acknowledgment
            self.state = "DOWNLOAD_WAIT_ACK"
            log.info(f"File download complete for {self.file_id} to {self.client_info}")

        except asyncio.CancelledError:
            log.info(f"Download task for {self.file_id} cancelled.")
            self.transport.close()
        except FileNotFoundError:
            log.error(f"Could not find file {filepath} for download.")
            self.transport.close()
        except Exception as e:
            log.error(f"Error sending file {filepath}: {e}", exc_info=True)
            self.transport.close()


    async def handle_authentication(self, payload):
        """Handles Module 4: Authentication"""
        username = payload.get('username')
        password = payload.get('password')
        
        # --- Dummy Authentication (Dev Plan 5.2.2) ---
        # In a real app, check this against a database.
        # For now, we only check for a valid username and that
        # the username isn't already taken.
        is_valid_user = username and password and not self.server_state.is_username_taken(username)
        # --- End Dummy Authentication ---

        if is_valid_user:
            self.username = username
            self.client_id = self.server_state.add_client(self)
            log.info(f"User '{username}' authenticated successfully. Assigned ID {self.client_id}")
            
            payload_out = {
                'message': f"Welcome, {username}!",
                'session_id': self.client_id
            }
            await self.send_message(MessageType.AUTHENTICATION_SUCCESS, payload_out, self.client_id)
            
            # Send initial file list
            await self.server_state.send_file_list(self)
            
            # Broadcast new user list to everyone
            await self.server_state.broadcast_presence()
        
        else:
            log.warning(f"Failed authentication attempt for username: '{username}'")
            payload_out = {'message': "Authentication failed. Invalid credentials or username already in use."}
            await self.send_message(MessageType.AUTHENTICATION_FAILURE, payload_out)
            self.is_running = False # Disconnect

    async def handle_text_message(self, payload):
        """Handles Module 4: Group Text Chat"""
        text = payload.get('text')
        if not text:
            return
            
        log.info(f"Chat from {self.username}: {text}")
        
        broadcast_payload = {
            'username': self.username,
            'text': text
        }
        await self.server_state.broadcast_message(
            MessageType.BROADCAST_TEXT_MESSAGE,
            broadcast_payload,
            exclude_id=self.client_id # Don't send back to sender
        )
        
    async def handle_set_udp_port(self, payload):
        """Handles Modules 1 & 2: Client reports its UDP ports"""
        audio_port = payload.get('audio_port')
        video_port = payload.get('video_port')
        

        if audio_port:
            self.server_state.set_client_udp_port(self.client_id, 'audio_port', audio_port)
            log.info(f"Registered audio (UDP) port {audio_port} for {self.username}")
            
        if video_port:
            self.server_state.set_client_udp_port(self.client_id, 'video_port', video_port)
            log.info(f"Registered video (UDP) port {video_port} for {self.username}")

        # Send a confirmation
        await self.send_message(MessageType.SET_UDP_PORT_SUCCESS, {})
        # --- ADD THIS NEW FUNCTION HERE ---
    async def handle_set_media_state(self, payload):
        """Handles client toggling mic or video."""
        media_type = payload.get('media_type') # 'audio' or 'video'
        state = payload.get('state') # True or False
        
        if media_type and state is not None:
            self.server_state.set_client_media_state(self.client_id, media_type, state)
    # --- END OF NEW FUNCTION ---


    # --- Module 3: Screen Sharing Handlers ---
    async def handle_screen_share_start(self):
        """Client requests to become the presenter."""
        if self.server_state.set_presenter(self.client_id):
            log.info(f"Screen share started for {self.username}.")
            payload = {'username': self.username}
            await self.server_state.broadcast_message(
                MessageType.NOTIFY_SCREEN_SHARE_STARTED,
                payload
            )
        else:
            log.warning(f"{self.username} tried to start screen share, but someone else is already presenting.")
            # TODO: Send an error back? For now, client UI should prevent this.
            
    async def handle_screen_share_stop(self):
        """Client (presenter) stops sharing."""
        if self.server_state.clear_presenter(self.client_id):
            log.info(f"Screen share stopped for {self.username}.")
            await self.server_state.broadcast_message(
                MessageType.NOTIFY_SCREEN_SHARE_STOPPED,
                {}
            )
        else:
            log.warning(f"{self.username} tried to stop screen share, but was not the presenter.")
            
    async def handle_screen_share_data(self, payload):
        """Presenter is sending a frame of data."""
        if self.server_state.is_presenter(self.client_id):
            # This payload contains the base64 JPEG data
            # Just broadcast it to everyone else
            await self.server_state.broadcast_message(
                MessageType.SCREEN_SHARE_DATA_FRAME,
                payload,
                exclude_id=self.client_id
            )
        else:
            log.warning(f"{self.username} sent screen share data but is not the presenter. Ignoring.")
            
    # --- Module 5: File Sharing Handlers ---
    async def handle_file_notify(self, payload):
        """
        Phase 1: Client notifies server it has a file to upload.
        Server reserves a file_id and path.
        """
        filename = payload.get('filename')
        size = payload.get('size')
        if not filename or not size:
            return await self.send_message(MessageType.FILE_TRANSFER_ERROR, {'message': 'Invalid file info.'})
            
        file_id, filepath = self.server_state.register_new_file(filename, size, self.username, self.client_id)
        
        log.info(f"{self.username} registered file '{filename}' ({size} bytes) with ID {file_id}")
        
        # Tell client "OK, you can start uploading this file."
        payload_out = {'file_id': file_id, 'filename': filename}
        await self.send_message(MessageType.FILE_UPLOAD_START_RESPONSE, payload_out)
        
    async def handle_file_upload_start(self, payload):
        """
        Deprecated. Replaced by FILE_TRANSFER_NOTIFY_REQUEST.
        This handler is kept for compatibility but does nothing.
        """
        log.warning("Received deprecated FILE_UPLOAD_START_REQUEST. Ignoring.")

    async def handle_file_download_start(self, payload):
        """
        Phase 3: Client requests to download a file.
        Server just responds "OK, connect to the file port."
        """
        file_id = payload.get('file_id')
        if not file_id or not self.server_state.get_file_info(file_id):
            return await self.send_message(MessageType.FILE_TRANSFER_ERROR, {'message': 'Invalid file ID.'})

        log.info(f"{self.username} requested to download file {file_id}.")
        
        # Tell client "OK, connect to file port 5001 and send this file_id"
        payload_out = {'file_id': file_id, 'file_port': TCP_FILE_PORT}
        await self.send_message(MessageType.FILE_DOWNLOAD_START_RESPONSE, payload_out)


class ServerState:
    """
    Manages the shared state of the server, including all clients,
    presence, and server-side handlers.
    """
    def __init__(self, gui_signal):
        self.clients = {}  # {client_id: ClientHandler}
        self.client_id_counter = 0
        self.gui_signal = gui_signal  # To update user list
        self.current_presenter_id = None
        
        # --- File Sharing State ---
        self.file_registry = {} # {file_id: {filename, size, ...}}
        self.file_id_counter = 0
        
        # --- Audio/Video State ---
        self.client_state = defaultdict(dict) # {client_id: {audio_port, ...}}

        # Create uploads directory
        if not os.path.exists(UPLOADS_DIR):
            os.makedirs(UPLOADS_DIR)
            log.info(f"Created uploads directory: {UPLOADS_DIR}")
        
    def add_client(self, client_handler):
        """Adds a new, authenticated client to the state."""
        self.client_id_counter += 1
        client_id = self.client_id_counter
        self.clients[client_id] = client_handler
        self.client_state[client_id] = {
            'id': client_id,
            'username': client_handler.username,
            'addr': client_handler.addr,
            'audio_port': None,
            'video_port': None,
            'is_unmuted': False,
            'is_video_on': False,
            'audio_chunk': None
        }
        self.update_gui_user_list()
        return client_id

    def remove_client(self, client_id):
        """Removes a client from the state."""
        if client_id in self.clients:
            del self.clients[client_id]
        if client_id in self.client_state:
            del self.client_state[client_id]
        self.update_gui_user_list()
        
    def get_client_by_id(self, client_id):
        """Gets the client state dict by ID."""
        return self.client_state.get(client_id)
        
    def get_all_clients(self):
        """Returns a list of all client state dicts."""
        return list(self.client_state.values())

    def is_username_taken(self, username):
        """Checks if a username is already in use."""
        for client_state in self.client_state.values():
            if client_state['username'].lower() == username.lower():
                return True
        return False

    def update_gui_user_list(self):
        """Sends the current user list to the GUI thread."""
        user_list = [c['username'] for c in self.client_state.values()]
        self.gui_signal.emit(user_list)

    async def broadcast_message(self, msg_type: MessageType, payload: dict, exclude_id: int = None):
        """Sends a message to all connected clients, optionally excluding one."""
        for client_id, client_handler in self.clients.items():
            if client_id != exclude_id:
                await client_handler.send_message(msg_type, payload)
                
    async def broadcast_presence(self):
        """Broadcasts the current user list (with IDs) to all clients."""
        log.info("Broadcasting presence update.")

        # FIX: Send a list of dictionaries, not just strings
        user_list = [
            {'id': client_id, 'username': state['username']}
            for client_id, state in self.client_state.items()
        ]

        payload = {'users': user_list}
        await self.broadcast_message(MessageType.PRESENCE_UPDATE, payload)
        
    async def check_heartbeats(self):
        """
        Periodically checks for client heartbeats and disconnects
        clients that have timed out.
        """
        log.info("Starting heartbeat check loop...")
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            now = asyncio.get_running_loop().time()
            timed_out_clients = []
            
            for client_id, client_handler in self.clients.items():
                if now - client_handler.last_heartbeat > CLIENT_TIMEOUT:
                    log.warning(f"Client {client_handler.username} timed out. Disconnecting.")
                    timed_out_clients.append(client_id)
                    client_handler.is_running = False # Signal handler to stop
                    if client_handler.writer and not client_handler.writer.is_closing():
                        client_handler.writer.close()
            
            # We can't modify the dict while iterating, so no cleanup needed
            # as the handle_client loop will do it.
            if timed_out_clients:
                log.debug("Heartbeat check complete. Timed out clients marked for removal.")

    # --- Screen Share State ---
    def set_presenter(self, client_id):
        """Sets the current presenter. Returns True on success."""
        if self.current_presenter_id is None:
            self.current_presenter_id = client_id
            return True
        return False
        
    def clear_presenter(self, client_id):
        """Clears the presenter, if it's the given client."""
        if self.current_presenter_id == client_id:
            self.current_presenter_id = None
            return True
        return False
        
    def is_presenter(self, client_id):
        """Checks if a client is the current presenter."""
        return self.current_presenter_id == client_id

    # --- File Share State ---
    def register_new_file(self, filename, size, uploader_name, uploader_id):
        """Registers a new file, creates a unique ID, and stores its metadata."""
        self.file_id_counter += 1
        file_id = f"{self.file_id_counter}-{uuid.uuid4().hex[:8]}"
        filepath = os.path.join(UPLOADS_DIR, f"{file_id}_{filename}")
        
        self.file_registry[file_id] = {
            'file_id': file_id,
            'filename': filename,
            'size': size,
            'uploader_name': uploader_name,
            'uploader_id': uploader_id,
            'filepath': filepath,
            'uploaded': False # Mark as not yet uploaded
        }
        return file_id, filepath
        
    def get_file_info(self, file_id):
        """Retrieves metadata for a file."""
        return self.file_registry.get(file_id)

    def finalize_file_upload(self, file_id):
        """
        Marks a file as fully uploaded and broadcasts its
        availability to all clients.
        """
        file_info = self.get_file_info(file_id)
        if not file_info:
            return
            
        file_info['uploaded'] = True
        log.info(f"File {file_info['filename']} is now available for download.")
        
        # Create broadcast payload
        payload = {
            'file_id': file_info['file_id'],
            'filename': file_info['filename'],
            'size': file_info['size'],
            'uploader_name': file_info['uploader_name']
        }
        
        # Run this in a new task so it doesn't block the file server
        asyncio.create_task(self.broadcast_message(
            MessageType.FILE_TRANSFER_NOTIFY_BROADCAST,
            payload
        ))
        
    async def send_file_list(self, client_handler):
        """Sends the list of all *available* files to a single client."""
        log.debug(f"Sending file list to {client_handler.username}")
        for file_info in self.file_registry.values():
            if file_info['uploaded']: # Only send completed files
                payload = {
                    'file_id': file_info['file_id'],
                    'filename': file_info['filename'],
                    'size': file_info['size'],
                    'uploader_name': file_info['uploader_name']
                }
                await client_handler.send_message(
                    MessageType.FILE_TRANSFER_NOTIFY_BROADCAST,
                    payload
                )

    # --- Audio/Video State ---
    def set_client_udp_port(self, client_id, port_type, port):
        """Updates the client's state with their reported UDP port."""
        if client_id in self.client_state:
            self.client_state[client_id][port_type] = port
    def set_client_media_state(self, client_id, media_type, state):
        """Updates the client's mute or video-on status."""
        if client_id in self.client_state:
            if media_type == 'audio':
                self.client_state[client_id]['is_unmuted'] = state
                log.debug(f"{self.client_state[client_id]['username']} is now {'UNMUTED' if state else 'MUTED'}")
            elif media_type == 'video':
                self.client_state[client_id]['is_video_on'] = state
                log.debug(f"{self.client_state[client_id]['username']} video is now {'ON' if state else 'OFF'}")
            
    


class AsyncioServerThread(QThread):
    """
    Runs the entire asyncio event loop in a separate thread,
    leaving the main thread free for the Qt GUI.
    """
    def __init__(self, server_state):
        super().__init__()
        self.server_state = server_state
        self.loop = None

    async def main(self):
        """The main asyncio task that starts all servers."""
        self.loop = asyncio.get_running_loop()
        
        # --- Start TCP Command Server (Module 4, 3, 5) ---
        tcp_command_server = await asyncio.start_server(
            self.handle_new_client,  # This function is called for each new client
            HOST,
            TCP_COMMAND_PORT
        )
        
        # --- Start TCP File Server (Module 5) ---
        tcp_file_server = await asyncio.start_server(
            lambda r, w: FileTransferProtocol(self.server_state, r, w) if 'FileTransferProtocol' in globals() else None,
            HOST,
            TCP_FILE_PORT
        )

        # --- Start UDP Audio Server (Module 2) ---
        audio_transport, _ = await self.loop.create_datagram_endpoint(
            lambda: AudioProtocol(self.server_state),
            local_addr=(HOST, UDP_AUDIO_PORT)
        )
        
        # --- Start UDP Video Server (Module 1) ---
        video_transport, _ = await self.loop.create_datagram_endpoint(
            lambda: VideoProtocol(self.server_state),
            local_addr=(HOST, UDP_VIDEO_PORT)
        )

        # --- Start Background Tasks ---
        heartbeat_task = asyncio.create_task(self.server_state.check_heartbeats())
        audio_mix_task = asyncio.create_task(
            audio_transport.get_protocol().broadcast_mixed_audio()
        )
        
        log.info(f"TCP Command Server started on port {TCP_COMMAND_PORT}")
        log.info(f"TCP File Server started on port {TCP_FILE_PORT}")
        
        # Keep servers running
        try:
            await asyncio.gather(
                tcp_command_server.serve_forever(),
                tcp_file_server.serve_forever(),
                heartbeat_task,
                audio_mix_task
            )
        except asyncio.CancelledError:
            log.info("Server tasks cancelled.")
        finally:
            log.info("Shutting down servers...")
            tcp_command_server.close()
            await tcp_command_server.wait_closed()
            tcp_file_server.close()
            await tcp_file_server.wait_closed()
            audio_transport.close()
            video_transport.close()
            log.info("All servers shut down.")

    async def handle_new_client(self, reader, writer):
        """
        Callback for when a new client connects to the
        TCP Command Server.
        """
        # Create a handler instance for this client
        client_handler = ClientHandler(reader, writer, self.server_state)
        # Create a new task to handle this client's messages
        asyncio.create_task(client_handler.handle_client())

    def run(self):
        """Main entry point for the QThread."""
        log.info("Asyncio server thread started.")
        try:
            asyncio.run(self.main())
        except Exception as e:
            log.critical(f"Critical error in asyncio main loop: {e}", exc_info=True)
            
    def stop(self):
        """Stops the asyncio event loop from the main thread."""
        log.info("Stopping asyncio server thread...")
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.wait() # Wait for thread to finish
        log.info("Server thread stopped.")


def main():
    """Main entry point for the application."""
    # Set thread name for main GUI thread
    threading.current_thread().name = "MainGUIThread"
    
    app = QApplication(sys.argv)
    
    # Create the ServerWindow
    window = ServerWindow(app)
    
    # Create the server state manager
    # Pass it the signal to update the GUI
    server_state = ServerState(window.update_user_list_signal)
    
    # Create and start the asyncio server thread
    server_thread = AsyncioServerThread(server_state)
    server_thread.start()
    
    # Show the window and run the Qt app loop
    window.show()
    
    # Connect app exit to stopping the server thread
    app.aboutToQuit.connect(server_thread.stop)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
