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
    try:
        await file_server.start()
    except Exception:
        log.error("File server failed to start on configured addresses. Exiting.")
        raise

    # Helper: try to bind server to multiple candidate addresses
    async def try_start_command_server():
        candidates = [HOST, '0.0.0.0', '127.0.0.1']
        last_exc = None
        for candidate in candidates:
            try:
                server = await asyncio.start_server(
                    lambda r, w: ClientHandler(r, w, server_state),
                    candidate, TCP_COMMAND_PORT
                )
                addr = server.sockets[0].getsockname()
                log.info(f'Command server running on {addr}')
                return server
            except OSError as e:
                last_exc = e
                log.warning(f"Could not bind command server on {candidate}:{TCP_COMMAND_PORT} -> {e}")
        log.error(f"Failed to bind command server on any candidate address: {last_exc}")
        raise last_exc

    # Start command server (with fallback addresses)
    command_server = await try_start_command_server()

    # Start UDP media servers
    loop = asyncio.get_event_loop()

    # Helper to create UDP endpoint with fallback addresses
    async def try_create_datagram(candidate_list, port, protocol_factory):
        last_exc = None
        for candidate in candidate_list:
            try:
                transport, protocol = await loop.create_datagram_endpoint(
                    protocol_factory,
                    local_addr=(candidate, port)
                )
                log.info(f"UDP endpoint bound on {(candidate, port)}")
                return transport, protocol
            except OSError as e:
                last_exc = e
                log.warning(f"Could not bind UDP on {candidate}:{port} -> {e}")
        log.error(f"Failed to bind UDP endpoint on any candidate for port {port}: {last_exc}")
        raise last_exc

    candidates = [HOST, '0.0.0.0', '127.0.0.1']
    # Audio server (UDP)
    transport_audio, audio_protocol = await try_create_datagram(candidates, UDP_AUDIO_PORT, lambda: AudioProtocol(server_state))

    # Video server (UDP)
    transport_video, video_protocol = await try_create_datagram(candidates, UDP_VIDEO_PORT, lambda: VideoProtocol(server_state))
    
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