import uos
import os

with open('/test.mp3', 'rb') as f:
    buf = bytearray(32)
    res = f.readinto(buf)
    print("readinto returned:", res)
