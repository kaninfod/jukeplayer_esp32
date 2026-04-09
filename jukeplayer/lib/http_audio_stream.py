import uasyncio as asyncio
import time
import gc
import json

class MockVS1053:
    """Mock for Peter Hinch's micropython-vs1053 hardware decoder.
    
    Reads from a socket-like or StreamReader-like stream at roughly 128kbps,
    discarding bytes and printing periodic status to prove the transport
    and backpressure mechanics function correctly.
    """
    def __init__(self):
        self.playing = False
        self.paused = False
        self.task = None
    
    async def play(self, stream, length=0):
        """Simulate hardware decode loop."""
        self.playing = True
        self.paused = False
        print("LOG: [VS1053] Starting hardware decoding...")
        bytes_received = 0
        start_time = time.time()
        last_log = time.time()
        chunk_size = 4096
        
        try:
            while self.playing:
                if self.paused:
                    await asyncio.sleep_ms(100)
                    continue
                    
                # Read 4KB at a time. If it's a StreamReader, it's awaitable.
                if hasattr(stream, 'readexactly'):
                    # asyncio.StreamReader
                    try:
                        chunk = await stream.read(chunk_size)
                    except Exception as e:
                        print(f"LOG: [VS1053] EOF or error reading from stream: {e}")
                        break
                else:
                    # socket or file
                    chunk = stream.read(chunk_size)
                
                if not chunk:
                    print("LOG: [VS1053] End of stream reached (0 bytes returned)")
                    break
                
                bytes_received += len(chunk)
                
                # Log periodically to prove streaming works
                if time.time() - last_log > 5:
                    last_log = time.time()
                    elapsed = last_log - start_time
                    kbps = (bytes_received / 1024) / elapsed if elapsed > 0 else 0
                    print(f"LOG: [VS1053] Streaming... {bytes_received//1024}KB decoded | {kbps:.1f} KB/s")
                    
                # Simulated hardware decoding time.
                # 128kbps = 16KB/s = 4096 bytes per 250ms
                await asyncio.sleep_ms(250)
                
                gc.collect()
                
        except asyncio.CancelledError:
            print("LOG: [VS1053] Playback cancelled externally")
        except Exception as e:
            print(f"LOG: [VS1053] Hardware interrupted: {e}")
        finally:
            self.playing = False
            print(f"LOG: [VS1053] Finished playing track. Total: {bytes_received//1024}KB")
            
    def cancel(self):
        """Hardware stop signal."""
        self.playing = False
        self.paused = False

    def pause(self):
        """Suspend reading from network."""
        print("LOG: [VS1053] Pausing stream read...")
        self.paused = True
        
    def resume(self):
        """Resume reading from network."""
        print("LOG: [VS1053] Resuming stream read...")
        self.paused = False


class HttpAudioStreamHandler:
    """Manages audio HTTP streaming. State machine: idle → play → paused/idle"""
    
    def __init__(self, config, control_ws=None, hardware_vs1053=None):
        """Initialize audio handler.
        
        Args:
            config: Device configuration
            control_ws: Used to send end-of-track events back if needed
            hardware_vs1053: Real VS1053 instance or None to fallback to Mock
        """
        self.config = config
        self.control_ws = control_ws
        
        # State machine
        self.state = "idle"  # idle, play, paused
        
        # Use real hardware if provided, otherwise fallback to terminal mock
        if hardware_vs1053:
            self.vs1053 = hardware_vs1053
        else:
            self.vs1053 = MockVS1053()
            
        self.stream_task = None
        self.reader = None
        self.writer = None
        
    def set_state(self, new_state: str):
        """Update handler state."""
        if new_state != self.state:
            print(f"LOG: [AUDIO] State: {self.state} → {new_state}")
            old_state = self.state
            self.state = new_state
            
            if new_state == "play":
                if old_state == "paused":
                    # Just unpause the hardware to start consuming bytes again
                    if hasattr(self.vs1053, "resume"):
                        self.vs1053.resume()
                else:
                    # Cancel any old streaming and open a new TCP socket
                    self._stop_stream()
                    self.stream_task = asyncio.create_task(self._stream_from_server())
            elif new_state == "paused":
                # Tell hardware to stop consuming bytes (don't close socket)
                if hasattr(self.vs1053, "pause"):
                    self.vs1053.pause()
            elif new_state in ("idle", "stopped"):
                self._stop_stream()
                
    def _stop_stream(self, cancel_task=True):
        """Stop playing and close hardware/socks."""
        if self.stream_task and cancel_task:
            self.stream_task.cancel()
            self.stream_task = None
            
        cancel_meth = getattr(self.vs1053, "cancel", None)
        if cancel_meth:
            try:
                # If it's an async method (like Peter Hinch hardware cancel), fire and forget
                # otherwise just call it (MockVS1053)
                import inspect
                # MicroPython sometimes doesn't have inspect.iscoroutinefunction, so safely handle it
                res = cancel_meth()
                if hasattr(res, "send"): # Is a coroutine or generator
                    asyncio.create_task(res)
            except:
                pass
        
        if self.writer:
            try:
                self.writer.close()
            except:
                pass
            self.reader = None
            self.writer = None
            
        gc.collect()

    async def _stream_from_server(self):
        """Fetch the stream via simple HTTP GET."""
        host = self.config['backend']['ip']
        port = self.config['backend']['port']
        # Backend reverse-proxy target for the active track
        path = "/api/mediaplayer/stream/current"
        
        print(f"LOG: [HTTP] Connecting to {host}:{port}{path}")
        
        try:
            # 1. Open Connection
            self.reader, self.writer = await asyncio.open_connection(host, port)
            
            # 2. HTTP Request (Use HTTP/1.0 to disable chunked transfer encoding!)
            req = f"GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n"
            self.writer.write(req.encode('utf-8'))
            await self.writer.drain()
            
            # 3. Fast-forward past HTTP headers
            print("LOG: [HTTP] Reading headers...")
            
            is_chunked = False
            while True:
                line = await self.reader.readline()
                if not line:
                    raise Exception("Connection closed before headers finished")
                if b"Transfer-Encoding: chunked" in line or b"transfer-encoding: chunked" in line:
                    is_chunked = True
                if line in (b'\r\n', b'\n'):
                    break # Blank line means end of headers
                    
            if is_chunked:
                print("WARNING: [HTTP] Server forced chunked encoding. Audio will be corrupted!")
                    
            print("LOG: [HTTP] Headers received, handing raw stream to VS1053 hardware")
            
            # 4. Give the raw TCP stream completely over to VS1053 library
            print("LOG: [VS1053] Starting hardware playback loop")
            if getattr(self.vs1053, "__class__", None).__name__ == "MockVS1053":
                await self.vs1053.play(self.reader)
            else:
                await self._pump_audio_stream(self.reader)
            
            # 5. Tell the backend the track finished naturally
            if self.control_ws and self.state == "play":
                print("LOG: [AUDIO] Hardware decode finished gracefully, telling server.")
                await self.control_ws.send(json.dumps({
                    "type": "track_finished",
                    "payload": {"reason": "stream_complete"}
                }))
                
        except asyncio.CancelledError:
            print("LOG: [HTTP] Stream task cancelled.")
        except Exception as e:
            print(f"LOG: [HTTP] Streaming error: {e}")
        finally:
            self._stop_stream(cancel_task=False)
            self.stream_task = None

    async def stream_loop(self):
        """Just holds state. Handled via set_state internally now."""
        # We don't even need the infinite loop anymore since stream starts on state switch.
        # But we'll keep it alive so main.py doesn't crash its gather()
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _pump_audio_stream(self, reader):
        """Asynchronously read from network, synchronously feed to VS1053."""
        print("LOG: [VS1053] Audio Streaming hardware loop started")
        self.vs1053._playing = True
        self.vs1053._cancnt = 0

        bytes_decoded = 0
        last_log_time = time.time()
        start_time = time.time()
        
        try:
            while self.state in ["play", "paused"]:
                if self.state == "paused":
                    await asyncio.sleep_ms(100)
                    continue

                chunk = await reader.read(4096)
                if not chunk:
                    print("LOG: [VS1053] Streaming EOF.")
                    break
                
                chunk_len = len(chunk)
                bytes_decoded += chunk_len
                
                # Periodic logging to prove stream is actually advancing
                now = time.time()
                if now - last_log_time >= 5:
                    elapsed = now - start_time
                    kbps = (bytes_decoded / 1024) / elapsed if elapsed > 0 else 0
                    print(f"LOG: [VS1053] Decoding... {bytes_decoded//1024}KB processed | {kbps:.1f} KB/s")
                    try:
                        # Log audio decoding stats
                        hdat0 = self.vs1053._read_reg(8) # _SCI_HDAT0
                        hdat1 = self.vs1053._read_reg(9) # _SCI_HDAT1
                        fmt = "Unknown"
                        if hdat1 >= 0xFFE0: fmt = "MP3"
                        elif hdat1 == 0x7665: fmt = "WAV"
                        elif hdat1 == 0x4154: fmt = "AAC"
                        elif hdat1 in (0x4f67, 0x4d34, 0x4d35): fmt = "OGG/M4A"
                        print(f"LOG: [VS1053] Audio Format: {fmt} (HDAT1=0x{hdat1:04X}, HDAT0=0x{hdat0:04X})")
                    except:
                        pass
                    last_log_time = now

                # We have up to 4096 bytes. The VS1053 accepts chunks of up to 32 bytes 
                # strictly while the DREQ line is high!
                mv = memoryview(chunk)
                rptr = 0
                bsize = chunk_len
                
                chk_cnt = 0
                while bsize > 0:
                    # Wait for device to be ready for 32 bytes
                    while not self.vs1053._dreq():
                        await asyncio.sleep_ms(0)
                        chk_cnt = 0
                        
                    wlen = min(32, bsize)
                    
                    # Manual fast-write directly via SPI, as Peter Hinch does
                    self.vs1053._xdcs(0)
                    self.vs1053._spi.write(mv[rptr : rptr + wlen])
                    self.vs1053._xdcs(1)
                    
                    rptr += wlen
                    bsize -= wlen
                    chk_cnt += 1
                    
                    # Ensure we don't lock out the async scheduler if VS1053 is consuming very fast.
                    if chk_cnt > 30:
                        await asyncio.sleep_ms(0)
                        chk_cnt = 0

                # Periodically collect garbage 
                gc.collect()
                    
        finally:
            self.vs1053._playing = False
            self.vs1053._cancnt = 0
            if getattr(self.vs1053, "_end_play", None):
                fake_buf = bytearray(32)
                try: asyncio.create_task(self.vs1053._end_play(fake_buf))
                except: pass
