# Klipper for the Mingda Magician Max (GD32)

> ## ⚠️ DISCLAIMER — READ BEFORE STARTING
>
> - This modification requires **opening the printer and removing the bottom
>   cover** to access the mainboard, power supply and wiring.
> - There is an **electric shock hazard**. The PSU input is mains voltage, and
>   the PSU/mainboard can hold lethal voltages even after power-off.
>   **Unplug the printer from the wall before working on it**, and never touch
>   the mains/PSU section while powered.
> - Proceed only if you are comfortable with electronics and are careful.
>   **You do this at your own risk** — no warranty, and you can damage the
>   printer or injure yourself.

Minimal guide to replace the stock Marlin firmware with **Klipper**, talking to
a Raspberry Pi over a **serial (UART) link**. Assumes basic electronics skills
(soldering/wiring, DC-DC converters, SD cards).

Tested on: Mingda Magician Max (GD32F407VET6 mainboard) + Raspberry Pi Zero 2 W.

> The stock touchscreen is **not supported** by Klipper — you use Mainsail/Fluidd
> in a browser instead.

## Folder layout

This repository is only `klipper_printer/`. The Klipper **source is not
included** — only clone it next to this folder if you want to rebuild the
firmware.

```
your-folder/
├── klipper_printer/     <- this repo (firmware.bin, printer.cfg, macros.cfg, guide)
└── klipper/             <- Klipper source (only needed to rebuild)
```

## 1. Flash the firmware (SD card)

1. Format a microSD card as **FAT32**.
2. Copy `firmware.bin` to the **root** of the card (keep the name `firmware.bin`).
3. Printer **off** → insert the card → power **on**. The screen shows a flashing
   percentage.
4. When it finishes, **remove the card** and power-cycle the printer.

## 2. Wiring

You need a **DC-DC step-down module** (24 V → 5 V, **≥ 2 A**) to power the Pi
from the printer's PSU, and **3 jumper wires** for the UART.

### 2.1 Power the Pi

```
      Printer PSU (24 V)
      ┌─────────────────┐
      │   +24V     GND  │
      └───┬──────────┬──┘
          │          │
   ┌──────┴──────────┴──────┐
   │   DC-DC step-down      │
   │      24V → 5V          │
   │       (≥ 2 A)          │
   └──────┬──────────┬──────┘
        +5V│          │GND
           │          │
   ┌───────┴──────────┴────────────────┐
   │          Raspberry Pi             │
   │      5V in             GND        │
   └───────────────────────────────────┘
```

### 2.2 UART link (printer → Pi)

The printer has an 8-pin **"W1" / ESP8266 socket** carrying **USART3**:

```
        W1 socket (front view)
        ┌───────────────┐
        │ 1 TX    2 GND │
        │ 3 EN    4 GPIO2│
        │ 5 RST   6 GPIO0│
        │ 7 3V3   8 RX  │
        └───────────────┘

   Pi GPIO14 (TX, pin 8)   ───────────────►  pin 8  (RX)
   Pi GPIO15 (RX, pin 10)  ◄───────────────  pin 1  (TX)
   Pi GND    (GND, pin 6)  ────────────────  pin 2  (GND)
```

- **Do not connect** pin 7 (3.3 V) or any other pin.
- 3.3 V logic; grounds are already common through the step-down.
- Remove any ESP8266 module from the socket.

## 3. Raspberry Pi software

1. Install Raspberry Pi OS and use **KIAUH** to install
   **Klipper + Moonraker + Mainsail**.
2. Enable the Pi's UART. In `/boot/firmware/config.txt` add:
   ```
   enable_uart=1
   dtoverlay=disable-bt
   ```
   then:
   ```
   sudo systemctl disable hciuart 2>/dev/null
   sudo reboot
   ```
   Verify: `ls -l /dev/serial0` → should point to `ttyAMA0`.
3. Copy `printer.cfg` and `macros.cfg` into `~/printer_data/config/`.

## 4. First start

Power the printer first, then the Pi. Open Mainsail — the MCU should come
online (serial `/dev/serial0`, 250000 baud). If it does, you're done with setup.

## 5. Calibrate (do this once)

```
G28                       ; home all axes
PROBE_CALIBRATE           ; lower with TESTZ, then ACCEPT + SAVE_CONFIG
PID_CALIBRATE HEATER=extruder TARGET=200
SAVE_CONFIG
PID_CALIBRATE HEATER=heater_bed TARGET=60
SAVE_CONFIG
BED_MESH_CALIBRATE        ; saves the mesh automatically
```
`PROBE_CALIBRATE` starts the nozzle ~5 mm above the bed: use the Mainsail
`TESTZ` buttons (or `TESTZ Z=-0.1`) until a sheet of paper has slight drag, then
`ACCEPT` and `SAVE_CONFIG`.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| No `/dev/serial0` | `enable_uart=1` + `dtoverlay=disable-bt` not applied; reboot |
| "Unable to connect" | TX/RX swapped, or ESP8266 module still in the socket |
| `TMC ... ShortToSupply` | Z/Z1 already run spreadCycle; check the motor connector |
| Screen stays blank | Expected — stock TFT unsupported |
| First layer too high/low | Re-run `PROBE_CALIBRATE`, or `SET_GCODE_OFFSET Z=...` |

## Rebuild the firmware (optional)

Clone the Klipper source **as a sibling of this folder** (so it lands in
`../klipper`):

```
# from the parent folder that contains klipper_printer/
git clone https://github.com/Klipper3d/klipper
```

Then build:

```
cd klipper_printer
./build_klipper_fw.sh usart3
```

The script automatically patches Klipper for this board and produces
`firmware.bin`. It needs an ARM GCC toolchain (with newlib); on macOS the
PlatformIO toolchain or `gcc-arm-embedded` works. `usart3` is this project's
build; `usb` / `usba` are alternatives.

Manual build? See **[menuconfig.md](menuconfig.md)** for the full `make menuconfig`
settings, the equivalent `.config`, macOS notes, and the USB patches.
