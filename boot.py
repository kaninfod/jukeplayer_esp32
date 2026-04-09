# This file is executed on every boot (including wake-boot from deepsleep)
import esp
esp.osdebug(None)

import gc
gc.collect()

import json
import sys
import time

try:
    from jukeplayer.lib.logger import log
    from jukeplayer.lib import wifi_manager
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

    # 2. Sync NTP Time
    log.info("Attempting NTP time sync...")
    log.sync_time()

    # 3. Start WebREPL if enabled
    webrepl_cfg = config.get("webrepl", {})
    if webrepl_cfg.get("enabled"):
        try:
            import webrepl
            pw = webrepl_cfg.get("password", "jukeplayer_repl")
            # In modern MicroPython, setting the password explicitly works again, 
            # and writing webrepl_cfg.py directly might cause reload loops if it gets compiled.
            webrepl.start(password=pw)
            log.info("WebREPL started successfully.")
        except ImportError:
            log.error("WebREPL module not found. Skipping.")
        except ValueError as e:
            # ValueError often means it's already started or bound
            log.info(f"WebREPL already running or failed to bind: {repr(e)}")
        except Exception as e:
            log.error(f"Failed to start WebREPL: {repr(e)}")

    # Pass the config to main via a global or file 
    # (or let main load it again, it's fast enough)
    log.info("Boot Sequence Complete. Yielding to main.py...")

boot_sequence()
