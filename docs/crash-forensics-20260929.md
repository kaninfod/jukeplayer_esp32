# Crash forensics 2026-09-29 — the two-layer answer

**Status:** evidence collected; hardware-layer verdict pending bench test.
**The surviving configuration:** commit `63c4259`, git tag **`proven-klangmeister-20260929`**,
device config preserved at `config_files/S3/config.json`.

## Naming (fixed 2026-09-29, use always)

| Name | What | Provenance |
|---|---|---|
| **green board** | testbench PCB + devboard | built for the bench; carries a 470 µF electrolytic in parallel with a 10 µF ceramic at the VBUS inlet (the wifi-burst-era mod); soldered pre-2026-09-20 |
| **red board** | klangmeister PCB + devboard | 3rd-gen PCB, ~3 months in the radio chassis; **no bulk electrolytic** |

Tonight the green board sits in the klangmeister chassis driving the ILI9488, RC522,
encoder and buttons, running the klangmeister config (device_id `gvaudio`). The red
board is benched, pending the test protocol below.

## The retained software

Commit **`63c4259`** (tag `proven-klangmeister-20260929`) — 2149326 proven stack
(2.7-day run, 09-25→09-28, zero crashes) plus two fixes committed 09-29:

1. **ili9488 full-frame sync push** (63c4259): the async segment refresh
   (per-segment DMA bursts yielding inside a scheduled task) is replaced by ONE
   synchronous `display.show()` while holding `spi_ctl.acquire(display_baudrate)`.
   Cost: the loop blocks ~100–150 ms per full-frame refresh; pings arrive every
   20–60 s, so the block is invisible.
2. **idle task started from the app's run()** (8903c94): `create_task` from sync
   contexts (init/update/wake branches) does not schedule on this port. The
   pre-fix trees shipped the idle task started from contexts that never ran it —
   the original "auto-off stopped working" dysfunction, reproduced on demand by
   the 7f9e7e4 rollback (zero idle ticks in 8.5 min).

Both devboards (red and green) run this tree **byte-identically** as of 17:24
(pushed over serial; the export lives at `/tmp/km-restore-63c4259`, repo-side it
is the tag). Full-tree byte verification pending — see queue.

## Layer 1 — the software mechanism (code-level, provable)

**The pre-fix crash surface.** The seg-refresh build scheduled a display task that
repeatedly re-initialised the SPI bus and pushed segments while yielding between
segments. When that task's state interleaved with socket IO during a WS handshake,
uasyncio's poller crashed deterministically:

```
ValueError: too many values to unpack (expected 2)
  in asyncio core wait_io_event
```

Reproduced 09-29 on two different backends at every baudrate and refresh split —
always exactly when a refresh task was live during the handshake with the ILI9488
active. The sync push removes the interleaving entirely: there is no per-segment
task that can collide with the IO queue; the push holds the bus lock and either
completes or blocks, and the event loop state stays intact.

**Evidence of the fix working under real load (2026-09-29 evening, green board in
the klangmeister chassis):**

```
17:26:23  idle task started from loop context / full-frame sync push / WS connected
17:26:24  ✅ Registration successful (60843cbe) — handshake survived sync pushes
17:34-35  backend rebooted by the owner → client walked the full retry ladder
          (5×5 s attempts, 2 s backoff cycles) — telemetry alive between attempts
17:36:20  volume 47 sent to backend (encoder, end-to-end)
17:36:30  NFC card read — UID parsed, album ID al-505
17:38:48  owner reboot — clean boot, reconnect, registry re-entry
17:41-42  alive: pings, idle ticks, MEM flat ~7649 KB
```

**Observability note that made today legible:** since 63c4259-era deploys carry the
crash-path flush, Python-level crashes now reach Loki with full tracebacks
(demonstrated 17:18:44, below). Silent-loss symptoms in Loki are therefore
*not* Python exceptions — they are pipeline deaths (loop/poller/hard reset) or
pre-`mark_syslog_online` deaths.

## Layer 2 — tonight's board correlation (hypothesis, pending bench test)

Same software (63c4259), same config, same chassis, same peripherals assembly —
two different PCB/devboard pairs, opposite outcomes:

| Boot | Board | Software | Result |
|---|---|---|---|
| 14:13:51 / 14:17:02 / 14:18:57 / 14:32:24 | red | mixed trees | deaths at scattered init points, no tracebacks |
| 17:04:31 | red | **63c4259** | WS ok (+40 s), then flush died ~17:05:05; RE turn → death, ×3, "every time" |
| 17:26 → 17:42+ | green | **63c4259** | RE clean, BE-reboot reconnect clean, NFC read clean, flush alive throughout |

The death cluster **followed the red board across a software change that
everything else rides fine on**.

The candidate physical delta (owner-provided): the green board carries the
**470 µF electrolytic in parallel with a 10 µF ceramic at the VBUS inlet** — the
wifi-burst-era mod (bulk + fast bypass pair, at the shortest high-current loop).
The red board carries none.

**Why this is a mechanism, not a finding:** the red board ran 2.7 days stable
without the capacitor — so its absence alone is not sufficient. The hypothesis is
that red's bulk reserve was always marginal, and *tonight's* conditions (Pi3
backend cadence, the fresh logger's boot-replay/flush bursts, the sync-push era's
burst shape) finally pushed transients through the inlet faster than the regulator
could follow — taking out the telemetry path (+21 s / +40 s windows, twice) and
the RE-interaction path (RE → sync push burst → brownout-class reset → blank +
reboot, deterministically). The green board's bulk reserve rides the same bursts.

**This remains a hypothesis.** The falsifying/confirming evidence is cheap and
ordered below.

## Red-board bench protocol (next maintenance window)

1. Wire the red board to the proven setup and boot with console-off config as
   delivered. Observe: does the flush survive past the +40 s window on the bench?
2. **Read the RTC reset-class forensics FIRST** — `boot.py::_boot_forensics`
   classifies resets (0 UNKNOWN, 1 POWER/brownout, 3 WDT, 5 SOFT) into the RTC
   crash record (`machine.RTC().memory()`, 16-byte layout:
   `[0:4] crash_count [4:8] last_crash_ts [8:12] heartbeat magic [12:16] alive_ts`).
   Reproduce one RE death, then read the class.
   - **brownout/POWER class** → rail theory gains direct evidence; proceed to (3)
   - **WDT or hard-fault class** → capacitor hypothesis loses support; move to
     the SPI line / RE wiring / converter path suspects
3. **Only then solder** the 470 µF || 10 µF pair at the red board's VBUS inlet.
4. Re-run the identical test: boot, 60 s untouched, one slow RE turn, then fast
   turns. If the death class or frequency changes, the root cause is named and
   the mod is justified component-by-component.

The order is deliberate: reproduce → classify → modify → re-test. The reset-class
read costs nothing and discriminates before a soldering iron is warm.

## Queue (non-critical, from tonight)

- **Cover art fetch fails**: `[COVER] download failed: 'Stream' object has no
  attribute 'drain'` — the fetcher's `await writer.drain()` does not exist on this
  build's uasyncio stream writer. Non-fatal (caught, logged); cover art simply
  won't load. Fix candidate: drop the drain (HTTP/1.0 close semantics) or use
  `await asyncio.sleep_ms(0)` — needs a small local change + bench test.
- **Full-tree byte verification protocol**: 56 files vs the export
  (`/tmp/verify_tree.sh` — note macOS lacks GNU `timeout`; the fixed script is
  staged). Run during a planned maintenance window only — WebREPL connection
  storms are themselves load bursts (observed 16:0x; treat as an experiment
  variable, not a diagnostic).
- **Serial access lesson (2026-09-29)**: mpremote's "failed to access /dev/cu.* (may
  be in use by another program)" can mask the *agent sandbox* denial
  (Errno 1 Operation not permitted — serial device open under workspace sandbox).
  Distinguish with a direct `pyserial` open; the true holder is only then visible.
  The Thonny-holds-the-port conflict remains real and separate.
- **AGENTS.md updates owed**: staged-boot design notes, sync-push law (segmented
  DMA + WS interleave = poller crash; keep full-frame pushes), device-local edit
  violations, DNS-blocking lesson, deploy manifest discipline (56-file list), the
  green/red board naming, serial errno lesson.
- The red board's RTC forensics read at its next power-up (before any soldering)
  is independent of everything else and answers the reset-class question alone.

### Cover-change pain — the partial-push candidate (planned, next week)

The owner's biggest current pain: album cover changes are slow. Root cause: a cover
blit touches only the 180×180 art region (~65 KB RGB565), but the retained tree
pushes the full 307 KB frame (~100–150 ms at 24 MHz). The ILI9488 fully supports
windowed writes.

Candidate (Tier-2, bench on the 'kitchen' board first): add `show_region(col,row,
w,h)` to the ili9488 driver (CASET/RASET windowed write of the dirty rows), call it
after cover blits only; label changes keep the full-frame path. Expected: album-
change push ~5× faster (~20–25 ms) inside the proven tree, zero new hardware.

### Future-PCB design notes (not scheduled — recorded for the redesign)

- **NFC on I2C**: the owner has PN532 modules and prior I2C experience with them
  (confirmed 2026-10). The past "can't write cards over I2C" limitation was the
  *driver*, not the interface — MIFARE auth/write is protocol-level and
  interface-independent. PN532 on I2C is the mature route; RC522 cheap modules are
  SPI-hardwired at reset, so a fresh PCB is the natural place to switch interfaces.
- NFC off the shared SPI bus unlocks `esp_lcd`-class display drivers (s3lcd family,
  russhughes) for any future panel upgrade (ST7796S considered 2026-09-30) — the
  two changes compose deliberately.
- Carry forward: the 470 µF || 10 µF cap pair at the power inlet as standard;
  route serial/WebREPL access out of the enclosure; pick the display connector for
  the panel actually intended.

## Timeline of record (2026-09-29, local)

- **10:02 / 10:27** — 63c4259 (sync push), ad2724a (staged boot) committed
- **morning** — deterministic ValueError handshake crash reproduced ×2 backends
- **11:42–14:13** — proven-tree session on red board, 2.5 h stable (owner reports)
- **14:13:35** — owner reboot → crash-loop (14:13:51, 14:17:02, 14:18:57, 14:32:24), deaths at scattered init points, silent to Loki after boot-complete
- **16:52** — 63c4259 pushed to red board over serial (Errno-1 sandbox discovery)
- **17:04:31 / 17:05:05** — red board boots the retained tree; registration OK; flush dies +~40 s; RE deaths ×3 (owner: every time)
- **17:18:44** — green board first boot in chassis: legible [CRASH] traceback (stale ILI9488 dm, `display_baudrate` kwarg) — observability working
- **17:24** — 63c4259 pushed to green board
- **17:26:24** — first clean chassis boot on green board (registration 60843cbe)
- **17:34–17:36** — BE reboot ridden cleanly; volume 47; NFC al-505 read
- **17:41–17:42** — alive and streaming; soak continues
- **tag `proven-klangmeister-20260929`** created; `config_files/S3/config.json` preserved