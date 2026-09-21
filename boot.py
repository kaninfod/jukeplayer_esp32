# This file is executed on every boot (including wake-boot from deepsleep)
import esp
esp.osdebug(None)

import gc
gc.collect()

import json
import sys
import time

# Config safety net: preserve the last-good config before the app can overwrite it
# via a remote config_set (WS). If the primary fails to parse, boot falls back.
try:
    with open("config.json", "r") as f:
        cfg_data = f.read()
    with open("config.json.bak", "w") as f:
        f.write(cfg_data)
except Exception:
    pass  # config.json doesn't exist yet — first boot

try:
    from jukeplayer.core.logger import log
    from jukeplayer.core import wifi_manager
except ImportError as e:
    print(f"CRITICAL BOOT ERROR: Could not load logger or wifi_manager: {e}")
    sys.exit(1)

def _boot_forensics():
    """Crash forensics: the previous run's end state from RTC memory (survives
    panic/WDT/brownout resets) plus this boot's reset reason. RTC layout is 16
    bytes and every writer writes all 16 (rtc.memory() replaces the length):
      [0:4] crash_count [4:8] last_crash_ts [8:12] heartbeat magic [12:16] alive_ts
    """
    import struct
    import machine
    MAGIC_ALIVE = 0x414C4956  # 'ALIV'
    CRASH_WINDOW_S = 300  # matches main.py's crash-loop window
    count, last = 0, 0
    try:
        raw = machine.RTC().memory()
        count, last, magic, alive_ts = (
            struct.unpack("<IIII", raw[:16]) if len(raw) >= 16 else (0, 0, 0, 0)
        )
        if magic == MAGIC_ALIVE:
            log.warn(f"[CRASH] previous run alive at epoch {alive_ts} (30s heartbeat)")
        else:
            log.warn("[CRASH] previous run left no heartbeat (crashed in boot or <30s in)")
        if count:
            log.warn(f"[CRASH] crash counter: {count} crash(es), last at epoch {last}")
        # Clear the stale heartbeat; the telemetry loop re-arms it within 30s.
        machine.RTC().memory(struct.pack("<IIII", count, last, 0, 0))
    except Exception as e:
        log.error(f"[CRASH] forensics unavailable: {e}")
    # Tree integrity: the same files must read back identically across boots.
    # Changing len/sum between boots = unstable flash reads (boot-loop root);
    # stable digests plus sporadic AttributeErrors = RAM corruption instead.
    try:
        checks = []
        for path in ("main.py", "jukeplayer/services/button_handler.py"):
            buf = open(path, "rb").read()
            checks.append(f"{path.split('/')[-1]} len={len(buf)} sum={sum(buf)}")
        log.warn("[CRASH] tree check: " + " | ".join(checks))
    except Exception as e:
        log.error(f"[CRASH] tree check unavailable: {e}")
    try:
        rr = machine.reset_cause()
        names = {1: "PWRON (power-on or BROWNOUT)", 2: "HARD (panic / sw reset)",
                 3: "WDT (watchdog)", 4: "DEEPSLEEP", 5: "SOFT"}
        log.warn(f"[CRASH] reset reason: {names.get(rr, str(rr))}")
        # Hardware resets bypass main.py's Python-level crash counter (WDT and
        # panic never enter the exception machinery) — count them here so the
        # stay-down gate covers every reset class. 2026-09-19 night: ~40
        # unguarded WDT/HARD resets looped until morning because the counter
        # only saw Python-level crashes. Time source is the pre-NTP RTC clock
        # (consistent across boots, so the delta logic is sound).
        if rr in (2, 3):  # HARD panic or watchdog
            now = int(time.time())
            count = count + 1 if (now - last) < CRASH_WINDOW_S else 1
            machine.RTC().memory(struct.pack("<IIII", count, now, 0, 0))
            log.warn(f"[CRASH] hardware reset counted — {count} within {CRASH_WINDOW_S}s window")
            log.flush_now()
            if count >= 3:
                log.error(f"[CRASH] {count} hardware resets in {CRASH_WINDOW_S}s — staying down; power-cycle to recover")
                while True:
                    log.flush_now()
                    time.sleep(1)
        elif rr == 1:  # PWRON: fresh power-up — clear the counter (the manual recovery path)
            machine.RTC().memory(struct.pack("<IIII", 0, 0, 0, 0))
    except Exception as e:
        log.error(f"[CRASH] reset reason unavailable: {e}")

def load_config():
    """Load configuration from config.json file at device root.
    Falls back to config.json.bak if the primary fails to parse."""
    import os
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        log.info(f"[CFG] loaded — client: {config.get('client', {}).get('name', 'Unknown')}")
        return config
    except Exception as e:
        log.error(f"[CFG] critical error loading config.json: {e}")
        if os.path.exists("config.json.bak"):
            try:
                with open("config.json.bak", "r") as f:
                    config = json.load(f)
                log.warn(f"[CFG] fell back to config.json.bak — client: {config.get('client', {}).get('name', 'Unknown')}")
                return config
            except Exception as e2:
                log.error(f"[CFG] config.json.bak also failed: {e2}")
        return None

def boot_sequence():
    log.info("[BOOT] === JukePlayer boot sequence started ===")
    
    config = load_config()
    if not config:
        log.error("[BOOT] halting — missing or invalid config.json")
        while True:
            time.sleep(1)

    # Configure logging targets from config.json (console + optional syslog)
    log.configure_from_config(config)

    # Crash forensics must run AFTER logging config: the syslog boot buffer
    # only captures post-configuration lines, so a pre-config call loses the
    # [CRASH] lines on every boot replay.
    _boot_forensics()

    wifi_cfg = config.get("wifi", {})
    ssid = wifi_cfg.get("ssid")
    password = wifi_cfg.get("password")

    if not ssid or not password:
        log.error("[BOOT] halting: missing WiFi credentials in config.json")
        while True:
            time.sleep(1)

    # 1. Connect to WiFi
    ip = wifi_manager.connect(ssid, password)
    if not ip:
        log.error("[BOOT] halting: failed to connect to WiFi")
        while True:
            time.sleep(1)

    # Network is up: start flushing buffered syslog messages (boot log replay)
    log.mark_syslog_online()

    # 2. Sync NTP Time
    log.info("[NTP] attempting time sync...")
    log.sync_time()
    # Diagnostic probe (2026-09-19): the forensics epochs read ~30 years
    # behind the real clock despite "synced successfully" — settle whether
    # ntptime.settime() actually reaches time.time() on this build.
    log.info(f"[NTP] device epoch after sync: {int(time.time())}")

    # 3. Start WebREPL if enabled
    webrepl_cfg = config.get("webrepl", {})
    if webrepl_cfg.get("enabled"):
        try:
            pw = webrepl_cfg.get("password", "")
            # Max password length for WebREPL is 9 characters! 
            if len(pw) > 9:
                pw = pw[:9]
                log.warn(f"[WEBREPL] password truncated to 9 chars: {pw}")
                
            with open("webrepl_cfg.py", "w") as f:
                f.write(f"PASS = '{pw}'\n")
                
            # If webrepl_cfg got cached as missing, we clear it so it reloads the file
            if "webrepl_cfg" in sys.modules:
                del sys.modules["webrepl_cfg"]
                
            import webrepl
            webrepl.start()
            log.info("[WEBREPL] started successfully")
        except ImportError:
            log.error("[WEBREPL] module not found — skipping")
        except Exception as e:
            log.error(f"[WEBREPL] failed to start: {repr(e)}")

    # Pass the config to main via a global or file 
    # (or let main load it again, it's fast enough)
    log.info("[BOOT] boot sequence complete — yielding to main.py")

boot_sequence()
