import asyncio
import binascii
import random
import struct
import re

try:
    import ssl
except ImportError:
    ssl = None

# Opcodes
OP_CONT = const(0x0)
OP_TEXT = const(0x1)
OP_BYTES = const(0x2)
OP_CLOSE = const(0x8)
OP_PING = const(0x9)
OP_PONG = const(0xa)

URL_RE = re.compile(r'(wss|ws)://([A-Za-z0-9-\.]+)(?:\:([0-9]+))?(/[^\s]*)?')

class AsyncWebsocketClient:
    def __init__(self, ms_delay_for_read=5):
        # We keep the parameter signature the same to not break app.py, 
        # though we won't need manual loop delays anymore
        self.reader = None
        self.writer = None
        self._open = False
        self.uri = None
        self._lock = asyncio.Lock()

    async def open(self, new_val=None):
        await self._lock.acquire()
        if new_val is not None:
            if not new_val and self._open:
                await self._close_streams()
            self._open = new_val
        to_return = self._open
        self._lock.release()
        return to_return

    async def _close_streams(self):
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except:
                pass
            self.writer = None
        self.reader = None

    async def close(self, code=None):
        if code is not None:
            print("Connection is closed. Code: ", code)
        return await self.open(False)

    def urlparse(self, uri):
        match = URL_RE.match(uri)
        if not match:
            raise ValueError("Invalid URL")
        protocol, host, port, path = match.group(1), match.group(2), match.group(3), match.group(4)
        if protocol not in ['ws', 'wss']:
            raise ValueError('Scheme is invalid')
        port = int(port) if port else (443 if protocol == 'wss' else 80)
        path = path or '/'
        
        class URIInfo:
            def __init__(self, p, h, po, pa):
                self.protocol, self.hostname, self.port, self.path = p, h, po, pa
        return URIInfo(protocol, host, port, path)

    async def handshake(self, uri, headers=[]):
        phase = "start"
        try:
            phase = "urlparse"
            self.uri = self.urlparse(uri)
            
            # Close existing connections cleanly
            phase = "close_streams"
            await self._close_streams()
            
            ssl_ctx = None
            if self.uri.protocol == 'wss':
                ssl_ctx = True 
                
            # Use MicroPython's native uasyncio Streams! Clean abstraction over non-blocking C-sockets.
            phase = "open_connection"
            self.reader, self.writer = await asyncio.open_connection(
                self.uri.hostname, 
                self.uri.port, 
                ssl=ssl_ctx
            )

            # Generate WS Key
            key = binascii.b2a_base64(bytes(random.getrandbits(8) for _ in range(16)))[:-1]
            
            # Build and send HTTP handshake (fully buffered)
            req = bytearray()
            req.extend(f"GET {self.uri.path} HTTP/1.1\r\n".encode())
            req.extend(f"Host: {self.uri.hostname}:{self.uri.port}\r\n".encode())
            req.extend(b"Connection: Upgrade\r\n")
            req.extend(b"Upgrade: websocket\r\n")
            req.extend(b"Sec-WebSocket-Key: " + key + b"\r\n")
            req.extend(b"Sec-WebSocket-Version: 13\r\n")
            req.extend(f"Origin: http://{self.uri.hostname}:{self.uri.port}\r\n".encode())
            for k, v in headers:
                req.extend(f"{k}: {v}\r\n".encode())
            req.extend(b"\r\n")
            
            phase = "write_handshake"
            self.writer.write(req)
            await self.writer.drain()
            
            # Wait for Server HTTP Upgrade Response
            phase = "read_status_line"
            line = await self.reader.readline()
            if not line.startswith(b'HTTP/1.1 101 '):
                raise Exception(f"Handshake failed: {line.decode().strip()}")
                
            phase = "read_headers"
            while True:
                line = await self.reader.readline()
                if not line or line == b'\r\n':
                    break
                    
            return await self.open(True)
            
        except BaseException as e:
            # Immediately clean up memory on timeout/drop
            await self._close_streams()
            raise Exception("WebSocket handshake error at {}: {!r}".format(phase, e))

    async def read_frame(self):
        if not self.reader:
            raise OSError("Stream is closed")
            
        header = await self.reader.readexactly(2)
        byte1, byte2 = struct.unpack('!BB', header)
        
        fin = bool(byte1 & 0x80)
        opcode = byte1 & 0x0f
        mask = bool(byte2 & 0x80)
        length = byte2 & 0x7f
        
        if length == 126:
            length, = struct.unpack('!H', await self.reader.readexactly(2))
        elif length == 127:
            length, = struct.unpack('!Q', await self.reader.readexactly(8))
            
        mask_bits = None
        if mask:
            mask_bits = await self.reader.readexactly(4)
            
        # Handle massive metadata payloads safely
        try:
            data = await self.reader.readexactly(length)
        except MemoryError:
            print(f"MemoryError: Payload size {length} too large. Draining stream bypass.")
            remaining = length
            while remaining > 0:
                chunk = min(remaining, 1024)
                await self.reader.readexactly(chunk)
                remaining -= chunk
            
            # Return OP_PONG to safely drop the frame without ending connection
            return True, OP_PONG, b''

        if mask and data:
            unmasked = bytearray(length)
            for i in range(length):
                unmasked[i] = data[i] ^ mask_bits[i % 4]
            data = bytes(unmasked)
            
        return fin, opcode, data

    async def write_frame(self, opcode, data=b''):
        if not self.writer:
            raise OSError("Stream is closed")
            
        fin = True
        mask = True
        length = len(data)
        
        header = bytearray()
        byte1 = (0x80 if fin else 0) | opcode
        header.append(byte1)
        
        byte2 = 0x80 if mask else 0
        if length < 126:
            header.append(byte2 | length)
        elif length < 65536:
            header.append(byte2 | 126)
            header.extend(struct.pack('!H', length))
        else:
            header.append(byte2 | 127)
            header.extend(struct.pack('!Q', length))
            
        mask_bits = struct.pack('!I', random.getrandbits(32))
        header.extend(mask_bits)
        
        # Apply mask directly into the layout bytearray
        masked_data = bytearray(length)
        for i in range(length):
            masked_data[i] = data[i] ^ mask_bits[i % 4]
            
        self.writer.write(header)
        self.writer.write(masked_data)
        await self.writer.drain()

    async def recv(self):
        while await self.open():
            try:
                fin, opcode, data = await self.read_frame()
            except OSError:
                await self.close()
                return None
                
            if opcode == OP_CLOSE:
                await self.close()
                return None
            elif opcode == OP_PING:
                await self.write_frame(OP_PONG, data)
            elif opcode == OP_PONG:
                continue
            elif opcode == OP_TEXT:
                return data.decode('utf-8')
            elif opcode == OP_BYTES:
                return data
                
        return None

    async def send(self, buf):
        if not await self.open():
            return
        try:
            if isinstance(buf, str):
                await self.write_frame(OP_TEXT, buf.encode('utf-8'))
            else:
                await self.write_frame(OP_BYTES, buf)
        except BaseException as e:
            await self.close()
            raise e
