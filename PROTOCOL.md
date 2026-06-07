# Logitech G PRO X2 SUPERSTRIKE — Linux control of HITS haptics / actuation

> 中文原版见 [PROTOCOL.zh.md](PROTOCOL.zh.md).

Reverse-engineering date: 2026-06-07. Method: captured G HUB's USB/HID++ traffic on
Windows with USBPcap and diffed it, then actively queried the real device with hidapi and
verified with a read-modify-write closed loop. **The protocol is confirmed and the device
can be driven independently of G HUB.**

---

## 0. TL;DR (just want to use it)

```bash
chmod +x superstrike-hits.py
sudo ./superstrike-hits.py --get            # read current left/right settings
sudo ./superstrike-hits.py --haptics 4      # click-haptics -> level 4
sudo ./superstrike-hits.py --actuation 7    # actuation point -> level 7
sudo ./superstrike-hits.py --haptics 6 --left   # left button only
sudo ./superstrike-hits.py --dump-features  # print the whole feature table (self-check)
```

To run without root: copy `42-logitech-superstrike.rules` to `/etc/udev/rules.d/`, then
`sudo udevadm control --reload && sudo udevadm trigger`, and re-plug the receiver.

---

## 1. Mechanism & link

- The SUPERSTRIKE's left/right main keys are **inductive analog switches** (coil + inductive
  sensor) plus a vibration motor that simulates the "click" — there is no mechanical switch.
  Adjustable: **actuation point (10 levels)**, **click-haptics (6 levels)**, rapid trigger.
- Control goes over **HID++ 2.0** (protocol version 4.2), through the **LIGHTSPEED receiver
  `046d:c54d`**.
- Packets: **HID++ long report, report ID `0x11`**, interface MI_02, vendor usage page `0xFF00`.
- Device index: wireless = `1` (single paired device); wired/direct = `0xFF`. The tool
  auto-detects this.
- The Linux kernel's `hid-logitech-dj` already supports this receiver, so the `/dev/hidraw`
  channel is available out of the box.

HID++ frame structure (request):
```
[report_id] [device_index] [feature_index] [(function<<4)|software_id] [params...]
   0x11          0x01          see below          low 4 bits swid = any        19 bytes
```
Responses use the same structure. Error response:
`[0x10][devIdx][0xFF][featIdx][fn|swid][error_code][..]`.

---

## 2. Core: analog/HITS feature = `0x1B0C`

This is a **brand-new vendor feature ID** (not in the libratbag/Solaar database).
**Do not hard-code its index** — it can differ across firmware/pairings. Resolve it at
runtime via IRoot:

```
IRoot.getFeature(0x1B0C)  ->  returns its feature_index  (= 0x0c on this unit)
```

### Functions of feature 0x1B0C

| function | purpose | request params | response |
|---|---|---|---|
| 0 | getInfo | (none) | `[00, nButtons=03, actuation_max=0x28, haptics_max=0x14, ?=0x14, 01]` |
| 1 | **set (per button)** | `[btn][actuation][x][haptics]` | echoes the same 4 bytes; out-of-range -> ERR_INVALID_ARGUMENT (0x02) |
| 2 | **get (per button)** | `[btn]` | `[btn][actuation][x][haptics]` |
| 3,4 | other (not investigated) | — | — |
| 5,6,7 | do not exist | — | ERR_INVALID_FUNCTION_ID (0x07) |

### Per-button record: 4 bytes `[button][actuation][x][haptics]`

| field | meaning | values |
|---|---|---|
| `button` | main key | `0x00`=left, `0x01`=right (echoed in the response; maps to 80/81 in G HUB's settings.db) |
| `actuation` | actuation point | UI level **1–10** -> byte = **level × 4** (`0x04`–`0x28`) |
| `x` | fixed sub-field (suspected rapid trigger) | observed `0x09`; **read it and write it back unchanged** |
| `haptics` | **click-haptics** | UI level **1–6** -> byte = **(level−1) × 4** (`0x00`–`0x14`) |

### Level ↔ byte mapping (cross-verified)

```
actuation:  L1=0x04 L2=0x08 L3=0x0c L4=0x10 L5=0x14
            L6=0x18 L7=0x1c L8=0x20 L9=0x24 L10=0x28      (= level × 4)
            ^ capture evidence: G HUB set to 1/3/5/8/10 -> 04/0c/14/20/28

haptics:    L1=0x00 L2=0x04 L3=0x08 L4=0x0c L5=0x10 L6=0x14   (= (level−1) × 4)
            ^ settings.db globalClickHaptics=3 <-> device reads 0x08; writing 0x18 (over the
              0x14 max) is rejected
```

### Correct way to change one parameter (what the tool does)

function 1 writes the whole "actuation + x + haptics" record at once, so **to change haptics
you must preserve actuation and x**:

```
1) fn2 read   [btn][act][x][hap]
2) replace only the byte you want to change
3) fn1 write back the whole record
4) do it once per main key (btn=0 and btn=1)
```

Example — set left-button haptics to level 4 (byte 0x0c), keeping the rest unchanged. The
long report actually sent:
```
11 01 <fidx> 1<swid> 00 <current act> <current x> 0c 00 00 ... (padded to 20 bytes)
Real sample captured from G HUB (left key, actuation=0x0c, x=09, haptics=0x14):
11 01 0c     1d       00  0c       09        14  00 00 ...
```

---

## 3. Full feature table (this unit, 35 features)

See `feature-table.txt`. Notable ones:
```
idx 0x09 ID 0x2202  adjust DPI
idx 0x0c ID 0x1B0C  <- analog/HITS (the subject of this doc)
idx 0x0d ID 0x8061  extended report rate (<8K polling)
idx 0x0e ID 0x8100  onboard config
idx 0x0f ID 0x8110  ...
idx 0x17 ID 0x1830  power / wake
```

---

## 4. Tool notes: `superstrike-hits.py`

- Pure Python standard library, talks to `/dev/hidraw` directly, no third-party deps.
- On start: scans `/dev/hidraw*` -> finds the VID `046d` device -> pings device indices 1–6/0xFF
  -> for the one that replies, uses `IRoot.getFeature(0x1B0C)` to resolve the analog feature
  index. Fully automatic.
- Subcommands: `--get` / `--haptics 1-6` / `--actuation 1-10` / `--left` / `--right` /
  `--raw-haptics 0xNN` / `--raw-actuation 0xNN` / `--dump-features` / `--device /dev/hidrawN`.
- Without `--left/--right`, it sets both main keys.

Troubleshooting:
- Device not found -> is the mouse powered on and connected? running as root / udev rule installed?
- Wired mode or different receiver -> the tool auto-tries 0xFF and other indices; if it still
  fails, pass `--device` manually.
- Run `--dump-features` first and confirm the output contains `0x1B0C  <- analog/HITS` before
  setting anything.

---

## 5. How it was reverse-engineered (for reference)

1. **Locate device/link**: receiver `046d:c54d` on USBPcap5, USB address 12; HID++ on endpoint
   0x83 (IN).
2. **G HUB config model**: read `%LOCALAPPDATA%\LGHUB\settings.db` (SQLite) to get
   `actuationPointValues/clickHapticsValues/global*Value` and keys 80/81 — i.e. which
   parameters exist and their ranges.
3. **Differential capture**: capture the receiver with USBPcap while sweeping haptics 1->6 and
   actuation 1/3/5/8/10 in G HUB, and diff the changed bytes. The request is a **control
   transfer SET_REPORT** (`bmRequestType=0x21 bRequest=9 wValue=0x0211`); the data stage is only
   visible with `tshark -x`.
4. **Get the feature ID**: G HUB caches the feature table and does not re-enumerate on
   reconnect, so **stop G HUB** (it holds HID++; otherwise you get ERR_BUSY 0x08) and enumerate
   the 35 features directly with hidapi via `IFeatureSet`, locating 0x0c -> `0x1B0C`.
5. **On-device closed loop**: fn2 read -> fn1 change haptics -> fn2 read back to confirm ->
   restore. Writing the out-of-range 0x18 is rejected by the device (confirming the 0x14 max).

The Windows-side scripts are under `windows逆向脚本备查/` and the raw captures under
`抓包原始数据/` (`hidpp_haptics.pcap` = differential capture, `hidpp_enum3.pcap` = enumeration
capture). Open the pcaps in Wireshark; for HID++ requests look at the control-transfer data
stage, and for responses look at endpoint 0x83's `usb.capdata`.

---

## 6. Possible follow-ups (not done)

- Confirm whether the `x` field (0x09) is rapid trigger (`globalRapidTriggerValue` in settings.db).
- Semantics of fn3/fn4 (the capture shows fn3 sending `01 3c`/`00 3c`, suspected global rapid
  trigger or an app toggle).
- systemd/udev auto-apply of the config on boot.
- Package it as a libratbag/Solaar feature plugin for `0x1B0C` and submit upstream.
