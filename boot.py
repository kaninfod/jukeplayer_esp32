# This file is executed on every boot (including wake-boot from deepsleep)
import esp
esp.osdebug(None)

import gc
gc.collect()

import json
import sys
import time

try:
    from jukeplayer.core.logger import log
    from jukeplayer.core import wifi_manager
except ImportError as e:
    print(f"CRITICAL BOOT ERROR: Could not load logger or wifi_manager: {e}")
    sys.exit(1)

def load_config():
    """Load configuration from config.json file at device root."""
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        log.info(f"Config loaded config.json - Client: {config.get('client', {}).get('name', 'Unknown')}")
        return config
    except Exception as e:
        log.error(f"CRITICAL ERROR loading config.json: {e}")
        # Return fallback configuration or halt
        return None

def boot_sequence():
    log.info("=== JukePlayer Boot Sequence Started ===")
    
    config = load_config()
    if not config:
        log.error("Halting boot sequence due to missing or invalid config.json.")
        while True:
            time.sleep(1)

    # Configure logging targets from config.json (console + optional syslog)
    log.configure_from_config(config)

    wifi_cfg = config.get("wifi", {})
    ssid = wifi_cfg.get("ssid")
    password = wifi_cfg.get("password")

    if not ssid or not password:
        log.error("Halting boot sequence: Missing WiFi credentials in config.json.")
        while True:
            time.sleep(1)

    # 1. Connect to WiFi
    ip = wifi_manager.connect(ssid, password)
    if not ip:
        log.error("Halting boot sequence: Failed to connect to WiFi.")
        while True:
            time.sleep(1)

    # Network is up: start flushing buffered syslog messages (boot log replay)
    log.mark_syslog_online()

    # 2. Sync NTP Time
    log.info("Attempting NTP time sync...")
    log.sync_time()

    # 3. Start WebREPL if enabled
    webrepl_cfg = config.get("webrepl", {})
    if webrepl_cfg.get("enabled"):
        try:
            pw = webrepl_cfg.get("password", "")
            # Max password length for WebREPL is 9 characters! 
            if len(pw) > 9:
                pw = pw[:9]
                log.info(f"WebREPL password truncated to 9 chars: {pw}")
                
            with open("webrepl_cfg.py", "w") as f:
                f.write(f"PASS = '{pw}'\n")
                
            # If webrepl_cfg got cached as missing, we clear it so it reloads the file
            if "webrepl_cfg" in sys.modules:
                del sys.modules["webrepl_cfg"]
                
            import webrepl
            webrepl.start()
            log.info("WebREPL started successfully.")
        except ImportError:
            log.error("WebREPL module not found. Skipping.")
        except Exception as e:
            log.error(f"Failed to start WebREPL: {repr(e)}")

    # Pass the config to main via a global or file 
    # (or let main load it again, it's fast enough)
    log.info("Boot Sequence Complete. Yielding to main.py...")

boot_sequence()
