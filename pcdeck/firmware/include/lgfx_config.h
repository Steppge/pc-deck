#pragma once
// LovyanGFX-Konfiguration, alle Pins aus platformio.ini (build_flags).
//   Deck 1 (MKS TS35):          ST7796 + XPT2046-Touch an einem gemeinsamen SPI-Bus
//   Deck 2 (BTT TFT35 SPI V2.1): ILI9488 über SPI (-DTFT_ILI9488) + NS2009-Touch über I2C (-DTOUCH_NS2009)

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

#ifndef PIN_TFT_BL
#define PIN_TFT_BL -1
#endif
#ifndef PIN_TFT_RST
#define PIN_TFT_RST -1
#endif
#ifndef TFT_INVERT
#define TFT_INVERT 0
#endif
#ifndef TFT_BGR
#define TFT_BGR 0
#endif
#ifndef TFT_SPI_FREQ
#define TFT_SPI_FREQ 40000000
#endif
#ifndef TFT_SPI_HOST
#define TFT_SPI_HOST SPI2_HOST  // ESP32-S3: FSPI; klassischer ESP32: SPI3_HOST (VSPI, Pins 18/19/23/5)
#endif
#ifndef PIN_TOUCH_INT
#define PIN_TOUCH_INT -1
#endif

class LGFX : public lgfx::LGFX_Device {
#ifdef TFT_ILI9488
  lgfx::Panel_ILI9488 _panel;
#else
  lgfx::Panel_ST7796 _panel;
#endif
  lgfx::Bus_SPI _bus;
  lgfx::Light_PWM _light;
#ifdef TOUCH_NS2009
  lgfx::Touch_NS2009 _touch;
#else
  lgfx::Touch_XPT2046 _touch;
#endif

 public:
  LGFX() {
    {
      auto cfg = _bus.config();
      cfg.spi_host = TFT_SPI_HOST;
      cfg.spi_mode = 0;
      cfg.freq_write = TFT_SPI_FREQ;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = false;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = PIN_SCLK;
      cfg.pin_mosi = PIN_MOSI;
      cfg.pin_miso = PIN_MISO;
      cfg.pin_dc = PIN_TFT_DC;
      _bus.config(cfg);
      _panel.setBus(&_bus);
    }
    {
      auto cfg = _panel.config();
      cfg.pin_cs = PIN_TFT_CS;
      cfg.pin_rst = PIN_TFT_RST;  // -1: kein Reset-Anschluss, das Display wird per Befehl zurückgesetzt
      cfg.pin_busy = -1;
      cfg.panel_width = 320;
      cfg.panel_height = 480;
      cfg.offset_x = 0;
      cfg.offset_y = 0;
      cfg.offset_rotation = 0;
      cfg.readable = false;
      cfg.invert = TFT_INVERT;
      cfg.rgb_order = TFT_BGR;
      cfg.dlen_16bit = false;
      cfg.bus_shared = true;
      _panel.config(cfg);
    }
#if PIN_TFT_BL >= 0
    {
      auto cfg = _light.config();
      cfg.pin_bl = PIN_TFT_BL;
      cfg.invert = false;
      cfg.freq = 12000;
      cfg.pwm_channel = 7;
      _light.config(cfg);
      _panel.setLight(&_light);
    }
#endif
    {
      auto cfg = _touch.config();
#ifdef TOUCH_NS2009
      cfg.x_min = 0;
      cfg.x_max = 4095;
      cfg.y_min = 0;
      cfg.y_max = 4095;
      cfg.pin_int = PIN_TOUCH_INT;
      cfg.bus_shared = false;
      cfg.offset_rotation = 0;
      cfg.i2c_port = 0;
      cfg.i2c_addr = 0x48;
      cfg.pin_sda = PIN_TOUCH_SDA;
      cfg.pin_scl = PIN_TOUCH_SCL;
      cfg.freq = 400000;
#else
      cfg.x_min = 300;
      cfg.x_max = 3900;
      cfg.y_min = 300;
      cfg.y_max = 3900;
      cfg.pin_int = -1;
      cfg.bus_shared = true;
      cfg.offset_rotation = 0;
      cfg.spi_host = TFT_SPI_HOST;
      cfg.freq = 1000000;
      cfg.pin_sclk = PIN_SCLK;
      cfg.pin_mosi = PIN_MOSI;
      cfg.pin_miso = PIN_MISO;
      cfg.pin_cs = PIN_TOUCH_CS;
#endif
      _touch.config(cfg);
      _panel.setTouch(&_touch);
    }
    setPanel(&_panel);
  }
};
