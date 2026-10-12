// FSMC 8080 parallel TFT driver (ST7796S 480x320)
//
// Mingda Magician Max (langgo407 / GD32F407VET6).
//
// Configures the 16-bit FSMC bus at MCU startup and exposes simple drawing
// commands used by the Klipper `st7796` display chip on the host.
//
// FSMC bank1 (NE1) base = 0x60000000. The LCD "RS" line is on A23 (PE2): a
// 16-bit access at base + ((2<<23)-2) selects a command and the next slot
// selects data.
//
// This file is distributed under the terms of the GNU GPLv3 license.

#include <stdint.h>
#include "internal.h" // RCC, GPIO_TypeDef
#include "command.h" // DECL_COMMAND
#include "sched.h" // DECL_INIT
#include "font8x14.h"

#define TFT_REG (*(volatile uint16_t *)0x60FFFFFEUL) // RS low  (A23=0)
#define TFT_RAM (*(volatile uint16_t *)0x61000000UL) // RS high (A23=1)

#define FSMC_BASE  0xA0000000UL
#define FSMC_BCR1  (*(volatile uint32_t *)(FSMC_BASE + 0x000))
#define FSMC_BTR1  (*(volatile uint32_t *)(FSMC_BASE + 0x004))
#define FSMC_BWTR1 (*(volatile uint32_t *)(FSMC_BASE + 0x104))

static void
tft_gpio_af12(GPIO_TypeDef *regs, uint32_t pin)
{
    uint32_t sh = pin * 2;
    regs->MODER = (regs->MODER & ~(3UL << sh)) | (2UL << sh); // alternate func
    regs->OSPEEDR |= (3UL << sh);                             // very high speed
    regs->PUPDR &= ~(3UL << sh);                              // no pull
    uint32_t idx = pin >> 3, ash = (pin & 7) * 4;
    regs->AFR[idx] = (regs->AFR[idx] & ~(0xFUL << ash))
        | (12UL << ash);                                      // AF12 = FSMC
}

void
tft_fsmc_setup(void)
{
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIODEN | RCC_AHB1ENR_GPIOEEN
        | RCC_AHB1ENR_GPIOGEN;
    RCC->AHB3ENR |= RCC_AHB3ENR_FSMCEN;

    // 16 data lines (FSMC D0..D15)
    tft_gpio_af12(GPIOD, 14); tft_gpio_af12(GPIOD, 15);
    tft_gpio_af12(GPIOD, 0);  tft_gpio_af12(GPIOD, 1);
    tft_gpio_af12(GPIOE, 7);  tft_gpio_af12(GPIOE, 8);
    tft_gpio_af12(GPIOE, 9);  tft_gpio_af12(GPIOE, 10);
    tft_gpio_af12(GPIOE, 11); tft_gpio_af12(GPIOE, 12);
    tft_gpio_af12(GPIOE, 13); tft_gpio_af12(GPIOE, 14);
    tft_gpio_af12(GPIOE, 15);
    tft_gpio_af12(GPIOD, 8);  tft_gpio_af12(GPIOD, 9);
    tft_gpio_af12(GPIOD, 10);
    // Control lines: NOE=PD4, NWE=PD5, NE1=PD7, RS=A23=PE2
    tft_gpio_af12(GPIOD, 4); tft_gpio_af12(GPIOD, 5); tft_gpio_af12(GPIOD, 7);
    tft_gpio_af12(GPIOE, 2);

    // Backlight PG10 -> output high
    GPIOG->MODER = (GPIOG->MODER & ~(3UL << 20)) | (1UL << 20);
    GPIOG->BSRR = (1UL << 10);

    // Bank1: 16-bit SRAM, write enabled, separate read/write timings.
    FSMC_BCR1 = (1UL << 0) | (1UL << 4) | (1UL << 12) | (1UL << 14);
    FSMC_BTR1 = (15UL << 0) | (15UL << 4) | (24UL << 8)
        | (16UL << 20) | (17UL << 24);
    FSMC_BWTR1 = (8UL << 0) | (15UL << 4) | (8UL << 8);
}

static void
tft_set_window(uint16_t xs, uint16_t ys, uint16_t xe, uint16_t ye)
{
    TFT_REG = 0x2A; // CASET
    TFT_RAM = xs >> 8; TFT_RAM = xs & 0xFF;
    TFT_RAM = xe >> 8; TFT_RAM = xe & 0xFF;
    TFT_REG = 0x2B; // RASET
    TFT_RAM = ys >> 8; TFT_RAM = ys & 0xFF;
    TFT_RAM = ye >> 8; TFT_RAM = ye & 0xFF;
}

static void
tft_rect(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t color)
{
    if (!w || !h)
        return;
    tft_set_window(x, y, x + w - 1, y + h - 1);
    TFT_REG = 0x2C; // RAMWR
    uint32_t count = (uint32_t)w * h;
    for (uint32_t i = 0; i < count; i++)
        TFT_RAM = color;
}

// Draw one 8x14 glyph scaled by `scale`, foreground/background colours.
static void
tft_char(uint16_t x, uint16_t y, uint8_t scale, uint8_t ch, uint16_t fg,
         uint16_t bg)
{
    if (!scale)
        return;
    tft_set_window(x, y, x + 8 * scale - 1, y + 16 * scale - 1);
    TFT_REG = 0x2C; // RAMWR
    const uint8_t *g = font8x14[ch];
    for (uint32_t row = 0; row < 16; row++) {
        uint8_t bits = g[row];
        for (uint32_t ry = 0; ry < scale; ry++) {
            for (uint32_t col = 0; col < 8; col++) {
                uint16_t c = (bits << col) & 0x80 ? fg : bg;
                for (uint32_t rx = 0; rx < scale; rx++)
                    TFT_RAM = c;
            }
        }
    }
}

// Draw a 16x16 1-bit icon (32 bytes, 16 rows big-endian) scaled to w x h.
static void
tft_icon(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t fg,
         uint16_t bg, const uint8_t *data, uint8_t len)
{
    if (!w || !h || len < 32)
        return;
    tft_set_window(x, y, x + w - 1, y + h - 1);
    TFT_REG = 0x2C; // RAMWR
    for (uint32_t oy = 0; oy < h; oy++) {
        uint32_t sy = oy * 16 / h;
        uint16_t row = (data[sy * 2] << 8) | data[sy * 2 + 1];
        for (uint32_t ox = 0; ox < w; ox++) {
            uint32_t sx = ox * 16 / w;
            TFT_RAM = (row << sx) & 0x8000 ? fg : bg;
        }
    }
}

// ---------------------------------------------------------------------------
// Host commands
// ---------------------------------------------------------------------------

// tft_icon x= y= w= h= fg= bg= data=<32 bytes>
void
command_tft_icon(uint32_t *args)
{
    uint8_t len = args[6];
    const uint8_t *data = command_decode_ptr(args[7]);
    tft_icon(args[0], args[1], args[2], args[3], args[4], args[5], data, len);
}
DECL_COMMAND(command_tft_icon,
             "tft_icon x=%hu y=%hu w=%hu h=%hu fg=%hu bg=%hu data=%*s");

// tft_cmd reg=<byte>
void
command_tft_cmd(uint32_t *args)
{
    TFT_REG = args[0] & 0xFF;
}
DECL_COMMAND(command_tft_cmd, "tft_cmd reg=%c");

// tft_data val=<uint16>
void
command_tft_data(uint32_t *args)
{
    TFT_RAM = args[0];
}
DECL_COMMAND(command_tft_data, "tft_data val=%hu");

// tft_rect x= y= w= h= color=
void
command_tft_rect(uint32_t *args)
{
    tft_rect(args[0], args[1], args[2], args[3], args[4]);
}
DECL_COMMAND(command_tft_rect, "tft_rect x=%hu y=%hu w=%hu h=%hu color=%hu");

// tft_char x= y= scale= ch= fg= bg=
void
command_tft_char(uint32_t *args)
{
    tft_char(args[0], args[1], args[2], args[3], args[4], args[5]);
}
DECL_COMMAND(command_tft_char,
             "tft_char x=%hu y=%hu scale=%c ch=%c fg=%hu bg=%hu");

void
tft_init(void)
{
    tft_fsmc_setup();
}
DECL_INIT(tft_init);
