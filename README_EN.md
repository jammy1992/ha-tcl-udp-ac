# TCL Smart AC (LAN)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/jammy1992/ha-tcl-udp-ac)

LAN-only Home Assistant integration for TCL wall-mounted air conditioners that
use a **BroadLink WiFi module**. No cloud, no account, no root required.

The protocol is plain XML over UDP on port `7777` — everything stays on your
local network.

中文说明：[README.md](README.md)

## Features

| Entity | Type | Capabilities |
| --- | --- | --- |
| `<device name>` | `climate` | Power · 16–31 °C · cool/heat/dry/fan-only/auto · fan speed · vertical & horizontal swing |
| `<device name> display` | `light` | Indoor unit display panel |
| 8 × `switch` | `switch` | ECO / Turbo / Healthy / Self-clean / Stereo wind / Aux heating / Beep / 0.5 °C step |

State is polled locally; entities show `unavailable` when the AC is offline and
recover automatically.

## Supported hardware

- TCL wall-mounted ACs with a BroadLink WiFi module (e.g. `3303-SBSL012001`,
  visible on the module inside the indoor unit)
- Units pairable through the official **TCL Smart AC** app over LAN
- Verified on a 2017 TCL KFR unit, MAC `aa:bb:cc:dd:ee:ff`

## Installation

### HACS

1. HACS → Integrations → `⋮` → **Custom repositories**
2. Category `Integration`, repository
   `https://github.com/jammy1992/ha-tcl-udp-ac`
3. Install **TCL Smart AC (LAN)** and restart Home Assistant

### Manual

Copy `custom_components/tcl_ac` into your Home Assistant config directory and
restart.

## Configuration

Find your device ID (= MAC in lowercase without separators + `_device`):

```bash
python3 custom_components/tcl_ac/protocol.py discover
# TAC_AABBCC  192.168.1.100:7777  id=aabbccddeeff_device
```

Then add the integration via **Settings → Devices & Services → Add Integration**
and search for `TCL`.

## Protocol

The protocol was reverse-engineered from the official app. Full documentation,
field tables and packet captures: [docs/protocol.md](docs/protocol.md).

## Disclaimer

Unofficial integration, not affiliated with TCL or BroadLink. Use at your own
risk. Do not expose UDP port 7777 to untrusted networks — the protocol has no
authentication.

## License

[MIT](LICENSE)
