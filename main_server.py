"""
Main entry point for the server application.
"""

import sys
import asyncio
import logging
from PySide6.QtWidgets import QApplication

from server import (
    ServerWindow, ServerState, ClientHandler,
    AudioProtocol, VideoProtocol, log,
    HOST, TCP_COMMAND_PORT, TCP_FILE_PORT,
    UDP_AUDIO_PORT, UDP_VIDEO_PORT, UPLOADS_DIR
)
from file_server import FileTransferServer

async def main():
    # Create Qt application
    app = QApplication(sys.argv)
    
    # Create main window
    window = ServerWindow(app)
    window.show()
    
    # Create shared server state
    server_state = ServerState(window.update_user_list_signal)
    
    # Create file transfer server
    file_server = FileTransferServer(HOST, TCP_FILE_PORT, UPLOADS_DIR, server_state)
    await file_server.start()

    # Start command server
    command_server = await asyncio.start_server(
        lambda r, w: ClientHandler(r, w, server_state),
        HOST, TCP_COMMAND_PORT
    )

    # Start UDP media servers
    loop = asyncio.get_event_loop()
    
    # Audio server (UDP)
    transport, audio_protocol = await loop.create_datagram_endpoint(
        lambda: AudioProtocol(server_state),
        local_addr=(HOST, UDP_AUDIO_PORT)
    )
    
    # Video server (UDP)
    transport, video_protocol = await loop.create_datagram_endpoint(
        lambda: VideoProtocol(server_state),
        local_addr=(HOST, UDP_VIDEO_PORT)
    )
    
    log.info(f"Server started on {HOST}")
    log.info(f"TCP Command Port: {TCP_COMMAND_PORT}")
    log.info(f"TCP File Port: {TCP_FILE_PORT}")
    log.info(f"UDP Audio Port: {UDP_AUDIO_PORT}")
    log.info(f"UDP Video Port: {UDP_VIDEO_PORT}")

    try:
        # Run Qt event loop
        while True:
            app.processEvents()
            await asyncio.sleep(0.01)
    except KeyboardInterrupt:
        log.info("Server shutting down...")
    finally:
        command_server.close()
        await command_server.wait_closed()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass