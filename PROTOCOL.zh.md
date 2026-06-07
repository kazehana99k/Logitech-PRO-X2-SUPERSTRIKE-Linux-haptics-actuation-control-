# Logitech G PRO X2 SUPERSTRIKE —— HITS 力反馈/触发行程 Linux 控制

逆向日期:2026-06-07。方法:Windows 上用 USBPcap 抓 G HUB 的 USB/HID++ 流量做差分,
再用 hidapi 在真机上主动查询 + 读改写闭环验证。**协议已确认、真机可独立驱动。**

---

## 0. TL;DR(只想用)

```bash
chmod +x superstrike-hits.py
sudo ./superstrike-hits.py --get            # 读当前左右键设置
sudo ./superstrike-hits.py --haptics 4      # 点击力反馈(震感)设到 4 档
sudo ./superstrike-hits.py --actuation 7    # 触发行程设到 7 档
sudo ./superstrike-hits.py --haptics 6 --left   # 只设左键
sudo ./superstrike-hits.py --dump-features  # 打印整张 feature 表自检
```

免 root:把 `42-logitech-superstrike.rules` 拷到 `/etc/udev/rules.d/`,然后
`sudo udevadm control --reload && sudo udevadm trigger`,重新插一下接收器。

---

## 1. 机制 & 链路

- SUPERSTRIKE 左右主键是**感应式模拟开关**(线圈+电感传感器)+ 震动马达模拟"咔哒"手感,
  没有机械微动。可调:**触发行程 actuation(10 档)**、**点击力反馈 haptics(6 档)**、rapid trigger。
- 控制走 **HID++ 2.0**(协议版本 4.2),经 **LIGHTSPEED 接收器 `046d:c54d`** 转发。
- 报文:**HID++ 长报文 report ID `0x11`**,接口 MI_02,vendor usage page `0xFF00`。
- 设备索引:无线 = `1`(单设备配对);有线/直连 = `0xFF`。工具会自动探测。
- Linux 内核 `hid-logitech-dj` 已支持该接收器,`/dev/hidraw` 通道现成可用。

HID++ 帧结构(发送):
```
[report_id] [device_index] [feature_index] [(function<<4)|software_id] [params...]
   0x11          0x01          见下文              低 4 位 swid 任意           19 字节
```
响应同结构;错误响应:`[0x10][devIdx][0xFF][featIdx][fn|swid][错误码][..]`。

---

## 2. 核心:analog/HITS feature = `0x1B0C`

这是个**全新的 vendor feature ID**(libratbag/Solaar 数据库里没有)。
**不要写死它的索引**——不同固件/配对下索引可能变。运行时用 IRoot 解析:

```
IRoot.getFeature(0x1B0C)  ->  返回它的 feature_index  (本机实测 = 0x0c)
```

### feature 0x1B0C 的函数

| function | 作用 | 请求参数 | 响应 |
|---|---|---|---|
| 0 | getInfo | (无) | `[00, 按键数=03, actuation上限=0x28, haptics上限=0x14, ?=0x14, 01]` |
| 1 | **设置(每键)** | `[btn][actuation][x][haptics]` | 回显同样 4 字节;越界报 ERR_INVALID_ARGUMENT(0x02) |
| 2 | **读取(每键)** | `[btn]` | `[btn][actuation][x][haptics]` |
| 3,4 | 其它(未深究) | — | — |
| 5,6,7 | 不存在 | — | ERR_INVALID_FUNCTION_ID(0x07) |

### 每键记录:4 字节 `[button][actuation][x][haptics]`

| 字段 | 含义 | 取值 |
|---|---|---|
| `button` | 主键 | `0x00`=左,`0x01`=右(响应里会回显,和 G HUB settings.db 的 80/81 对应) |
| `actuation` | 触发行程 | UI 档 **1–10** → 字节 = **档 × 4**(`0x04`–`0x28`) |
| `x` | 固定子字段(疑似 rapid trigger) | 实测 `0x09`,**读出来原样写回,别动** |
| `haptics` | **点击力反馈(震感)** | UI 档 **1–6** → 字节 = **(档−1) × 4**(`0x00`–`0x14`) |

### 档位 ↔ 字节映射(已交叉验证)

```
actuation:  档1=0x04 档2=0x08 档3=0x0c 档4=0x10 档5=0x14
            档6=0x18 档7=0x1c 档8=0x20 档9=0x24 档10=0x28      （= 档×4）
            ↑ 抓包实证:G HUB 设 1/3/5/8/10 → 04/0c/14/20/28

haptics:    档1=0x00 档2=0x04 档3=0x08 档4=0x0c 档5=0x10 档6=0x14   （=(档−1)×4)
            ↑ settings.db globalClickHaptics=3 ↔ 真机读到 0x08;写 0x18(超上限0x14)被拒
```

### 改某个参数的正确做法(工具就是这么做的)

function 1 一次写"行程+x+haptics"整条记录,所以**改 haptics 必须保留 actuation 和 x**:

```
1) fn2 读出 [btn][act][x][hap]
2) 只替换要改的字节
3) fn1 写回整条记录
4) 左右键各做一次(btn=0、btn=1)
```

例:把左键 haptics 设到 4 档(字节 0x0c),保持其余不变,实际发出的长报文:
```
11 01 <fidx> 1<swid> 00 <当前act> <当前x> 0c 00 00 ... (补齐 20 字节)
G HUB 抓到的真实样例(左键 actuation=0x0c, x=09, haptics=0x14):
11 01 0c     1d       00  0c       09        14  00 00 ...
```

---

## 3. 完整 feature 表(本机,35 个)

见 `feature-table.txt`。关键几个:
```
idx 0x09 ID 0x2202  调节 DPI
idx 0x0c ID 0x1B0C  ← analog/HITS(本文主角)
idx 0x0d ID 0x8061  扩展报告率(<8K 轮询)
idx 0x0e ID 0x8100  板载配置
idx 0x0f ID 0x8110  ...
idx 0x17 ID 0x1830  电源/唤醒
```

---

## 4. 工具说明 `superstrike-hits.py`

- 纯 Python 标准库,直接 `/dev/hidraw` 收发,无第三方依赖。
- 启动时:扫 `/dev/hidraw*` → 找 VID `046d` 的设备 → 逐个 ping 设备索引 1–6/0xFF →
  对能 ping 通的用 `IRoot.getFeature(0x1B0C)` 拿到 analog feature 索引。全自动。
- 子命令:`--get` / `--haptics 1-6` / `--actuation 1-10` / `--left` / `--right` /
  `--raw-haptics 0xNN` / `--raw-actuation 0xNN` / `--dump-features` / `--device /dev/hidrawN` / `--help-udev`。
- 不带 `--left/--right` 时默认左右键一起设。

排错:
- 找不到设备 → 鼠标是否开机连上?是否 root / 装了 udev 规则?
- 有线模式或换了接收器 → 工具会自动试 0xFF 和别的索引;真不行用 `--device` 手动指定。
- 先跑 `--dump-features`,确认输出里有 `0x1B0C  <- analog/HITS` 再设置。

---

## 5. 我是怎么逆出来的(方法备查)

1. **设备/链路定位**:`046d:c54d` 接收器在 USBPcap5、USB 地址 12;HID++ 在端点 0x83(IN)。
2. **G HUB 配置模型**:扒 `%LOCALAPPDATA%\LGHUB\settings.db`(SQLite),拿到
   `actuationPointValues/clickHapticsValues/global*Value` 和键 80/81 → 知道有哪些参数、值域。
3. **差分抓包**:USBPcap 抓接收器,在 G HUB 里把 haptics 1→6、actuation 1/3/5/8/10 逐档调,
   差分出变化的字节。请求是**控制传输 SET_REPORT**(`bmRequestType=0x21 bRequest=9 wValue=0x0211`),
   data stage 要用 `tshark -x` 才看得到。
4. **拿 feature ID**:G HUB 缓存 feature 表、重连不重新枚举;于是**停掉 G HUB**(它占着 HID++,
   不停会 ERR_BUSY 0x08),用 hidapi 直接 `IFeatureSet` 枚举 35 个 feature,定位 0x0c→`0x1B0C`。
5. **真机闭环**:fn2 读 → fn1 改 haptics → fn2 回读确认 → 还原。写越界 0x18 被设备拒(确认上限 0x14)。

Windows 端用到的脚本都在 `windows逆向脚本备查/`,原始抓包在 `抓包原始数据/`
(`hidpp_haptics.pcap`=差分抓包,`hidpp_enum3.pcap`=枚举抓包)。
Wireshark 可直接打开 pcap;HID++ 请求看控制传输 data stage,响应看端点 0x83 的 `usb.capdata`。

---

## 6. 后续可做(没做)

- `x` 字段(0x09)逆向确认是否=rapid trigger(settings.db 里 `globalRapidTriggerValue`)。
- fn3/fn4 的语义(抓包里 fn3 发过 `01 3c`/`00 3c`,疑似全局 rapid trigger 或应用开关)。
- systemd/udev 开机自动应用配置。
- 封装成 libratbag/Solaar 的 feature 插件(0x1B0C)提交上游。
