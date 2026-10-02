#!/bin/sh
# -----------------------------------------------------------------------------
# Build Klipper MCU firmware for the Mingda Magician Max (GD32F407VET6)
#
#   STM32F407 | 64KiB bootloader (0x08010000) | 8 MHz crystal
#
# Usage:  ./build_klipper_fw.sh [usart3|usb|usba]
#   usart3 (default) : Serial on USART3 PB11/PB10  -> Pi GPIO UART (/dev/serial0)
#   usb              : USB device on PB14/PB15     -> printer USB-C
#   usba             : USB device on PA11/PA12     -> printer USB-A
#
# The USB-C/USB-A options need a small patch (upstream Klipper only allows USB on
# PB14/PB15 for STM32H743/H750); the patch is applied automatically and is inert
# for the usart3 build.
#
# Run on macOS or Linux. Result: firmware.bin next to this script.
# -----------------------------------------------------------------------------
set -e
cd "$(dirname "$0")"

KLIPPER_DIR="${KLIPPER_DIR:-../klipper}"
CONFIG_TARGET="firmware.bin"

# --- Interface selection -----------------------------------------------------
IFACE="${1:-usart3}"
case "$IFACE" in
  usart3) IFACE_CFG="CONFIG_STM32_SERIAL_USART3=y" ;;
  usb|usbc) IFACE_CFG="CONFIG_STM32_USB_PB14_PB15=y" ;;
  usba) IFACE_CFG="CONFIG_STM32_USB_PA11_PA12=y" ;;
  *) echo "usage: $0 [usart3|usb|usba]" >&2; exit 1 ;;
esac
echo "Building interface: $IFACE ($IFACE_CFG)"

# --- Pick a toolchain --------------------------------------------------------
# Klipper needs a full ARM GNU toolchain *with newlib* (setjmp.h). The bare
# Homebrew "arm-none-eabi-gcc" formula has no newlib, so each candidate is
# actually test-compiled before it is accepted.
try_tc() {
    tc="$1"
    [ -x "${tc}gcc" ] || command -v "${tc}gcc" >/dev/null 2>&1 || return 1
    echo '#include <setjmp.h>
int main(void){return 0;}' | ${tc}gcc -x c - -c -o /tmp/klipper_tc_test.o 2>/dev/null
}

CROSS_PREFIX=""
for cand in "arm-none-eabi-" \
            "$HOME/.platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-"; do
    if try_tc "$cand"; then
        CROSS_PREFIX="$cand"
        echo "Using ARM toolchain: $cand"
        break
    fi
done
if [ -z "$CROSS_PREFIX" ]; then
    echo "ERROR: no usable arm-none-eabi-gcc found (needs newlib). Install one with:"
    echo "  brew install --cask gcc-arm-embedded   # macOS (full toolchain)"
    echo "  sudo apt install gcc-arm-none-eabi     # Debian/Ubuntu"
    exit 1
fi

# --- Clone Klipper if needed -------------------------------------------------
if [ ! -d "$KLIPPER_DIR/.git" ]; then
    echo "Cloning Klipper into $KLIPPER_DIR ..."
    git clone --depth 1 https://github.com/Klipper3d/klipper.git "$KLIPPER_DIR"
fi

# --- Apply the STM32F4 USB_OTG_HS (PB14/PB15) patch --------------------------
python3 - "$KLIPPER_DIR" <<'PYEOF'
import sys, os
kc = os.path.join(sys.argv[1], 'src/stm32/Kconfig')
uc = os.path.join(sys.argv[1], 'src/stm32/usbotg.c')

def patch(path, pairs):
    with open(path) as f:
        s = f.read()
    orig = s
    for old, new in pairs:
        if new in s:
            continue
        if old in s:
            s = s.replace(old, new)
        else:
            sys.stderr.write("WARN: expected pattern not found in %s\n" % path)
    if s != orig:
        with open(path, 'w') as f:
            f.write(s)
        print("patched %s" % path)
    else:
        print("already patched %s" % path)

patch(kc, [(
"""    config STM32_USB_PB14_PB15
        bool "USB (on PB14/PB15)"
        depends on MACH_STM32H743 || MACH_STM32H750
        select USBSERIAL""",
"""    config STM32_USB_PB14_PB15
        bool "USB (on PB14/PB15)"
        depends on MACH_STM32H743 || MACH_STM32H750 || MACH_STM32F4x5
        select USBSERIAL""")])

patch(uc, [(
"""#if IS_OTG_HS
  #define USB_PERIPH_BASE USB_OTG_HS_PERIPH_BASE
  #define OTG_IRQn OTG_HS_IRQn
  #define USBOTGEN RCC_AHB1ENR_USB1OTGHSEN
#else""",
"""#if IS_OTG_HS
  #define USB_PERIPH_BASE USB_OTG_HS_PERIPH_BASE
  #define OTG_IRQn OTG_HS_IRQn
  #if CONFIG_MACH_STM32H7
    #define USBOTGEN RCC_AHB1ENR_USB1OTGHSEN
  #else
    #define USBOTGEN RCC_AHB1ENR_OTGHSEN
  #endif
#else"""),(
"""    SET_BIT(RCC->AHB1ENR, USBOTGEN);
#else
    RCC->AHB2ENR |= RCC_AHB2ENR_OTGFSEN;
#endif""",
"""    SET_BIT(RCC->AHB1ENR, USBOTGEN);
#elif IS_OTG_HS
    // STM32F4 USB_OTG_HS (eg. PB14/PB15 device port) is clocked via AHB1
    SET_BIT(RCC->AHB1ENR, USBOTGEN);
#else
    RCC->AHB2ENR |= RCC_AHB2ENR_OTGFSEN;
#endif""")])
PYEOF

cd "$KLIPPER_DIR"

# --- Write the firmware configuration ---------------------------------------
cat > .config <<EOF
CONFIG_LOW_LEVEL_OPTIONS=y
CONFIG_MACH_STM32=y
CONFIG_MACH_STM32F407=y
CONFIG_STM32_FLASH_START_10000=y
CONFIG_STM32_CLOCK_REF_8M=y
$IFACE_CFG
EOF

# Resolve defaults first (this writes CONFIG_BOARD_DIRECTORY into .config),
# then clean so the out/board symlink is created with the correct target.
make olddefconfig
make clean >/dev/null 2>&1 || true
make CROSS_PREFIX="$CROSS_PREFIX" "CPP=${CROSS_PREFIX}gcc -E"

# --- Copy the result ---------------------------------------------------------
cp out/klipper.bin "../klipper_printer/$CONFIG_TARGET"
echo
echo "Built -> $(cd .. && pwd)/klipper_printer/$CONFIG_TARGET"
echo "Copy it to the SD card root as firmware.bin and power-cycle the printer."
