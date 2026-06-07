# superstrike-hits-linux

Control the **Logitech G PRO X2 SUPERSTRIKE** click-haptics and actuation points
on **Linux**, without G HUB.

The PRO X2 SUPERSTRIKE uses Logitech's **HITS** (Haptic Inductive Trigger System):
inductive analog main switches + a vibration motor that simulates the "click", with
**6 click-haptics levels** and **10 actuation-point levels**. On Windows these are set
in G HUB. On Linux there was **no way to change them** — the mouse is stuck on whatever
the onboard profile last had.

This project reverse-engineers the relevant HID++ feature (**`0x1B0C`**, not present in
libratbag/Solaar) and gives you a small, dependency-free CLI to read and set those values.
As of writing, neither **Solaar** nor **libratbag** supports this device's HITS settings —
this appears to be the first public Linux implementation.

## Features

- Read / set **click-haptics** (1–6) and **actuation point** (1–10), per button.
- Pure Python standard library, talks to `/dev/hidraw` directly. No daemon, no deps.
- **Auto-detects** the receiver's HID++ control interface (FF00 usage page) — no need to
  guess the right `/dev/hidrawN`.
- Optional **persistence**: save your levels and re-apply automatically on connect/boot.

## Requirements

- A Logitech **LIGHTSPEED receiver** (`046d:c54d`) with the SUPERSTRIKE paired.
  (Kernel `hid-logitech-dj` already exposes it; wired/direct should also work.)
- Python 3.

## Install

```bash
git clone https://github.com/<you>/superstrike-hits-linux
cd superstrike-hits-linux
chmod +x superstrike-hits.py

# non-root access (recommended): install the udev rule
sudo cp 42-logitech-superstrike.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
# then re-plug the receiver
```

## Usage

```bash
./superstrike-hits.py --get               # read current per-button settings
./superstrike-hits.py --haptics 4         # set click-haptics to level 4 (both buttons)
./superstrike-hits.py --actuation 7       # set actuation point to level 7
./superstrike-hits.py --haptics 6 --left  # left button only
./superstrike-hits.py --haptics 3 --actuation 8 --save   # set + remember as default
```

### Persistence (re-apply on every connect)

`--save` writes your levels to `~/.config/superstrike-hits.conf`; `--apply` reads and
applies them. Wire `--apply` to a udev/systemd hook so settings survive reboots and the
onboard profile being changed by Windows/G HUB. (See the repo wiki / issues for a sample
systemd unit.)

## How it works

Full reverse-engineering notes and the exact HID++ protocol (feature `0x1B0C`, the
`[button][actuation][x][haptics]` record format, level↔byte mappings, and how it was
captured from G HUB) are in **[PROTOCOL.md](PROTOCOL.md)**.

## Status / TODO

- [ ] Confirm the `x`/rapid-trigger sub-field semantics.
- [ ] Upstream as a libratbag device entry and/or a Solaar feature plugin for `0x1B0C`.
- [ ] Packaged systemd/udev auto-apply.

## Credits

Reverse-engineered by **kazehana** (USBPcap differential capture of G HUB + on-device
HID++ probing). Contributions / upstreaming welcome.

## License

MIT — see [LICENSE](LICENSE).
