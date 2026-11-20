# mhs_scripts

## Running the shmoo GUI

```sh
LD_LIBRARY_PATH=$HOME/.local/opt/xcb-util-cursor/usr/lib64 uv run shmoo
```

### Clock generator

"Clock generator" in the GUI picks the DUT clock source:

- **33622A** (default): wavegen CH2 over LAN, high-Z mode, 0 to 1.8 V square, up to 120 MHz.
- **81134A**: pulse generator CH1 normal output over USB, 15 MHz to 3.35 GHz. It has no
  high-Z mode (levels are specified into 50 ohm), so it is programmed to 0 to 0.9 V, which is
  about 0 to 1.8 V at the unterminated DUT clock input. Check the levels at the clock pin on a
  scope before connecting the chip. On Linux, opening it over USB as a normal user needs a
  udev rule for vendor `0957`.

## TODO

- [ ] **Fix the `libxcb-cursor` workaround.** PyQt6 (Qt >= 6.5) needs the system library
  `libxcb-cursor.so.0`, which isn't installed on this machine (RHEL 9 package
  `xcb-util-cursor`). As a stopgap, the RPM was unpacked into
  `~/.local/opt/xcb-util-cursor` and is loaded via `LD_LIBRARY_PATH`. Proper fix: get
  `sudo dnf install xcb-util-cursor` done (e.g. ask the machine admins), then drop
  `LD_LIBRARY_PATH` and delete `~/.local/opt/xcb-util-cursor`.
