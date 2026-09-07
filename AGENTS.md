# AGENTS.md — JukePlayer ESP32

Ground rules for humans and agents working in this repository. Read this before
changing anything; deviations need an explicit agreement (Tier 3 below).

## What this is

MicroPython (ESP32 / ESP32-S3) companion devices for a jukebox backend:
WebSocket client to the backend player, Home Assistant integration via MQTT,
display backends (I²C SSD1306 OLED, ST7735R TFT, ILI9488 480×320 SPI),
RC522 NFC read/write, rotary encoder, pushbuttons, and buffered UDP syslog.
The backend (separate repo) owns playback; the devices render state and send
commands. The app only imports the hand-vendored fork in `jukeplayer/nanogui/`
(the reference copy in `src/` was removed on 2026-09-07).

## Architecture — the law

1. **Single asyncio loop.** All concurrency is uasyncio tasks. No threads.
2. **No blocking I/O inside coroutines.** Synchronous sockets (umqtt), long
   `sleep_ms` polling loops, or large SPI transfers stall WS pings, button
   handling and display refresh. Anything that waits must `await`.
   (Fixed 2026-09-07: NFC write poll, cover-art HTTP fetch and the MQTT
   broker probe now all yield. Residual known-blocking: umqtt's `connect()`
   itself — bounded in practice by the async reachability pre-check; and the
   one-shot blocking SPI frame pushes (display.show()), same cost class as
   the old behavior.)
3. **GC is traffic-independent.** The telemetry loop collects every 30 s
   (`app.py _telemetry_loop`) — do not remove. The recv-loop collect before
   frame reads stays for fragmentation control before cover blits. Watch the
   `[MEM] ... | GC reclaimed:` line: ≈0–2 KB per tick when idle is healthy;
   a steady climb in that column means a real retention leak.
4. **HardwareFactory + dummy fallbacks.** Every peripheral (OLED, NFC, encoder,
   buttons, LEDs) degrades to a mock from `jukeplayer/mocks/` when disabled or
   failing. TFT display failure halts boot on purpose (a screenless jukebox is
   dead). Dummies must honor the same button-facing API as real managers.
5. **AppState** uses small-int keys from `jukeplayer/core/state_constants.py`
   (memory-cheap). Subscribers are called as `cb(state=changed_delta)`.
   New keys append at the end of the numbering; never recycle indexes.
6. **Shared SPI arbitration.** Display and NFC share one SPI bus: each driver
   re-inits bus speed via a per-transfer `spi_init` callback and deasserts the
   other device's CS first. A new bus user requires this pattern — propose it
   before implementing.
7. **Logging.** One `log` singleton (`jukeplayer/core/logger.py`), console +
   buffered syslog with boot replay. Tag prefixes (`[MEM]`, `[CONNECT]`,
   `[COVER]`, `[MS]`) are load-bearing for device-side verification — keep
   them stable.

## Device-code rules

- Stdlib only in device code. No pip dependencies on the device. Host-side
  tooling under `scripts/` may use CPython packages.
- `machine.Pin` has **no** `.toggle()` on the ESP32 port — use
  `pin.value(not pin.value())`.
- Deployed files need a **soft reset** (Ctrl+D) to take effect; imports are
  cached in the running interpreter.
- Never add a dependency, abstraction, or file without consultation.

## Consultation agreement

| Tier | Scope | Flow |
|---|---|---|
| 1 | Trivial: dead-code removal, comment/log wording, ≤ ~20 lines, no behavior change | Execute, show diff after |
| 2 | Scoped bug fix in an existing path | Short plan in chat → owner says go → implement → diff + deploy + verify |
| 3 | Design: new features, files, patterns; refactors > ~50 lines; config schema changes | Plan mode, always |

Standing invariants:
- Deletion over addition.
- No drive-by fixes: adjacent findings go to the queue, never into the
  current diff.
- Stop and report when surprised.
- Every change ships with its deploy command and the device log line that
  proves it landed ("done" = device log confirms).
- The owner deploys to production (WebREPL) devices; bench deploys may be
  delegated over serial with per-command approval.
- **Local-first**: all code changes are made in this repository, then copied
  to devices. Devices are deploy targets, never edit surfaces — no on-device
  code edits via REPL/WebREPL. REPL-pasted code is diagnostics only; any fix
  it proves must still be written locally and redeployed. Runtime artifacts
  managed by the firmware (webrepl_cfg.py, device-root config.json) are the
  only exception.

## Device inventory

| Device | Board | Config variant | Display | Access |
|---|---|---|---|---|
| Klangmeister | ESP32-S3, Octal SPIRAM | `config_files/S3/config.json` | ILI9488 480×320 SPI | WebREPL (mounted in radio chassis, no serial) |
| Testbench v2 | ESP32-S3, Octal SPIRAM, **native USB** (`usbmodem*` — no DTR/RTS auto-reset circuit; restart via mpremote soft-reset, a hard reset briefly drops the port) | device-root `config.json` (client "ESP32_S3 Testbench v2", MQTT **on** since 2026-09-07 for functional testing, console+syslog logging on) | ST7735R SPI TFT (separate `hardware.tft` section — the `oled` flag being off does not mean screenless) | Serial `/dev/cu.usbmodem1101` (path changes per session — re-check `ls /dev/cu.*`); custom preview build |

Firmware: MicroPython v1.28.0-preview (custom build), **DEV mode** = app runs
from the filesystem. `toggle_mode.py` renames `jukeplayer/` ↔ `_jukeplayer/`
to switch DEV ↔ PROD (frozen into firmware). `main.py` imports
`jukeplayer.app` — so device deploys target `/jukeplayer/...` paths, never a
root-level `app.py`.

## Deploy flows

### WebREPL (chassis device)
```
python3 webrepl_cli.py -p <password> <local-file> <device-ip>:<device-path>
```
- `webrepl_cli.py` from github.com/micropython/webrepl (port 8266).
- Example: `... jukeplayer/app.py 192.168.68.x:/jukeplayer/app.py`
- Copying while the app runs is safe; soft reset afterwards to load.

### Serial (dev bench)
```
python3 -m mpremote connect /dev/cu.usbmodemXXXX cp <local> :<device-path>
python3 -m mpremote connect /dev/cu.usbmodemXXXX reset
```
- One-shot commands only; the port is exclusive (close Thonny/screen first).
- **Port custody**: the agent holds the port while a serial session/job is
  active; the owner should not attach Thonny/screen during that window —
  attaching takes the port and interrupts the running app. The app restarts
  autonomously after any reset (boot.py → main.py) and does not need a held
  serial connection; a held connection is only for observing output.

### Bench quirks (learned 2026-09-07 — don't rediscover these)

- **Stuck raw REPL**: an interrupted tool session (Thonny) can leave the device
  in raw paste mode, where **`main.py` does not auto-run** after a soft reset —
  symptom: boot.py runs, console shows `raw REPL; CTRL-B to exit`, app never
  starts. Recovery: send Ctrl-B (exit raw REPL), then a soft reset.
- **USB-CDC console only transmits with DTR asserted** — passive pyserial
  reads with `dtr=False` see nothing; always open with `dtr=True` (safe: the
  board has no DTR/RTS auto-reset circuit on native USB).
- **Power sensitivity**: a sagging USB supply manifests as USB-CDC drops
  (host sees Errno 6 "Device not configured") *while the app keeps running*,
  and occasionally transient flash-read ImportErrors for modules that exist.
  It bites hardest when radio TX (WS handshake), display SPI and backlight
  coincide. The bench needs a solid 5V supply (verified: fails on a marginal
  Mac port, runs on a phone charger).
- The RTS/DTR "reset pulse" trick for USB-UART boards does nothing here.
- **MicroPython file writes**: always `close()` (or a named handle) before
  re-reading a just-written file — MicroPython does not refcount-flush file
  objects, so `json.dump(c, open(p,'w'))` can leave data unflushed when the
  same statement re-reads the file.

## Config deployment

`boot.py` loads `config.json` from the **device root**.

- **Tracked template**: `config_files/example.config.json` — the Klangmeister
  configuration with secrets replaced by `<placeholders>`; copy it and fill in
  secrets out-of-band (never commit real secrets).
- **Preserved real configs** (on disk, gitignored, never committed):
  `config_files/testbench/config.json` and `config_files/S3/config.json`.
  Refresh them from the device if they drift (webrepl GET for Klangmeister,
  `mpremote cp :config.json <dest>` for the bench).
- Both patterns are gitignored (`config.json`, `config_files/**/config.json`),
  as are the device artifacts `webrepl_cfg.py` and `*.mpy`.
- Historical commits still contain old plaintext credentials — treat as
  compromised; rotation is pending.

For a full device rebuild (e.g. after a filesystem wipe): flash the custom
firmware, copy the preserved config to the device root as `config.json`,
deploy the `jukeplayer/` tree, then soft reset.

## Verification recipes

- `python3 scripts/check.py` — syntax gate + display/button contract check.
- Deploy fingerprint: confirm the exact new log line appears in syslog after
  the reset (e.g. `[MEM] ... | GC reclaimed: N KB`).
- Syslog is ground truth for device health; the `[MEM]` series shows memory
  behavior, `[CONNECT]`/`[RECONNECT]` show link state.