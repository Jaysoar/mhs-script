# mhs_scripts

## Running the shmoo GUI

```sh
LD_LIBRARY_PATH=$HOME/.local/opt/xcb-util-cursor/usr/lib64 uv run shmoo
```

## TODO

- [ ] **Fix the `libxcb-cursor` workaround.** PyQt6 (Qt >= 6.5) needs the system library
  `libxcb-cursor.so.0`, which isn't installed on this machine (RHEL 9 package
  `xcb-util-cursor`). As a stopgap, the RPM was unpacked into
  `~/.local/opt/xcb-util-cursor` and is loaded via `LD_LIBRARY_PATH`. Proper fix: get
  `sudo dnf install xcb-util-cursor` done (e.g. ask the machine admins), then drop
  `LD_LIBRARY_PATH` and delete `~/.local/opt/xcb-util-cursor`.
