# mhs_scripts

## Running the shmoo GUI

```sh
LD_LIBRARY_PATH=$HOME/.local/opt/xcb-util-cursor/usr/lib64 uv run shmoo
```

### Clock generator

"Clock address" is the DUT clock source. The GUI reads its `*IDN?` and drives it accordingly:

- **33622A** (default, `TCPIP::169.254.5.21::5025::SOCKET`): wavegen CH2, high-Z mode, 0 to
  1.8 V square, up to 120 MHz.
- **81134A** (`USB0::2391::16392::DE42800784::0::INSTR`): pulse generator CH1 normal output,
  15 MHz to 3.35 GHz. It has no high-Z mode (levels are specified into 50 ohm), so it is
  programmed to 0 to 0.9 V, which is about 0 to 1.8 V at the unterminated DUT clock input.
  Check the levels at the clock pin on a scope before connecting the chip. On Linux, opening
  it over USB as a normal user needs a udev rule for vendor `0957`.
