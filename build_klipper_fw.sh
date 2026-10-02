#!/bin/sh
# -----------------------------------------------------------------------------
# Build Klipper MCU firmware for the Mingda Magician Max (GD32F407VET6)
#
#   STM32F407 | 64KiB bootloader (0x08010000) | 8 MHz crystal
#   Serial on USART3 (PB11/PB10)  ->  Raspberry Pi GPIO UART (/dev/serial0)
#
# Requires the Klipper source cloned next to this folder as ../klipper
#   (cd .. && git clone https://github.com/Klipper3d/klipper)
#
# Run on macOS or Linux. Result: firmware.bin next to this script.
# -----------------------------------------------------------------------------
set -e
cd "$(dirname "$0")"

KLIPPER_DIR="${KLIPPER_DIR:-../klipper}"
CONFIG_TARGET="firmware.bin"

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

cd "$KLIPPER_DIR"

# --- Write the firmware configuration ---------------------------------------
cat > .config <<'EOF'
CONFIG_LOW_LEVEL_OPTIONS=y
CONFIG_MACH_STM32=y
CONFIG_MACH_STM32F407=y
CONFIG_STM32_FLASH_START_10000=y
CONFIG_STM32_CLOCK_REF_8M=y
CONFIG_STM32_SERIAL_USART3=y
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
