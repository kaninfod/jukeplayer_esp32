import network
import time
from jukeplayer.lib.logger import log

def connect(ssid, password, timeout_sec=30):
    """
    Connect to a WiFi network.
    Returns the IP address if successful, None otherwise.
    """
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if wlan.isconnected():
        log.info(f"Already connected to WiFi. IP: {wlan.ifconfig()[0]}")
        return wlan.ifconfig()[0]
        
    log.info(f"Connecting to WiFi: {ssid}...")
    wlan.connect(ssid, password)
    
    start_time = time.time()
    while not wlan.isconnected():
        if time.time() - start_time > timeout_sec:
            log.error(f"WiFi connection timeout after {timeout_sec}s")
            return None
        time.sleep_ms(100)
        
    ip = wlan.ifconfig()[0]
    log.info(f"WiFi Connected! IP: {ip}")
    return ip