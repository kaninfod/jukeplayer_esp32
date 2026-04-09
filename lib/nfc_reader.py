import lib.rc522 as rc522
from machine import Pin
import time

class NFCReader:
    """Wrapper for RC522 NFC reader on dedicated SPI1/HSPI bus.
    
    Includes timeout protection to prevent hanging on faulty card reads.
    """
    
    def __init__(self, spi, rst_pin=4, cs_pin=5, timeout_ms=1000):
        """Initialize NFC reader on separate SPI bus.
        
        Args:
            spi: SPI1 (HSPI) instance (separate from display which uses SPI2/VSPI)
            rst_pin: Reset pin
            cs_pin: Chip Select pin
            timeout_ms: Timeout for individual operations (default 1000ms)
        """
        self.rdr = rc522.MFRC522(rst_pin, cs_pin, spi)
        self.cs = Pin(cs_pin, Pin.OUT, value=1)
        self.default_key = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
        self.timeout_ms = timeout_ms
        self.last_read_time = 0
        self.last_successful_read = 0
    
    def select_chip(self):
        """Select the NFC chip on dedicated SPI bus."""
        self.cs.value(0)
    
    def deselect_chip(self):
        """Deselect the NFC chip on dedicated SPI bus."""
        self.cs.value(1)
    
    def read_album_id(self, block=4):
        """Read album ID from NFC card block with timeout protection.
        
        Args:
            block: Block number to read (default 4)
            
        Returns:
            str: Album ID (e.g., 'al-138') or None if read fails or times out
        """
        self.select_chip()
        start_time = time.ticks_ms()
        
        try:
            # Check if still in cooldown from last successful read (prevent rapid re-reads)
            now = time.ticks_ms()
            if time.ticks_diff(now, self.last_successful_read) < 500:
                return None
            
            self.last_read_time = now
            
            # Quick check for card presence - if no card detected, return immediately
            try:
                (stat, tag_type) = self.rdr.request(self.rdr.REQIDL)
            except Exception as e:
                print(f"LOG: NFC request error (timeout): {e}")
                return None
            
            if stat != self.rdr.OK:
                # No card detected
                return None
            
            # Check timeout after each operation
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > self.timeout_ms:
                print(f"LOG: NFC read timeout at anticoll check")
                return None
            
            # Card detected, proceed with full read
            try:
                (stat, raw_uid) = self.rdr.anticoll()
            except Exception as e:
                print(f"LOG: NFC anticoll error: {e}")
                return None
            
            if stat != self.rdr.OK:
                return None
            
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > self.timeout_ms:
                print(f"LOG: NFC read timeout at select_tag")
                return None
            
            try:
                if self.rdr.select_tag(raw_uid) != self.rdr.OK:
                    return None
            except Exception as e:
                print(f"LOG: NFC select_tag error: {e}")
                return None
            
            print(f"LOG: UID detected: {[hex(x) for x in raw_uid]}")
            

            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > self.timeout_ms:
                print(f"LOG: NFC read timeout at auth")
                return None
            
            # Authenticate with default Mifare Classic key
            try:
                auth_stat = self.rdr.auth(self.rdr.AUTHENT1A, block, self.default_key, raw_uid)
            except Exception as e:
                print(f"LOG: NFC auth error: {e}")
                return None
            
            if auth_stat != self.rdr.OK:
                print(f"LOG: Auth failed with status {auth_stat}")
                return None
            
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > self.timeout_ms:
                print(f"LOG: NFC read timeout at block read")
                return None
            
            # Read the block
            try:
                raw_data = self.rdr.read(block)
            except Exception as e:
                print(f"LOG: NFC read error: {e}")
                raw_data = None
            finally:
                try:
                    self.rdr.stop_crypto1()
                except:
                    pass
            
            if not raw_data:
                return None
            
            print(f"LOG: Raw Bytes from Block {block}: {raw_data}")
            
            # Parse as ASCII string
            album_id = "".join([chr(x) for x in raw_data if 32 <= x <= 126]).strip()
            print(f"LOG: Parsed Album ID: [{album_id}]")
            
            if album_id:
                self.last_successful_read = time.ticks_ms()
            
            return album_id if album_id else None
            
        finally:
            self.deselect_chip()
