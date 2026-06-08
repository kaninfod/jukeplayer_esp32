import re
with open("jukeplayer/lib/ws_events.py", "r") as f:
    content = f.read()

content = content.replace("def write_frame(self, opcode, data=b''):", "async def write_frame(self, opcode, data=b''):")
content = content.replace("self._write_all(header)", "await self._write_all(header)")
content = content.replace("self._write_all(mask_bits)", "await self._write_all(mask_bits)")
content = content.replace("self._write_all(data)", "await self._write_all(data)")

content = content.replace("def _write_all(self, data):", "async def _write_all(self, data):")
content = content.replace("                import time\n              time.sleep_ms(10)\n          \n          # Failed after all retries", "                await a.sleep_ms(40)\n          \n          # Failed after all retries")
content = content.replace("import time\n              time.sleep_ms(10)", "await a.sleep_ms(40)")
content = content.replace("for attempt in range(15):  # 15 attempts", "for attempt in range(40):  # 40 attempts")

content = content.replace("self.write_frame(OP_PONG, data)", "await self.write_frame(OP_PONG, data)")
content = content.replace("self.write_frame(opcode, buf)", "await self.write_frame(opcode, buf)")
content = content.replace("self.write_frame(OP_TEXT, buf.encode('utf-8'))", "await self.write_frame(OP_TEXT, buf.encode('utf-8'))")

content = content.replace("def send_header(header, *args):", "async def send_header(header, *args):")
content = content.replace("self.sock.write(header % args + '\\r\\n') # type: ignore", "await self._write_all(header % args + b'\\r\\n') # type: ignore")
content = content.replace("send_header(b'GET %s HTTP/1.1',", "await send_header(b'GET %s HTTP/1.1',")
content = content.replace("send_header(b'Host: %s:%s',", "await send_header(b'Host: %s:%s',")
content = content.replace("send_header(b'Connection: Upgrade')", "await send_header(b'Connection: Upgrade')")
content = content.replace("send_header(b'Upgrade: websocket')", "await send_header(b'Upgrade: websocket')")
content = content.replace("send_header(b'Sec-WebSocket-Key: %s', key)", "await send_header(b'Sec-WebSocket-Key: %s', key)")
content = content.replace("send_header(b'Sec-WebSocket-Version: 13')", "await send_header(b'Sec-WebSocket-Version: 13')")
content = content.replace("send_header(b'Origin:", "await send_header(b'Origin:")
content = content.replace("            send_header(b'%s: %s', key_header, value_header)", "            await send_header(b'%s: %s', key_header, value_header)")
content = content.replace("send_header(b'')", "await send_header(b'')")

with open("jukeplayer/lib/ws_events.py", "w") as f:
    f.write(content)
