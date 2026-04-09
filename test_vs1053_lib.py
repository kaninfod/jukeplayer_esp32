import uasyncio as asyncio
from machine import Pin, SPI
import sys
import os

# We need to add the lib directory to sys.path so we can import vs1053
try:
    sys.path.append("/jukeplayer/lib")
    from vs1053 import VS1053
except ImportError:
    # Maybe we are running from a subfolder
    sys.path.append("jukeplayer/lib")
    from vs1053 import VS1053

# Config
SCK_PIN = 14
MISO_PIN = 12
MOSI_PIN = 13
XCS_PIN = 15
XDCS_PIN = 17
DREQ_PIN = 27
RESET_PIN = -1 

import time

async def main():
    def log(msg):
        print(f"[{time.time()}] {msg}")
        
    log("Initializing SPI...")
    spi = SPI(2, sck=Pin(SCK_PIN), mosi=Pin(MOSI_PIN), miso=Pin(MISO_PIN))

    log("Configuring pins...")
    if RESET_PIN == -1:
        reset = Pin(22, Pin.OUT, value=1) 
    else:
        reset = Pin(RESET_PIN, Pin.OUT, value=1) 
        
    xcs = Pin(XCS_PIN, Pin.OUT, value=1)
    xdcs = Pin(XDCS_PIN, Pin.OUT, value=1)
    dreq = Pin(DREQ_PIN, Pin.IN)

    log(f"Test initial DREQ state: {dreq.value()}")

    log("Initializing VS1053 (UNBUFFERED mode)...")
    player = VS1053(spi, reset, dreq, xdcs, xcs, buffered=False)
    
    log("Setting absolute maximum volume (0,0)...")
    player.volume(0, 0)
    
    log("Testing sine wave for 5 seconds...")
    await player.sine_test(5)
    log("Sine wave finished. Done.")
    
    try:
        log("Playing /test.mp3 ...")
        t0 = time.time()
        with open('/test.mp3', 'rb') as f:
            await player.play(f)
        log(f"Playback ended. Total time: {time.time() - t0} seconds")
    except Exception as e:
        log(f"Could not play mp3: {e}")

if __name__ == '__main__':
    asyncio.run(main())