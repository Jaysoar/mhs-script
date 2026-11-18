import logging
import os
import sys
import threading
from time import sleep

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib import colormaps
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from pymeasure.display.Qt import QtCore, QtWidgets
from pymeasure.display.widgets import TabWidget
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import FloatParameter, IntegerParameter, Parameter, Procedure
from pymeasure.experiment.procedure import ProcedureStatus
from pymeasure.instruments.agilent import Agilent33622A
from pymeasure.instruments.keysight import KeysightEDU36311A

from mhs_scripts.dut import RUN_SCRIPT, UART_PORT, run_dut_test

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

PSU_IP = "169.254.54.112"
WAVEGEN_IP = "169.254.5.21"

# DUT clock on wavegen CH2: 0 to 1.8 V square, into a high-Z load. The DUT clock input
# is not 50 ohm terminated, so the wavegen MUST be in high-Z mode or the levels double.
CLK_AMPLITUDE = 1.8  # Vpp
CLK_OFFSET = 0.9  # V

PASS_COLOR = "#2ca02c"
FAIL_COLOR = "#d62728"
UNTESTED_COLOR = "#d9d9d9"


def sweep(start: float, end: float, step: float) -> np.ndarray:
    """Points from start to end (inclusive if it lands on the grid) in increments of step."""
    n = int(np.floor((end - start) / step + 1e-9)) + 1
    return np.round(start + step * np.arange(n), 9)


class PowerMonitor:
    """Poll a PSU channel's V and I in a background thread between start() and stop().

    The PSU must not be touched from other threads while it runs. Each V+I sample takes
    ~0.3 s over LAN, so a 1 s window gives only ~3 samples.
    """

    def __init__(self, channel):
        self.channel = channel
        self.powers = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.powers.append(self.channel.voltage * self.channel.current)
            except Exception as e:
                log.warning(f"PSU power sample failed: {e}")
                return

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()

    @property
    def mean(self) -> float:
        return float(np.mean(self.powers)) if self.powers else float("nan")

    @property
    def max(self) -> float:
        return float(np.max(self.powers)) if self.powers else float("nan")


class ShmooProcedure(Procedure):
    psu_address = Parameter("PSU address", default=f"TCPIP::{PSU_IP}::5025::SOCKET")
    wavegen_address = Parameter("Wavegen address", default=f"TCPIP::{WAVEGEN_IP}::5025::SOCKET")
    current_limit = FloatParameter("PSU current limit", units="A", default=1.0)
    uart_port = Parameter("DUT UART port", default=UART_PORT)
    run_script = Parameter("DUT run script", default=RUN_SCRIPT)
    test_seconds = IntegerParameter("Test duration", units="s", default=60, minimum=0)

    # ResultsImage requires <column>_start/_end/_step parameters for the image axes
    voltage_start = FloatParameter("Voltage start", units="V", default=0.6)
    voltage_end = FloatParameter("Voltage end", units="V", default=1.0)
    voltage_step = FloatParameter("Voltage step", units="V", default=0.02)
    frequency_start = FloatParameter("Frequency start", units="MHz", default=10)
    frequency_end = FloatParameter("Frequency end", units="MHz", default=100)
    frequency_step = FloatParameter("Frequency step", units="MHz", default=5)
    settle_time = FloatParameter("Settle time", units="s", default=0.1)
    power_cycle_time = FloatParameter("Power cycle off time", units="s", default=2)

    DATA_COLUMNS = ["voltage", "frequency", "voltage_meas", "pass", "power_avg", "power_max"]

    def startup(self):
        self.psu = KeysightEDU36311A(self.psu_address)
        self.wavegen = Agilent33622A(self.wavegen_address)
        log.info(f"PSU: {self.psu.id}")
        log.info(f"Wavegen: {self.wavegen.id}")

        self.psu.ch_1.output_enabled = False
        self.psu.ch_1.current_limit = self.current_limit
        self.psu.ch_1.voltage_setpoint = self.voltage_start

        self.wavegen.ch_2.output = False
        # High-Z first: the load setting changes how amplitude/offset are interpreted
        self.wavegen.ch_2.output_load = "INF"
        self.wavegen.ch_2.shape = "SQU"
        self.wavegen.ch_2.amplitude_unit = "VPP"
        self.wavegen.ch_2.amplitude = CLK_AMPLITUDE
        self.wavegen.ch_2.offset = CLK_OFFSET
        self.wavegen.ch_2.frequency = self.frequency_start * 1e6
        self.check_wavegen()

        # Power the DUT before driving its clock input
        self.psu.ch_1.output_enabled = True
        self.wavegen.ch_2.output = True

    def check_wavegen(self):
        """Read back the wavegen clock settings and refuse to continue if any are wrong."""
        ch = self.wavegen.ch_2
        load, amplitude, offset = ch.output_load, ch.amplitude, ch.offset
        log.info(f"Wavegen CH2: load={load}, {amplitude} Vpp, offset {offset} V, {ch.shape}")
        # The 33622A reports high-Z (INFinity) as 9.9E+37
        if not (isinstance(load, float) and load > 1e37):
            raise RuntimeError(f"Wavegen CH2 load is {load!r}, not high-Z; refusing to output")
        if abs(amplitude - CLK_AMPLITUDE) > 1e-3 or abs(offset - CLK_OFFSET) > 1e-3:
            raise RuntimeError(f"Wavegen CH2 is {amplitude} Vpp / {offset} V offset, expected "
                               f"{CLK_AMPLITUDE} Vpp / {CLK_OFFSET} V; refusing to output")

    def execute(self):
        voltages = sweep(self.voltage_start, self.voltage_end, self.voltage_step)
        frequencies = sweep(self.frequency_start, self.frequency_end, self.frequency_step)
        total = len(voltages) * len(frequencies)

        # Voltage is the inner (fastest) loop: each frequency is swept across all voltages
        for i, frequency in enumerate(frequencies):
            self.wavegen.ch_2.frequency = frequency * 1e6
            for j, voltage in enumerate(voltages):
                if self.should_stop():
                    log.warning("Caught the stop flag in the procedure")
                    return
                self.psu.ch_1.voltage_setpoint = voltage
                sleep(self.settle_time)
                result, power = self.run_point(frequency)
                if result.reason == "load error" and not self.should_stop():
                    # The chip can wedge so that JTAG can't reach it until it is power cycled
                    log.warning(f"Load error at {voltage:.3f} V, {frequency:.3f} MHz; "
                                "power cycling the DUT and retrying once")
                    self.power_cycle()
                    result, power = self.run_point(frequency)
                if result.reason == "aborted" or self.should_stop():
                    # Don't record a point that was cut short
                    log.warning("Caught the stop flag in the procedure")
                    return
                passed = result.passed
                if not passed:
                    log.warning(f"DUT {result.reason} at {voltage:.3f} V, {frequency:.3f} MHz; "
                                f"UART: {result.uart.strip()!r}")
                self.emit("results", {
                    "voltage": voltage,
                    "frequency": frequency,
                    "voltage_meas": self.psu.ch_1.voltage,
                    "pass": int(passed),
                    "power_avg": power.mean,
                    "power_max": power.max,
                })
                log.info(f"{voltage:.3f} V, {frequency:.3f} MHz: {'PASS' if passed else 'FAIL'}, "
                         f"avg {power.mean * 1e3:.1f} mW, max {power.max * 1e3:.1f} mW "
                         f"({len(power.powers)} samples)")
                self.emit("progress", 100 * (i * len(voltages) + j + 1) / total)

    def run_point(self, frequency):
        """Run the DUT test once at frequency (MHz), sampling supply power while it runs.

        Power is only sampled from when the test starts on the DUT (after the JTAG load) until
        its verdict arrives, so it reflects the workload rather than loading.
        """
        power = PowerMonitor(self.psu.ch_1)
        try:
            result = run_dut_test(frequency * 1e6, self.test_seconds, self.uart_port,
                                  self.run_script, on_start=power.start,
                                  should_stop=self.should_stop)
        finally:
            power.stop()
        return result, power

    def power_cycle(self):
        """Power cycle the DUT, keeping the clock off whenever it is unpowered."""
        self.wavegen.ch_2.output = False
        self.psu.ch_1.output_enabled = False
        sleep(self.power_cycle_time)
        self.psu.ch_1.output_enabled = True
        sleep(self.settle_time)
        self.wavegen.ch_2.output = True
        sleep(self.settle_time)

    def shutdown(self):
        # Clock off before power off; startup may have failed partway through
        if hasattr(self, "wavegen"):
            self.wavegen.ch_2.output = False
        if hasattr(self, "psu"):
            self.psu.ch_1.output_enabled = False
        log.info("Outputs disabled")


class HeatmapCurve:
    """Handle tying one run's results to a HeatmapWidget, in the role of a pymeasure curve."""

    def __init__(self, results, wdg):
        self.results = results
        self.wdg = wdg

    def update_data(self):
        self.wdg.redraw(self)


class HeatmapWidget(TabWidget, QtWidgets.QWidget):
    """Matplotlib voltage x frequency heatmap of one data column for the latest loaded run."""

    column = None

    def __init__(self, name, refresh_time=0.5, parent=None):
        super().__init__(name, parent=parent)
        self.curves = []
        self.figure = Figure(layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax = self.figure.add_subplot()
        self.colorbar = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(NavigationToolbar2QT(self.canvas, self))
        layout.addWidget(self.canvas)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(refresh_time * 1e3))
        self.draw()

    def new_curve(self, results, color=None, **kwargs):
        return HeatmapCurve(results, self)

    def load(self, curve):
        self.curves.append(curve)
        self.draw()

    def remove(self, curve):
        if curve in self.curves:
            self.curves.remove(curve)
        self.draw()

    def refresh(self):
        if self.curves and self.curves[-1].results.procedure.status == ProcedureStatus.RUNNING:
            self.draw()

    def redraw(self, curve):
        if self.curves and curve is self.curves[-1]:
            self.draw()

    def draw(self):
        if self.colorbar is not None:
            self.colorbar.remove()
            self.colorbar = None
        self.ax.clear()
        if not self.curves:
            self.ax.set_axis_off()
            self.ax.text(0.5, 0.5, "Queue a run to see its shmoo", ha="center", va="center",
                         transform=self.ax.transAxes, color="grey")
            self.canvas.draw_idle()
            return

        results = self.curves[-1].results
        procedure = results.procedure
        voltages = sweep(procedure.voltage_start, procedure.voltage_end, procedure.voltage_step)
        frequencies = sweep(procedure.frequency_start, procedure.frequency_end,
                            procedure.frequency_step)

        # NaN marks points that haven't been run (yet), or have no value for this column
        grid = np.full((len(frequencies), len(voltages)), np.nan)
        data = results.data
        if data is not None and self.column in data:
            for _, row in data.iterrows():
                i = np.abs(frequencies - row["frequency"]).argmin()
                j = np.abs(voltages - row["voltage"]).argmin()
                grid[i, j] = row[self.column]

        self.plot(grid)

        self.ax.set_xticks(range(len(voltages)), [f"{v:g}" for v in voltages],
                           rotation=45 if len(voltages) > 10 else 0)
        self.ax.set_yticks(range(len(frequencies)), [f"{f:g}" for f in frequencies])
        self.ax.set_xticks(np.arange(len(voltages) + 1) - 0.5, minor=True)
        self.ax.set_yticks(np.arange(len(frequencies) + 1) - 0.5, minor=True)
        self.ax.grid(which="minor", color="white", linewidth=1)
        self.ax.tick_params(which="minor", length=0)
        self.ax.set_xlabel("Voltage (V)")
        self.ax.set_ylabel("Frequency (MHz)")
        self.ax.set_title(os.path.basename(results.data_filename))
        self.canvas.draw_idle()

    def plot(self, grid):
        raise NotImplementedError


class ShmooWidget(HeatmapWidget):
    """Pass/fail map."""

    column = "pass"

    def plot(self, grid):
        cmap = ListedColormap([FAIL_COLOR, PASS_COLOR])
        cmap.set_bad(UNTESTED_COLOR)
        self.ax.imshow(np.ma.masked_invalid(grid), cmap=cmap, vmin=0, vmax=1, origin="lower",
                       aspect="auto", interpolation="nearest")

        # Label cells with P/F unless they'd be too small to read
        if grid.size <= 400:
            for (i, j), value in np.ndenumerate(grid):
                if not np.isnan(value):
                    self.ax.text(j, i, "P" if value else "F", ha="center", va="center",
                                 color="white", fontsize=8, fontweight="bold")

        self.ax.legend(
            handles=[Patch(color=PASS_COLOR, label="Pass"), Patch(color=FAIL_COLOR, label="Fail"),
                     Patch(color=UNTESTED_COLOR, label="Not run")],
            loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False,
        )


class PowerWidget(HeatmapWidget):
    """Average supply power while the test runs, in mW."""

    column = "power_avg"

    def plot(self, grid):
        grid = grid * 1e3
        cmap = colormaps["viridis"].copy()
        cmap.set_bad(UNTESTED_COLOR)
        image = self.ax.imshow(np.ma.masked_invalid(grid), cmap=cmap, origin="lower",
                               aspect="auto", interpolation="nearest")
        self.colorbar = self.figure.colorbar(image, ax=self.ax, label="Average power (mW)")

        # Label cells with their value unless they'd be too small to read; dark text on the
        # light (high) end of viridis, white on the dark end
        if grid.size <= 400 and not np.isnan(grid).all():
            norm = image.norm
            for (i, j), value in np.ndenumerate(grid):
                if not np.isnan(value):
                    color = "black" if norm(value) > 0.6 else "white"
                    self.ax.text(j, i, f"{value:.3g}", ha="center", va="center", color=color,
                                 fontsize=7)


class MainWindow(ManagedWindow):
    def __init__(self):
        shmoo_widget = ShmooWidget("Shmoo")
        power_widget = PowerWidget("Power")
        super().__init__(
            procedure_class=ShmooProcedure,
            inputs=[
                "psu_address", "wavegen_address", "current_limit",
                "uart_port", "run_script", "test_seconds",
                "voltage_start", "voltage_end", "voltage_step",
                "frequency_start", "frequency_end", "frequency_step",
                "settle_time", "power_cycle_time",
            ],
            displays=["voltage_start", "voltage_end", "frequency_start", "frequency_end"],
            x_axis="voltage",
            y_axis="voltage_meas",
            widget_list=(shmoo_widget, power_widget),
        )
        self.setWindowTitle("V/F Shmoo")

    # pymeasure's Abort also pauses the queue until "Resume" is clicked. Here Abort only cancels
    # the running sweep: the button stays "Abort", and queued sweeps carry on afterwards.
    def abort(self):
        self.abort_button.setEnabled(False)  # until the sweep has actually stopped
        try:
            self.manager.abort()
        except Exception:
            log.exception("Failed to abort experiment")

    def abort_returned(self, experiment):
        self.manager.resume()  # undo the pause; starts the next queued sweep, if any
        if self.manager.is_running():
            self.abort_button.setEnabled(True)
        else:
            self.browser_widget.clear_button.setEnabled(True)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
