#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""superstrike-hits (hardened) — Logitech G PRO X2 SUPERSTRIKE click-haptics &
actuation control on Linux, no G HUB.

Based on kazehana's reverse-engineered protocol (feature 0x1B0C).
Hardened vs original:
  * auto-locates the receiver's HID++ control interface (FF00 usage page),
    skips other hidraw nodes that reject writes (no more BrokenPipe crash);
    --device no longer needed.
  * config persistence: --save / --apply  (+ ~/.config/superstrike-hits.conf)
    so a udev/systemd hook can re-apply settings on every connect.
"""
import os, sys, glob, time, select, argparse, json

VID, PID_RECEIVER = 0x046D, 0xC54D
HIDPP_SHORT, HIDPP_LONG = 0x10, 0x11
SHORT_LEN, LONG_LEN = 7, 20
SWID = 0x05
ANALOG_FEATURE_ID = 0x1B0C
FEAT_IROOT, FEAT_FEATURESET = 0x00, 0x01
CONF = os.path.expanduser("~/.config/superstrike-hits.conf")

def act_level_to_raw(l): return l * 4
def act_raw_to_level(b): return b // 4
def hap_level_to_raw(l): return (l - 1) * 4
def hap_raw_to_level(b): return b // 4 + 1
ACT_MIN, ACT_MAX = 1, 10
HAP_MIN, HAP_MAX = 1, 6

class HidppError(Exception): pass

def sysfs_hid_id(name):
    try:
        with open(f"/sys/class/hidraw/{name}/device/uevent") as f:
            for line in f:
                if line.startswith("HID_ID="):
                    bus, vid, pid = line.strip().split("=")[1].split(":")
                    return int(bus, 16), int(vid, 16), int(pid, 16)
    except OSError:
        pass
    return None

def has_ff00(name):
    """True if this hidraw's report descriptor declares vendor usage page 0xFF00 (HID++)."""
    try:
        with open(f"/sys/class/hidraw/{name}/device/report_descriptor", "rb") as f:
            rd = f.read()
        return b"\x06\x00\xff" in rd
    except OSError:
        return False

class Device:
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    def close(self):
        try: os.close(self.fd)
        except OSError: pass
    def _drain(self):
        while True:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r: break
            try: os.read(self.fd, 64)
            except (BlockingIOError, OSError): break
    def request(self, dev_idx, feat_idx, fn, params=b"", long=True,
                want_byte0=None, timeout=0.6, tries=4):
        rid = HIDPP_LONG if long else HIDPP_SHORT
        n = LONG_LEN if long else SHORT_LEN
        pkt = bytes([rid, dev_idx, feat_idx, (fn << 4) | SWID]) + bytes(params)
        pkt = pkt + b"\x00" * (n - len(pkt))
        last_err = None
        for _ in range(tries):
            self._drain()
            try:
                os.write(self.fd, pkt)
            except OSError as e:
                # wrong interface (e.g. BrokenPipe): treat as no-such-device here
                raise HidppError(f"write failed on {self.path}: {e}")
            end = time.time() + timeout
            while time.time() < end:
                r, _, _ = select.select([self.fd], [], [], max(0, end - time.time()))
                if not r: continue
                try: data = os.read(self.fd, 64)
                except (BlockingIOError, OSError): continue
                if len(data) < 6 or data[1] != dev_idx: continue
                if (data[3] & 0x0F) != SWID: continue
                if data[2] == 0xFF and (data[4] & 0x0F) == SWID:
                    last_err = data[5]; break
                if data[2] == feat_idx and (data[3] >> 4) == fn:
                    body = data[4:]
                    if want_byte0 is None or (body and body[0] == want_byte0):
                        return body
            time.sleep(0.04)
        raise HidppError(f"no/err response feat=0x{feat_idx:02x} fn={fn} "
                         f"(last hid++ error={last_err})")
    def ping(self, dev_idx):
        body = self.request(dev_idx, FEAT_IROOT, 1, bytes([0, 0, 0x5A]))
        return body[0], body[1]
    def get_feature_index(self, dev_idx, fid):
        body = self.request(dev_idx, FEAT_IROOT, 0, bytes([fid >> 8, fid & 0xFF]))
        return body[0]

def find_device(explicit=None):
    if explicit:
        cands = [explicit]
    else:
        # HID++ interface first: VID 046d AND FF00 usage page
        all_hr = sorted(glob.glob("/dev/hidraw*"))
        pref = [p for p in all_hr if (sysfs_hid_id(os.path.basename(p)) or (0,0,0))[1] == VID
                and has_ff00(os.path.basename(p))]
        rest = [p for p in all_hr if p not in pref]
        cands = pref + rest
    for path in cands:
        name = os.path.basename(path)
        if not explicit:
            hid = sysfs_hid_id(name)
            if not hid or hid[1] != VID:
                continue
        try:
            dev = Device(path)
        except OSError:
            continue
        for dev_idx in (1, 2, 3, 4, 5, 6, 0xFF):
            try:
                dev.ping(dev_idx)
                fidx = dev.get_feature_index(dev_idx, ANALOG_FEATURE_ID)
            except HidppError:
                continue
            if fidx:
                return dev, dev_idx, fidx
        dev.close()
    raise SystemExit("ERROR: 没找到 SUPERSTRIKE 的 HID++(0x1B0C)设备。鼠标开机连上了吗?有 root/udev 权限吗?")

def read_btn(dev, dev_idx, fidx, btn):
    rec = dev.request(dev_idx, fidx, 2, bytes([btn]), want_byte0=btn)
    return bytearray(rec[:4])  # [btn, act, x, hap]

def set_button(dev, dev_idx, fidx, btn, act_raw=None, hap_raw=None):
    rec = read_btn(dev, dev_idx, fidx, btn)
    if act_raw is not None: rec[1] = act_raw
    if hap_raw is not None: rec[3] = hap_raw
    res = dev.request(dev_idx, fidx, 1, bytes(rec), want_byte0=btn)
    return res[:4]

def cmd_get(dev, dev_idx, fidx):
    for btn, label in ((0, "左键"), (1, "右键")):
        b = read_btn(dev, dev_idx, fidx, btn)
        print(f"  {label}: 行程 {act_raw_to_level(b[1])}档(0x{b[1]:02x})  震感 {hap_raw_to_level(b[3])}档(0x{b[3]:02x})  [x=0x{b[2]:02x}]")

def main():
    ap = argparse.ArgumentParser(description="G PRO X2 SUPERSTRIKE 震感/触发行程 (Linux)")
    ap.add_argument("--get", action="store_true")
    ap.add_argument("--haptics", type=int, metavar="1-6")
    ap.add_argument("--actuation", type=int, metavar="1-10")
    ap.add_argument("--left", action="store_true")
    ap.add_argument("--right", action="store_true")
    ap.add_argument("--device")
    ap.add_argument("--save", action="store_true", help="把当前(或本次设定的)配置写入 ~/.config/superstrike-hits.conf")
    ap.add_argument("--apply", action="store_true", help="读取配置文件并应用(给开机/连接钩子用)")
    args = ap.parse_args()

    dev, dev_idx, fidx = find_device(args.device)
    try:
        if args.apply:
            if not os.path.exists(CONF):
                print("没有配置文件,跳过"); return
            cfg = json.load(open(CONF))
            for btn in (0, 1):
                key = ("left", "right")[btn]
                c = cfg.get(key, {})
                set_button(dev, dev_idx, fidx, btn,
                           act_raw=act_level_to_raw(c["actuation"]) if "actuation" in c else None,
                           hap_raw=hap_level_to_raw(c["haptics"]) if "haptics" in c else None)
            print("已应用配置:", cfg); return

        act_raw = act_level_to_raw(args.actuation) if args.actuation else None
        hap_raw = hap_level_to_raw(args.haptics) if args.haptics else None
        if args.actuation and not ACT_MIN <= args.actuation <= ACT_MAX:
            raise SystemExit("actuation 必须 1..10")
        if args.haptics and not HAP_MIN <= args.haptics <= HAP_MAX:
            raise SystemExit("haptics 必须 1..6")

        if act_raw is None and hap_raw is None and not args.save:
            cmd_get(dev, dev_idx, fidx); return

        buttons = [0,1] if not (args.left or args.right) else ([0] if args.left else []) + ([1] if args.right else [])
        for btn in buttons:
            out = set_button(dev, dev_idx, fidx, btn, act_raw, hap_raw)
            print(f"  设 {('左键','右键')[btn]}: 行程 {act_raw_to_level(out[1])}档  震感 {hap_raw_to_level(out[3])}档  OK")

        if args.save:
            cfg = {}
            for btn in (0, 1):
                b = read_btn(dev, dev_idx, fidx, btn)
                cfg[("left","right")[btn]] = {"actuation": act_raw_to_level(b[1]), "haptics": hap_raw_to_level(b[3])}
            json.dump(cfg, open(CONF, "w"), indent=2)
            print("已保存配置 ->", CONF, cfg)
    finally:
        dev.close()

if __name__ == "__main__":
    main()
