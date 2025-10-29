"""
File Transfer Server Module

Handles TCP file transfers on port 5001.
"""

import asyncio
import socket
import struct
import logging
import os

from enum import IntEnum

log = logging.getLogger(__name__)

class FileTransferServer:
    """
    Handles file uploads and downloads over a dedicated TCP port.
    """
    def __init__(self, host, port, uploads_dir, server_state):
        self.host = host
        self.port = port
        self.uploads_dir = uploads_dir
        self.server_state = server_state
        self.server = None

    async def start(self):
        """Starts the file transfer server.

        Tries a small set of candidate addresses if the configured host
        cannot be bound (e.g. when HOST is a LAN IP not present on this
        machine). Returns the asyncio.Server on success or raises the
        last exception on failure.
        """
        candidates = [self.host, '0.0.0.0', '127.0.0.1']
        last_exc = None
        for candidate in candidates:
            try:
                self.server = await asyncio.start_server(
                    self.handle_file_client,
                    candidate,
                    self.port
                )
                addr = self.server.sockets[0].getsockname()
                log.info(f'File transfer server running on {addr}')
                return self.server
            except OSError as e:
                last_exc = e
                log.warning(f"Could not bind file server on {candidate}:{self.port} -> {e}")

        # If we get here, all candidates failed
        log.error(f"Failed to start file server on any candidate address: {last_exc}")
        raise last_exc

    async def handle_file_client(self, reader, writer):
        """
        Handles an individual file transfer client.
        The first 37 bytes contain: [Mode (1 byte)][File_ID (36 bytes)]
        Mode is 'U' for upload or 'D' for download.
        """
        try:
            # Get the header
            header = await reader.read(37)
            if len(header) != 37:
                log.error("Invalid file transfer header received")
                return

            mode = header[0:1].decode('utf-8')
            file_id = header[1:].decode('utf-8')
            
            peer = writer.get_extra_info('peername')
            log.info(f"File transfer connection from {peer}: {mode} {file_id}")
            
            if mode == 'U':
                await self.handle_upload(reader, writer, file_id)
            elif mode == 'D':
                await self.handle_download(reader, writer, file_id)
            else:
                log.error(f"Invalid transfer mode: {mode}")
                
        except Exception as e:
            log.error(f"Error handling file transfer: {e}", exc_info=True)
        finally:
            writer.close()
            await writer.wait_closed()

    async def handle_upload(self, reader, writer, file_id):
        """
        Handles a file upload from a client.
        Protocol:
        1. Receive file size (8 bytes)
        2. For each chunk:
           - Receive chunk size (4 bytes)
           - Receive chunk data (chunk_size bytes)
        3. Send acknowledgment (1 byte)
        """
        try:
            file_info = self.server_state.get_file_info(file_id)
            if not file_info:
                log.error(f"Unknown file_id for upload: {file_id}")
                return

            filepath = os.path.join(self.uploads_dir, file_info['filename'])

            # Get total file size
            size_data = await reader.read(8)
            if len(size_data) != 8:
                log.error("Failed to receive file size")
                return
            file_size = struct.unpack('!Q', size_data)[0]

            if file_size != file_info['size']:
                log.error(f"File size mismatch. Expected {file_info['size']}, got {file_size}")
                return

            bytes_received = 0
            with open(filepath, 'wb') as f:
                while bytes_received < file_size:
                    # Get chunk size
                    size_data = await reader.read(4)
                    if len(size_data) != 4:
                        raise Exception("Failed to receive chunk size")
                    chunk_size = struct.unpack('!I', size_data)[0]

                    # Get chunk data
                    chunk = await reader.read(chunk_size)
                    if len(chunk) != chunk_size:
                        raise Exception(f"Incomplete chunk: got {len(chunk)}/{chunk_size} bytes")

                    f.write(chunk)
                    bytes_received += len(chunk)

            # Send acknowledgment
            writer.write(b'1')
            await writer.drain()

            log.info(f"Upload complete: {file_id} ({bytes_received} bytes)")

        except Exception as e:
            log.error(f"Upload error for {file_id}: {e}", exc_info=True)
            try:
                writer.write(b'0')
                await writer.drain()
            except:
                pass

    async def handle_download(self, reader, writer, file_id):
        """
        Handles a file download request from a client.
        Protocol:
        1. Send file size (8 bytes)
        2. For each chunk:
           - Send chunk size (4 bytes)
           - Send chunk data (chunk_size bytes)
        3. Receive acknowledgment (1 byte)
        """
        try:
            file_info = self.server_state.get_file_info(file_id)
            if not file_info:
                log.error(f"Unknown file_id for download: {file_id}")
                return

            filepath = os.path.join(self.uploads_dir, file_info['filename'])
            if not os.path.exists(filepath):
                log.error(f"File not found: {filepath}")
                return

            file_size = os.path.getsize(filepath)
            writer.write(struct.pack('!Q', file_size))
            await writer.drain()

            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(65536)  # 64KB chunks
                    if not chunk:
                        break

                    # Send chunk size then chunk
                    writer.write(struct.pack('!I', len(chunk)))
                    writer.write(chunk)
                    await writer.drain()

            # Wait for acknowledgment
            ack = await reader.read(1)
            if ack != b'1':
                log.warning(f"Download not acknowledged for {file_id}")

            log.info(f"Download complete: {file_id} ({file_size} bytes)")

        except Exception as e:
            log.error(f"Download error for {file_id}: {e}", exc_info=True)