# Compiling the Klipper MCU firmware yourself (`make menuconfig`)

Use this if you want to build `firmware.bin` from source instead of using the
prebuilt one. The MCU talks to the Pi over **USART3**, so that is the only
interface needed.

## 0. Prerequisites

- **Klipper source** cloned as a sibling of this folder:
  ```bash
  # from the folder that contains klipper_printer/
  git clone https://github.com/Klipper3d/klipper
  ```
- **ARM GCC toolchain with newlib** (`arm-none-eabi-gcc`):
  - macOS: `brew install --cask gcc-arm-embedded`
  - Debian/Ubuntu/Raspberry Pi OS: `sudo apt install gcc-arm-none-eabi`
  - (Klipper uses `-flto=auto`, so GCC 10+ is recommended.)

## 1. Configure

```bash
cd klipper
make menuconfig
```

Select exactly these values:

| Menu item | Value |
|---|---|
| Enable extra low-level configuration options | **`[*]` yes** |
| Micro-controller Architecture | **STMicroelectronics STM32** |
| Processor model | **STM32F407** |
| Bootloader offset | **64KiB bootloader** |
| Clock Reference | **8 MHz crystal** |
| Communication interface | **Serial (on USART3 PB11/PB10)** |

Quit with `Q`, then `Y` to save.

### Equivalent `.config` (skip the menu)

Paste this into `klipper/.config`:

```
CONFIG_LOW_LEVEL_OPTIONS=y
CONFIG_MACH_STM32=y
CONFIG_MACH_STM32F407=y
CONFIG_STM32_FLASH_START_10000=y
CONFIG_STM32_CLOCK_REF_8M=y
CONFIG_STM32_SERIAL_USART3=y
```

then:

```bash
make olddefconfig
```

## 2. Build

```bash
make
```

Output: **`out/klipper.bin`** → copy it to the SD-card **root** as
**`firmware.bin`** and flash (see the main README).

### macOS note

The default `cpp` shipped by Apple breaks Klipper's `.ld` preprocessing; point
`CPP` at the cross toolchain:

```bash
make CPP="arm-none-eabi-gcc -E"
```

(If `arm-none-eabi-gcc` isn't on `PATH`, add
`CROSS_PREFIX=/path/to/toolchain/bin/arm-none-eabi-`.)

## 3. Verify the build

```bash
grep FLASH_APPLICATION_ADDRESS .config   # must be 0x8010000
xxd -l 8 out/klipper.bin                 # starts with SP 0x2002...., reset 0x0801....
```

- App offset **`0x10000`** matches the stock bootloader (it writes `firmware.bin`
  to `0x08010000`).
- `GD32F407VET6` is register-compatible with `STM32F407` (same peripheral
  addresses, PLL layout and flash wait-state register), which is why we target
  STM32F407.
