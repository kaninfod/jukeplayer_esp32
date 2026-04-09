import time
from machine import Pin, SPI
import gc

# ---------------------------------------------------------
# VS1053 Driver Configuration (matched from app.py)
# ---------------------------------------------------------
VS1053_SPI_UNIT = 2
VS1053_BAUDRATE = 2000000
VS1053_SCK = 14
VS1053_MOSI = 13
VS1053_MISO = 12
VS1053_DREQ = 34
VS1053_XDCS = 26
VS1053_XCS = 15

def run_test():
    print("Starting VS1053 Hardware Test...")
    gc.collect()
    
    try:
        from jukeplayer.lib.vs1053 import VS1053
    except ImportError:
        print("ERROR: Could not import jukeplayer.lib.vs1053")
        return

    # SPI Bus
    print("Selecting SPI bus...")
    spi_vs = SPI(VS1053_SPI_UNIT,
                 baudrate=VS1053_BAUDRATE,
                 sck=Pin(VS1053_SCK),
                 mosi=Pin(VS1053_MOSI),
                 miso=Pin(VS1053_MISO))

    # We hardwired RESET to 3.3V, so provide a dummy lambda
    reset_pin = lambda x: None

    print("Initializing VS1053...")
    try:
        vs1053 = VS1053(
            spi_vs,
            reset=reset_pin,
            dreq=Pin(VS1053_DREQ, Pin.IN),
            xdcs=Pin(VS1053_XDCS, Pin.OUT),
            xcs=Pin(VS1053_XCS, Pin.OUT)
        )
        print("VS1053 Initialization complete.")
    except Exception as e:
        print(f"FAILED to initialize VS1053 hardware: {e}")
        return

    print("Setting volume to 100% (-0 dB)")
    vs1053.volume(0, 0, powerdown=False)
    
    # Check registers before playing
    hdat0 = vs1053._read_reg(8)
    hdat1 = vs1053._read_reg(9)
    print(f"Initial register state: HDAT0=0x{hdat0:04X}, HDAT1=0x{hdat1:04X}")

    filename = '/test.mp3'
    print(f"Attempting to play local file: {filename}")
    
    try:
        with open(filename, 'rb') as f:
            print("File opened successfully. Playing...")
            # We'll use the driver's block write loop manually for full visibility
            # The play() coroutine relies on asyncio which isn't running in this synchronous test.
            
            bytes_decoded = 0
            chunk_size = 4096
            start = time.time()
            
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    print("Reached EOF")
                    break
                    
                bytes_decoded += len(chunk)
                mv = memoryview(chunk)
                rptr = 0
                bsize = len(chunk)
                
                while bsize > 0:
                    while not vs1053._dreq():
                        time.sleep_ms(1)
                        
                    wlen = min(32, bsize)
                    
                    vs1053._xdcs(0)
                    vs1053._spi.write(mv[rptr : rptr + wlen])
                    vs1053._xdcs(1)
                    
                    rptr += wlen
                    bsize -= wlen
                
                # Check format midway
                if bytes_decoded == chunk_size * 10:  # After ~40KB
                    hdat0 = vs1053._read_reg(8)
                    hdat1 = vs1053._read_reg(9)
                    print(f"Mid-stream registers: HDAT0=0x{hdat0:04X}, HDAT1=0x{hdat1:04X}")
                    
                time.sleep_ms(10)  # Gentle yield
                
            elapsed = time.time() - start
            print(f"Finished playing {bytes_decoded//1024}KB in {elapsed} seconds.")
            
    except OSError as e:
        print(f"File error: {e}. Are you sure {filename} exists on the ESP32 filesystem?")
    except Exception as e:
        print(f"Playback error: {e}")

if __name__ == "__main__":
    run_test()