import socket
import asyncio as a
import binascii as b
import random as r
from collections import namedtuple
import re
import struct
import ssl

# Opcodes
OP_CONT = const(0x0)
OP_TEXT = const(0x1)
OP_BYTES = const(0x2)
OP_CLOSE = const(0x8)
OP_PING = const(0x9)
OP_PONG = const(0xa)

# Close codes
CLOSE_OK = const(1000)
CLOSE_GOING_AWAY = const(1001)
CLOSE_PROTOCOL_ERROR = const(1002)
CLOSE_DATA_NOT_SUPPORTED = const(1003)
CLOSE_BAD_DATA = const(1007)
CLOSE_POLICY_VIOLATION = const(1008)
CLOSE_TOO_BIG = const(1009)
CLOSE_MISSING_EXTN = const(1010)
CLOSE_BAD_CONDITION = const(1011)

URL_RE = re.compile(r'(wss|ws)://([A-Za-z0-9-\.]+)(?:\:([0-9]+))?(/[^\s]*)?')
URI = namedtuple('URI', ('protocol', 'hostname', 'port', 'path'))

class AsyncWebsocketClient:
    def __init__(self, ms_delay_for_read: int = 5):
        self._open = False
        self.delay_read = ms_delay_for_read
        self._lock_for_open = a.Lock()
        self.sock = None

    async def open(self, new_val: bool | None = None):
        await self._lock_for_open.acquire()
        if new_val is not None:
            if not new_val and self.sock:
                self.sock.close()
                self.sock = None
            self._open = new_val
        to_return = self._open
        self._lock_for_open.release()
        return to_return

    async def close(self, code=None):
        if code is not None:
            print("Connection is closed. Code: ", code)
        return await self.open(False)

    def urlparse(self, uri):
        """Parse ws or wss:// URLs"""
        match = URL_RE.match(uri)
        if match:
            protocol, host, port, path = match.group(1), match.group(2), match.group(3), match.group(4)

            if protocol not in ['ws', 'wss']:
                raise ValueError('Scheme {} is invalid'.format(protocol))

            if port is None:
                port = (80, 443)[protocol == 'wss']

            return URI(protocol, host, int(port), path)

    async def a_readline(self):
        line = None
        while line is None:
            line = self.sock.readline()
            await a.sleep_ms(self.delay_read)

        return line

    async def a_read(self, size: int | None = None):
        if size == 0:
            return b''
        chunks = []
        bytes_read = 0
        
        # On ESP32, socket buffer is limited
        if size is None:
            # For header reads (size=None), read first available chunk
            # Don't timeout quickly - server might be sending soon
            for attempt in range(40):  # ~200ms total
                try:
                    b = self.sock.read(128)
                    if b is not None and len(b) > 0:
                        return b
                    await a.sleep_ms(self.delay_read)
                except OSError:
                    await a.sleep_ms(self.delay_read)
            return b''
        
        # For sized reads: MUST get exactly 'size' bytes OR error
        # Never return partial data - that breaks struct.unpack()
        max_chunk = 256
        max_total_retries = 200  # ~1000ms total timeout for full data
        retry_count = 0
        
        while bytes_read < size:
            try:
                remaining = size - bytes_read
                read_size = min(remaining, max_chunk)
                
                b = self.sock.read(read_size)
                
                # No data available right now
                if b is None:
                    retry_count += 1
                    if retry_count > max_total_retries:
                        # Timeout - ALWAYS raise error, never return incomplete data
                        raise OSError(f"Timeout: got {bytes_read}/{size} bytes, need all {size}")
                    await a.sleep_ms(self.delay_read)
                    continue
                
                # Reset retry counter on data
                retry_count = 0
                
                # Got some data
                if len(b) > 0:
                    chunks.append(b)
                    bytes_read += len(b)
                else:
                    # Empty read = socket closed or connection issue
                    raise OSError(f"Socket closed: got {bytes_read}/{size} bytes")
                
            except OSError as e:
                # Socket error - always propagate
                raise
        
        # After while loop exits normally, we have all 'size' bytes
        data = b''.join(chunks)
        return data

    async def handshake(self, uri, headers=[], keyfile=None, certfile=None, cafile=None, cert_reqs=0):
        if self.sock:
            await self.close()

        self.sock = socket.socket()
        self.uri = self.urlparse(uri)
        ai = socket.getaddrinfo(self.uri.hostname, self.uri.port) # type: ignore
        addr = ai[0][4]

        self.sock.connect(addr)
        self.sock.setblocking(False)

        if self.uri.protocol == 'wss': # type: ignore
            cadata = None
            if not cafile is None:
                with open(cafile, 'rb') as f:
                    cadata = f.read()
            self.sock = ssl.wrap_socket(
                self.sock, server_side=False,
                key=keyfile, cert=certfile, # type: ignore
                cert_reqs=cert_reqs, # 0 - NONE, 1 - OPTIONAL, 2 - REQUIED
                cadata=cadata, # type: ignore
                server_hostname=self.uri.hostname # type: ignore
            )

        def send_header(header, *args):
            self.sock.write(header % args + '\r\n') # type: ignore

        # Sec-WebSocket-Key is 16 bytes of random base64 encoded
        key = b.b2a_base64(bytes(r.getrandbits(8)
                                        for _ in range(16)))[:-1]

        send_header(b'GET %s HTTP/1.1', self.uri.path or '/') # type: ignore
        send_header(b'Host: %s:%s', self.uri.hostname, self.uri.port) # type: ignore
        send_header(b'Connection: Upgrade')
        send_header(b'Upgrade: websocket')
        send_header(b'Sec-WebSocket-Key: %s', key)
        send_header(b'Sec-WebSocket-Version: 13')
        send_header(b'Origin: http://{hostname}:{port}'.format( # type: ignore
            hostname=self.uri.hostname, # type: ignore
            port=self.uri.port) # type: ignore
        )

        for key, value in headers:
            send_header(b'%s: %s', key, value)

        send_header(b'')

        line = await self.a_readline()
        header = (line)[:-2]
        if not header.startswith(b'HTTP/1.1 101 '):
            raise Exception(header)

        # We don't (currently) need these headers
        # FIXME: should we check the return key?
        while header:
            line = await self.a_readline()
            header = (line)[:-2]

        return await self.open(True)

    async def read_frame(self, max_size=None):
        # Frame header
        byte1, byte2 = struct.unpack('!BB', await self.a_read(2))

        # Byte 1: FIN(1) _(1) _(1) _(1) OPCODE(4)
        fin = bool(byte1 & 0x80)
        opcode = byte1 & 0x0f

        # Byte 2: MASK(1) LENGTH(7)
        mask = bool(byte2 & (1 << 7))
        length = byte2 & 0x7f

        if length == 126:  # Magic number, length header is 2 bytes
            length, = struct.unpack('!H', await self.a_read(2))
        elif length == 127:  # Magic number, length header is 8 bytes
            length, = struct.unpack('!Q', await self.a_read(8))

        if mask:  # Mask is 4 bytes
            mask_bits = await self.a_read(4)

        try:
            data = await self.a_read(length)
        except MemoryError:
            # We can't receive this many bytes, close the socket
            await self.close(code=CLOSE_TOO_BIG)
            # await self._stream.drain()
            return True, OP_CLOSE, None

        if mask:
            data = bytes(b ^ mask_bits[i % 4]
                         for i, b in enumerate(data))

        return fin, opcode, data

    def write_frame(self, opcode, data=b''):
        fin = True
        mask = True  # messages sent by client are masked

        length = len(data)

        # Frame header
        # Byte 1: FIN(1) _(1) _(1) _(1) OPCODE(4)
        byte1 = 0x80 if fin else 0
        byte1 |= opcode

        # Byte 2: MASK(1) LENGTH(7)
        byte2 = 0x80 if mask else 0

        # Build header
        if length < 126:  # 126 is magic value to use 2-byte length header
            byte2 |= length
            header = struct.pack('!BB', byte1, byte2)

        elif length < (1 << 16):  # Length fits in 2-bytes
            byte2 |= 126  # Magic code
            header = struct.pack('!BBH', byte1, byte2, length)

        elif length < (1 << 64):
            byte2 |= 127  # Magic code
            header = struct.pack('!BBQ', byte1, byte2, length)
        else:
            raise ValueError()

        # Send header (with retry on partial writes)
        try:
            self._write_all(header)
        except OSError:
            # Header failed to send
            raise

        if mask:  # Mask is 4 bytes
            mask_bits = struct.pack('!I', r.getrandbits(32))
            try:
                self._write_all(mask_bits)
            except OSError:
                # Mask failed to send
                raise
            data = bytes(b ^ mask_bits[i % 4]
                         for i, b in enumerate(data))

        # Send data (with retry on partial writes)
        try:
            self._write_all(data)
        except OSError:
            # Data failed to send
            raise
    
    def _write_all(self, data):
        """Write all data to socket. For small frames like ACK messages."""
        if not data:
            return  # Nothing to write
        
        if self.sock is None:
            raise OSError("Socket is None")
        
        # Ensure data is bytes
        if isinstance(data, str):
            data = data.encode('utf-8')
        
        # ESP32: For small frames (< 256 bytes), try to write with retries
        # Track individual write results for diagnostics
        offset = 0
        total_attempts = 0
        zero_writes = 0
        os_errors = 0
        
        for attempt in range(15):  # 15 attempts
            try:
                remaining = data[offset:]
                if not remaining:
                    return  # All data written successfully
                
                written = self.sock.write(remaining)
                total_attempts += 1
                
                if written is None:
                    written = 0
                    zero_writes += 1
                elif written > 0:
                    offset += written
                else:
                    zero_writes += 1
                    
            except OSError as e:
                os_errors += 1
                # OSError on write - might be EAGAIN or buffer full
                total_attempts += 1
                # Continue to retry
            
            if offset >= len(data):
                return  # Success
            
            # Backoff before retrying to let the socket buffer clear
            import time
            time.sleep_ms(10)
        
        # Failed after all retries - provide diagnostics
        error_msg = f"Failed to send {len(data) - offset}/{len(data)} bytes (attempts={total_attempts}, zero_writes={zero_writes}, os_errors={os_errors})"
        raise OSError(error_msg)

    async def recv(self):
        frame_error_count = 0
        max_retries = 10  # Increase retries for transient issues
        
        while await self.open():
            try:
                fin, opcode, data = await self.read_frame()
                frame_error_count = 0  # Reset on success
                
            except (OSError, ValueError) as ex:
                # Frame reading errors: these might be transient on ESP32
                frame_error_count += 1
                error_msg = str(ex)
                
                if "buffer too small" in error_msg or "timeout" in error_msg.lower():
                    # Transient socket/timing issue
                    if frame_error_count <= max_retries:
                        await a.sleep_ms(100)  # Longer wait on transient error
                        continue
                
                # Too many errors or unrecognized error
                print(f'Frame read error (giving up after {frame_error_count} retries): {ex}')
                await self.open(False)
                return
                
            except Exception as ex:
                print(f'Exception in recv while reading frame: {ex}')
                await self.open(False)
                return

            if not fin:
                raise NotImplementedError()

            if opcode == OP_TEXT:
                return data.decode('utf-8')
            elif opcode == OP_BYTES:
                return data
            elif opcode == OP_CLOSE:
                await self.open(False)
                return
            elif opcode == OP_PONG:
                # Ignore this frame, keep waiting for a data frame
                continue
            elif opcode == OP_PING:
                try:
                    # We need to send a pong frame
                    self.write_frame(OP_PONG, data)

                    # And then continue to wait for a data frame
                    continue
                except Exception as ex:
                    print('Error sending pong frame:', ex)
                    # If sending the pong frame fails, close the connection
                    await self.open(False)
                    return
            elif opcode == OP_CONT:
                # This is a continuation of a previous frame
                raise NotImplementedError(opcode)
            else:
                raise ValueError(opcode)

    async def send(self, buf):
        if not await self.open():
            return
        if isinstance(buf, str):
            opcode = OP_TEXT
            buf = buf.encode('utf-8')
        elif isinstance(buf, bytes):
            opcode = OP_BYTES
        else:
            raise TypeError()
        
        # On ESP32, yield to event loop before writing
        await a.sleep_ms(0)
        
        # Call write_frame which might raise OSError if socket write fails
        try:
            self.write_frame(opcode, buf)
        except OSError as e:
            # Socket write failed - propagate to caller
            raise
