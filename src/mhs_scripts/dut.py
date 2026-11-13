"""Run one test on the Penguin DUT and judge it from its UART output.

The test is loaded and started over JTAG by baremetal-ide's scripts/shmoo/run.sh, which
uses channel A of the carrier board's FT2232H. The chip prints its result on UART0, which
comes back on channel B of the same FTDI.
"""

import argparse
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import serial

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# baremetal-ide is checked out next to mhs-script
RUN_SCRIPT = str(Path(__file__).resolve().parents[3] / "baremetal-ide/scripts/shmoo/run.sh")
UART_PORT = "/dev/serial/by-id/usb-FTDI_Dual_RS232-HS-if01-port0"
UART_BAUD = 115200

PASS_MARKER = "All tests passed"
FAIL_MARKER = "Test failed"

# Allowance on top of the test's run time for the result to arrive over UART
RESULT_MARGIN = 5.0
OPENOCD_TIMEOUT = 60.0


@dataclass
class DutResult:
    passed: bool
    reason: str  # "pass", "fail", "timeout" or "load error"
    uart: str  # everything received on the UART


def parse_uart(text: str) -> bool | None:
    """True if the output reports a pass, False if a failure, None if no verdict yet."""
    if FAIL_MARKER in text:
        return False
    if PASS_MARKER in text:
        return True
    return None


def run_dut_test(clk_hz: float, seconds: int = 0, port: str = UART_PORT,
                 run_script: str = RUN_SCRIPT, elf: str | None = None,
                 margin: float = RESULT_MARGIN) -> DutResult:
    """Load and start the test at SoC clock clk_hz, looping for `seconds`, and wait for its verdict."""
    # Open the UART before starting the test so none of its output is missed
    with serial.Serial(port, UART_BAUD, stopbits=serial.STOPBITS_TWO, timeout=0.1) as uart:
        uart.reset_input_buffer()

        cmd = [run_script, str(int(round(clk_hz))), str(int(seconds))]
        if elf:
            cmd.append(elf)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=OPENOCD_TIMEOUT)
        except subprocess.TimeoutExpired:
            log.error(f"{run_script} timed out after {OPENOCD_TIMEOUT} s")
            return DutResult(False, "load error", "")
        if proc.returncode != 0:
            log.error(f"{run_script} exited with {proc.returncode}:\n{proc.stdout}{proc.stderr}")
            return DutResult(False, "load error", "")
        log.debug(f"OpenOCD output:\n{proc.stdout}{proc.stderr}")

        # The test runs `seconds` of wall time (it counts cycles at clk_hz) after starting
        deadline = time.monotonic() + seconds + margin
        received = b""
        while time.monotonic() < deadline:
            received += uart.read(uart.in_waiting or 1)
            text = received.decode(errors="replace")
            verdict = parse_uart(text)
            if verdict is not None:
                # Let the rest of the line(s) after a failure arrive for the log
                if not verdict:
                    time.sleep(0.1)
                    text += uart.read(uart.in_waiting).decode(errors="replace")
                return DutResult(verdict, "pass" if verdict else "fail", text)
        return DutResult(False, "timeout", received.decode(errors="replace"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one test on the Penguin DUT.")
    parser.add_argument("clk_hz", type=float, help="SoC clock frequency in Hz")
    parser.add_argument("seconds", type=int, nargs="?", default=0,
                        help="how long the test loops (0 = one pass)")
    parser.add_argument("--port", default=UART_PORT, help="UART device")
    parser.add_argument("--run-script", default=RUN_SCRIPT)
    parser.add_argument("--elf", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    result = run_dut_test(args.clk_hz, args.seconds, args.port, args.run_script, args.elf)
    print(result.uart, end="" if result.uart.endswith("\n") else "\n")
    print(f"{'PASS' if result.passed else 'FAIL'} ({result.reason})")
    raise SystemExit(0 if result.passed else 1)
