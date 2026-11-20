"""DUT clock sources for the shmoo.

Both drive the DUT clock input (P1) with a 0 to 1.8 V square wave. The input is not 50 ohm
terminated, so it is driven as a high-Z load. Each source has the same interface: setup()
programs it with the output off and refuses to continue if a setting reads back wrong,
set_frequency() changes the frequency, and enable() switches the output.
"""

import logging

from pymeasure.instruments.agilent import Agilent33622A
from pymeasure.instruments.keysight import Keysight81134A

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

WAVEGEN_IP = "169.254.5.21"
PULSEGEN_ADDRESS = "USB0::2391::16392::DE42800784::0::INSTR"

# 33622A CH2: 0 to 1.8 V square in high-Z mode, so the levels are what reaches the DUT.
CLK_AMPLITUDE = 1.8  # Vpp
CLK_OFFSET = 0.9  # V

# The 81134A has no high-Z mode: its levels are specified into 50 ohm, so the open DUT input
# sees about twice the programmed levels. 0 to 0.9 V programmed gives about 0 to 1.8 V at P1.
PULSEGEN_HIGH = 0.9  # V, as programmed
PULSEGEN_LOW = 0.0  # V, as programmed


class WavegenClock:
    """Agilent 33622A waveform generator, clock on CH2."""

    name = "33622A"
    min_frequency = 1e-6  # Hz
    max_frequency = 120e6  # Hz

    def __init__(self, address: str):
        self.wavegen = Agilent33622A(address)
        self.id = self.wavegen.id

    def setup(self, frequency: float):
        ch = self.wavegen.ch_2
        ch.output = False
        # High-Z first: the load setting changes how amplitude/offset are interpreted
        ch.output_load = "INF"
        ch.shape = "SQU"
        ch.amplitude_unit = "VPP"
        ch.amplitude = CLK_AMPLITUDE
        ch.offset = CLK_OFFSET
        ch.frequency = frequency
        self.check()

    def check(self):
        """Read back the clock settings and refuse to continue if any are wrong."""
        ch = self.wavegen.ch_2
        load, amplitude, offset = ch.output_load, ch.amplitude, ch.offset
        log.info(f"Wavegen CH2: load={load}, {amplitude} Vpp, offset {offset} V, {ch.shape}")
        # The 33622A reports high-Z (INFinity) as 9.9E+37
        if not (isinstance(load, float) and load > 1e37):
            raise RuntimeError(f"Wavegen CH2 load is {load!r}, not high-Z; refusing to output")
        if abs(amplitude - CLK_AMPLITUDE) > 1e-3 or abs(offset - CLK_OFFSET) > 1e-3:
            raise RuntimeError(f"Wavegen CH2 is {amplitude} Vpp / {offset} V offset, expected "
                               f"{CLK_AMPLITUDE} Vpp / {CLK_OFFSET} V; refusing to output")

    def set_frequency(self, frequency: float):
        self.wavegen.ch_2.frequency = frequency

    def enable(self, on: bool):
        self.wavegen.ch_2.output = on


class PulsegenClock:
    """Keysight 81134A pulse/pattern generator, clock on the CH1 normal output."""

    name = "81134A"
    min_frequency = 15e6  # Hz
    max_frequency = 3.35e9  # Hz

    def __init__(self, address: str):
        self.gen = Keysight81134A(address)
        self.id = self.gen.id

    def setup(self, frequency: float):
        gen, ch = self.gen, self.gen.ch_1
        ch.output_enabled = False
        gen.ch_2.output_enabled = False
        gen.trigger_output.output_enabled = False
        gen.outputs_enabled = True  # central switch; the outputs above stay off
        gen.write("FUNC PATT")  # pattern mode, so each channel runs continuously
        gen.clock_source = "IMM"
        ch.mode = "SQU"
        ch.divider = 1
        ch.polarity = "NORM"
        ch.termination_voltage = 0
        # Shrink the amplitude, centre it, then widen it: from any starting levels, no step
        # takes the high or low level out of range
        ch.level_limits_enabled = False
        ch.amplitude = 0.05
        ch.offset = (PULSEGEN_HIGH + PULSEGEN_LOW) / 2
        ch.amplitude = PULSEGEN_HIGH - PULSEGEN_LOW
        # Lock the levels so they can't be changed (e.g. from the front panel) during the run
        ch.level_limits_enabled = True
        gen.frequency = frequency
        self.check()

    def check(self):
        """Read back the clock settings and refuse to continue if any are wrong."""
        gen, ch = self.gen, self.gen.ch_1
        high, low, mode, divider = ch.voltage_high, ch.voltage_low, ch.mode, ch.divider
        log.info(f"81134A CH1: {low} to {high} V into 50 ohm, {mode}, divider {divider}, "
                 f"{gen.frequency / 1e6:g} MHz")
        if abs(high - PULSEGEN_HIGH) > 1e-3 or abs(low - PULSEGEN_LOW) > 1e-3:
            raise RuntimeError(f"81134A CH1 is {low} to {high} V, expected {PULSEGEN_LOW} to "
                               f"{PULSEGEN_HIGH} V; refusing to output")
        if mode != "SQU" or divider != 1 or gen.ask("FUNC?").strip() != "PATT":
            raise RuntimeError(f"81134A CH1 is in mode {mode} with divider {divider}, expected "
                               "a square wave with divider 1; refusing to output")
        errors = gen.check_errors()
        if errors:
            raise RuntimeError(f"81134A reported errors {errors}; refusing to output")

    def set_frequency(self, frequency: float):
        self.gen.frequency = frequency

    def enable(self, on: bool):
        # Normal output only; the complement output stays off
        self.gen.ch_1.positive_output_enabled = on


CLOCKS = {clock.name: clock for clock in (WavegenClock, PulsegenClock)}
