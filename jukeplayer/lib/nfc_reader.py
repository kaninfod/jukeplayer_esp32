import jukeplayer.lib.rc522 as rc522
from machine import Pin
import time

class NFCReader:
    """Wrapper for RC522 NFC reader on dedicated SPI1/HSPI bus.
    
    Includes timeout protection to prevent hanging on faulty card reads.
    Can operate in dummy mode for testing without hardware.
    """
    
    def __init__(self, spi, rst_pin=4, cs_pin=5, timeout_ms=1000, dummy_mode=False):
        """Initialize NFC reader on separate SPI bus.
        
        Args:
            spi: SPI1 (HSPI) instance (separate from display which uses SPI2/VSPI)
            rst_pin: Reset pin
            cs_pin: Chip Select pin
            timeout_ms: Timeout for individual operations (default 1000ms)
            dummy_mode: If True, don't initialize actual RC522 hardware
        """
        self.dummy_mode = dummy_mode
        self.rdr = None
        self.cs = None
        
        if dummy_mode:
            print(f"LOG: NFC reader initialized in DUMMY MODE (no hardware reads)")
        else:
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
        if self.dummy_mode:
            return None  # Dummy mode: always return None
        
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
                print(f"LOG: ❌ NFC request error (timeout): {e}")
                return None
            
            if stat != self.rdr.OK:
                # No card detected
                return None
            
            # Check timeout after each operation
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > self.timeout_ms:
                print(f"LOG: ❌ NFC read timeout at anticoll check")
                return None
            
            # Card detected, proceed with full read
            try:
                (stat, raw_uid) = self.rdr.anticoll()
            except Exception as e:
                print(f"LOG: ❌ NFC anticoll error: {e}")
                return None
            
            if stat != self.rdr.OK:
                return None
            
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > self.timeout_ms:
                print(f"LOG: ❌ NFC read timeout at select_tag")
                return None
            
            try:
                if self.rdr.select_tag(raw_uid) != self.rdr.OK:
                    return None
            except Exception as e:
                print(f"LOG: ❌ NFC select_tag error: {e}")
                return None
            
            print(f"LOG: ✅ UID detected: {[hex(x) for x in raw_uid]}")
            
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > self.timeout_ms:
                print(f"LOG: ❌ NFC read timeout at auth")
                return None
            
            # Authenticate with default Mifare Classic key
            try:
                auth_stat = self.rdr.auth(self.rdr.AUTHENT1A, block, self.default_key, raw_uid)
            except Exception as e:
                print(f"LOG: ❌ NFC auth error: {e}")
                return None
            
            if auth_stat != self.rdr.OK:
                print(f"LOG: ❌ Auth failed with status {auth_stat}")
                return None
            
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > self.timeout_ms:
                print(f"LOG: ❌ NFC read timeout at block read")
                return None
            
            # Read the block
            try:
                raw_data = self.rdr.read(block)
            except Exception as e:
                print(f"LOG: ❌ NFC read error: {e}")
                raw_data = None
            finally:
                try:
                    self.rdr.stop_crypto1()
                except:
                    pass
            
            if not raw_data:
                return None
            
            print(f"LOG: ✅ Raw Bytes from Block {block}: {raw_data}")
            
            # Parse as ASCII string
            album_id = "".join([chr(x) for x in raw_data if 32 <= x <= 126]).strip()
            print(f"LOG: ✅ Parsed Album ID: [{album_id}]")
            
            if album_id:
                self.last_successful_read = time.ticks_ms()
            
            return album_id if album_id else None
            
        finally:
            self.deselect_chip()
    
    def write_data(self, album_id, block=4, timeout_ms=None):
        """Write album_id to NFC card block 4 with timeout protection.
        
        Args:
            album_id: Album ID string to write (will be padded to 16 bytes)
            block: Block number to write to (default 4)
            timeout_ms: Timeout for write operation (uses self.timeout_ms if None)
            
        Returns:
            dict with keys:
            - status: "success", "timeout", or "error"
            - uid: Card UID as hex string (e.g., "0x12345678")
            - error_message: Error description if status != "success"
        """
        if self.dummy_mode:
            return {"status": "success", "uid": "0xDUMMY", "album_id": album_id}
        
        if timeout_ms is None:
            timeout_ms = self.timeout_ms
        
        self.select_chip()
        start_time = time.ticks_ms()
        
        try:
            print(f"LOG: NFC write starting to block {block}")
            print(f"LOG: NFC write timeout: {timeout_ms}ms - waiting for card...")
            
            # Poll for card presence repeatedly until timeout
            while True:
                elapsed = time.ticks_diff(time.ticks_ms(), start_time)
                if elapsed > timeout_ms:
                    print(f"LOG: NFC write timeout after {elapsed}ms - no card detected")
                    return {"status": "timeout", "uid": None, "error_message": f"No card detected within {timeout_ms}ms"}
                
                # Check for card presence
                try:
                    (stat, tag_type) = self.rdr.request(self.rdr.REQIDL)
                    if stat == self.rdr.OK:
                        print(f"LOG: Card detected after {elapsed}ms - proceeding with write")
                        break
                except Exception as e:
                    # No card yet, keep polling
                    pass
                
                # Small delay before next attempt (avoid tight polling)
                time.sleep_ms(100)
            
            # At this point, card was detected (stat == OK from polling loop)
            # Check timeout
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > timeout_ms:
                return {"status": "timeout", "error_message": "Write timeout: card detection"}
            
            # Get card UID
            try:
                (stat, raw_uid) = self.rdr.anticoll()
            except Exception as e:
                print(f"LOG: NFC anticoll error: {e}")
                return {"status": "error", "error_message": f"Card detection failed: {e}"}
            
            if stat != self.rdr.OK:
                return {"status": "error", "error_message": "Failed to read card UID"}
            
            # Convert UID to hex string
            uid_hex = "0x" + "".join(f"{b:02x}" for b in raw_uid)
            print(f"LOG: UID detected: {uid_hex}")
            
            # Check timeout
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > timeout_ms:
                return {"status": "timeout", "uid": uid_hex, "error_message": "Write timeout: card selection"}
            
            # Select card
            try:
                if self.rdr.select_tag(raw_uid) != self.rdr.OK:
                    return {"status": "error", "uid": uid_hex, "error_message": "Failed to select card"}
            except Exception as e:
                print(f"LOG: NFC select_tag error: {e}")
                return {"status": "error", "uid": uid_hex, "error_message": f"Card selection failed: {e}"}
            
            # Check timeout
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > timeout_ms:
                return {"status": "timeout", "uid": uid_hex, "error_message": "Write timeout: authentication"}
            
            # Authenticate with block
            try:
                auth_stat = self.rdr.auth(self.rdr.AUTHENT1A, block, self.default_key, raw_uid)
            except Exception as e:
                print(f"LOG: NFC auth error: {e}")
                return {"status": "error", "uid": uid_hex, "error_message": f"Authentication failed: {e}"}
            
            if auth_stat != self.rdr.OK:
                return {"status": "error", "uid": uid_hex, "error_message": f"Authentication failed (status {auth_stat})"}
            
            # Check timeout
            elapsed = time.ticks_diff(time.ticks_ms(), start_time)
            if elapsed > timeout_ms:
                return {"status": "timeout", "uid": uid_hex, "error_message": "Write timeout: write operation"}
            
            # Prepare data (pad to 16 bytes)
            data = [ord(c) for c in album_id[:16]]
            while len(data) < 16:
                data.append(ord(' '))
            
            print(f"LOG: Writing to block {block}: {album_id}")
            
            # Write the block
            try:
                write_stat = self.rdr.write(block, data)
            except Exception as e:
                print(f"LOG: NFC write error: {e}")
                write_stat = self.rdr.ERR
            finally:
                try:
                    self.rdr.stop_crypto1()
                except:
                    pass
            
            if write_stat != self.rdr.OK:
                return {"status": "error", "uid": uid_hex, "error_message": f"Write failed (status {write_stat})"}
            
            print(f"LOG: ✅ Write successful: {album_id}")
            return {"status": "success", "uid": uid_hex}
            
        finally:
            self.deselect_chip()
