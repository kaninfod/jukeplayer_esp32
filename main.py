# JukePlayer boot entrypoint with crash watchdog:
# an app crash is logged, counted, and the device reboots after a short grace
# so the log can flush. Deliberate interrupts (Thonny / REPL sessions) stay
# down for maintenance. A crash loop (repeated rapid crashes) stops the
# auto-reboot instead of looping forever.
import gc
import time
import sys
import machine
from jukeplayer.core.logger import log

CRASH_WINDOW_S = 300   # crashes closer together than this count as a loop
MAX_CRASHES = 3        # stay down instead of rebooting at this many
REBOOT_GRACE_S = 5     # let the crash log flush before machine.reset()

def _bump_crash_counter():
    """Increment the crash counter in RTC memory (survives resets, cleared by
    power loss). Returns the number of crashes within the window."""
    import struct
    try:
        rtc = machine.RTC()
        try:
            raw = rtc.memory()
        except Exception:
            raw = b""
        count, last = 0, 0
        if raw and len(raw) >= 8:
            count, last = struct.unpack("<II", raw[:8])
        now = int(time.time())
        count = count + 1 if (now - last) < CRASH_WINDOW_S else 1
        try:
            rtc.memory(struct.pack("<II", count, now))
        except Exception:
            pass
        return count
    except Exception:
        # RTC memory unavailable (unexpected firmware) — degrade to plain restart
        return 1

def start_app():
    gc.collect()
    try:
        log.info("Starting Jukebox app...")
        from jukeplayer.app import main
        import asyncio
        asyncio.run(main())
    except KeyboardInterrupt:
        # deliberate interrupt (Thonny / REPL sessions): stay down
        log.info("App stopped by KeyboardInterrupt")
    except Exception as e:
        log.error("Application crashed:")
        sys.print_exception(e)
        crashes = _bump_crash_counter()
        if crashes >= MAX_CRASHES:
            log.error(
                f"Crash loop detected ({crashes} crashes within {CRASH_WINDOW_S}s) "
                "- staying down; power-cycle or long-press stop to recover"
            )
            return
        log.info(f"Auto-restarting in {REBOOT_GRACE_S}s (crash #{crashes})")
        time.sleep(REBOOT_GRACE_S)
        machine.reset()

start_app()