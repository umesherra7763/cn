#!/usr/bin/env python3
"""
Simple headless client to test the binary protocol used by the GUI client.
Run this when `server.py` is listening on 127.0.0.1:5000.
No PySide6 required.
"""
import asyncio
import struct
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

HOST = '127.0.0.1'
PORT = 5000
MAGIC_NUMBER = 0xDEADBEEF
PROTOCOL_VERSION = 0x0100
HEADER_FORMAT = '!IHHQI'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

class MessageType:
    AUTHENTICATION_REQUEST = 1
    AUTHENTICATION_SUCCESS = 2
    AUTHENTICATION_FAILURE = 3
    HEARTBEAT = 10
    SEND_TEXT_MESSAGE = 100
    BROADCAST_TEXT_MESSAGE = 101
    PRESENCE_UPDATE = 200

async def run_test(username: str, password: str):
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
        log.info(f"Connected to {HOST}:{PORT}")

        payload = {'username': username, 'password': password}
        payload_bytes = json.dumps(payload).encode('utf-8')
        payload_len = len(payload_bytes)

        header = struct.pack(HEADER_FORMAT, MAGIC_NUMBER, PROTOCOL_VERSION, MessageType.AUTHENTICATION_REQUEST, 0, payload_len)
        writer.write(header)
        writer.write(payload_bytes)
        await writer.drain()
        log.info("Sent authentication request.")

        # Wait for a response header
        header_bytes = await reader.readexactly(HEADER_SIZE)
        magic, ver, msg_type_val, session_id, payload_len = struct.unpack(HEADER_FORMAT, header_bytes)
        if magic != MAGIC_NUMBER:
            log.error("Bad magic from server. Closing.")
            writer.close()
            await writer.wait_closed()
            return

        payload_data = {}
        if payload_len > 0:
            payload_bytes = await reader.readexactly(payload_len)
            payload_data = json.loads(payload_bytes.decode('utf-8'))

        log.info(f"Received msg_type={msg_type_val} session_id={session_id} payload={payload_data}")

        writer.close()
        await writer.wait_closed()

    except asyncio.IncompleteReadError:
        log.error("Server closed connection unexpectedly.")
    except ConnectionRefusedError:
        log.error("Connection refused (is server.py running?).")
    except Exception as e:
        log.exception("Error in test client: %s", e)

if __name__ == '__main__':
    if len(sys.argv) >= 3:
        user = sys.argv[1]
        pw = sys.argv[2]
    else:
        user = 'testuser'
        pw = 'pass123'

    asyncio.run(run_test(user, pw))
