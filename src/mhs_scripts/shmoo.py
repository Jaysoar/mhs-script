import logging
import os
import sys
from time import sleep

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from pymeasure.display.Qt import QtCore, QtWidgets
from pymeasure.display.widgets import TabWidget
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import FloatParameter, Parameter, Procedure
from pymeasure.experiment.procedure import ProcedureStatus
from pymeasure.instruments.agilent import Agilent33622A
from pymeasure.instruments.keysight import KeysightEDU36311A

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

PSU_IP = "169.254.168.144"
WAVEGEN_IP = "169.254.5.21"

# Fixed wavegen clock levels, into a 50 ohm load
CLK_LOW = 0.0
CLK_HIGH = 1.0

PASS_COLOR = "#2ca02c"
FAIL_COLOR = "#d62728"
UNTESTED_COLOR = "#d9d9d9"


def dut_test(voltage: float, frequency: float) -> bool:
    """Stub: report whether the DUT passes at this supply voltage (V) and clock frequency (Hz).

    Fakes a shmoo where the max passing frequency rises linearly above a 0.5 V threshold.
    """
    return frequency <= 200e6 * (voltage - 0.5)


def sweep(start: float, end: float, step: float) -> np.ndarray:
    """Points from start to end (inclusive if it lands on the grid) in increments of step."""
    n = int(np.floor((end - start) / step + 1e-9)) + 1
    return np.round(start + step * np.arange(n), 9)


class ShmooProcedure(Procedure):
    psu_address = Parameter("PSU address", default=f"TCPIP::{PSU_IP}::5025::SOCKET")
    wavegen_address = Parameter("Wavegen address", default=f"TCPIP::{WAVEGEN_IP}::5025::SOCKET")
    current_limit = FloatParameter("PSU current limit", units="A", default=0.1)

    # ResultsImage requires <column>_start/_end/_step parameters for the image axes
    voltage_start = FloatParameter("Voltage start", units="V", default=0.6)
    voltage_end = FloatParameter("Voltage end", units="V", default=1.2)
    voltage_step = FloatParameter("Voltage step", units="V", default=0.05)
    frequency_start = FloatParameter("Frequency start", units="MHz", default=10)
    frequency_end = FloatParameter("Frequency end", units="MHz", default=100)
    frequency_step = FloatParameter("Frequency step", units="MHz", default=10)
    settle_time = FloatParameter("Settle time", units="s", default=0.1)

    DATA_COLUMNS = ["voltage", "frequency", "voltage_meas", "pass"]

    def startup(self):
        self.psu = KeysightEDU36311A(self.psu_address)
        self.wavegen = Agilent33622A(self.wavegen_address)
        log.info(f"PSU: {self.psu.id}")
        log.info(f"Wavegen: {self.wavegen.id}")

        self.psu.ch_1.output_enabled = False
        self.psu.ch_1.current_limit = self.current_limit
        self.psu.ch_1.voltage_setpoint = self.voltage_start

        self.wavegen.ch_1.output = False
        self.wavegen.ch_1.output_load = 50
        self.wavegen.ch_1.shape = "SQU"
        self.wavegen.ch_1.voltage_low = CLK_LOW
        self.wavegen.ch_1.voltage_high = CLK_HIGH
        self.wavegen.ch_1.frequency = self.frequency_start * 1e6

        # Power the DUT before driving its clock input
        self.psu.ch_1.output_enabled = True
        self.wavegen.ch_1.output = True

    def execute(self):
        voltages = sweep(self.voltage_start, self.voltage_end, self.voltage_step)
        frequencies = sweep(self.frequency_start, self.frequency_end, self.frequency_step)
        total = len(voltages) * len(frequencies)

        for i, voltage in enumerate(voltages):
            self.psu.ch_1.voltage_setpoint = voltage
            for j, frequency in enumerate(frequencies):
                if self.should_stop():
                    log.warning("Caught the stop flag in the procedure")
                    return
                self.wavegen.ch_1.frequency = frequency * 1e6
                sleep(self.settle_time)
                passed = dut_test(voltage, frequency * 1e6)
                self.emit("results", {
                    "voltage": voltage,
                    "frequency": frequency,
                    "voltage_meas": self.psu.ch_1.voltage,
                    "pass": int(passed),
                })
                log.info(f"{voltage:.3f} V, {frequency:.3f} MHz: {'PASS' if passed else 'FAIL'}")
                self.emit("progress", 100 * (i * len(frequencies) + j + 1) / total)

    def shutdown(self):
        # Clock off before power off; startup may have failed partway through
        if hasattr(self, "wavegen"):
            self.wavegen.ch_1.output = False
        if hasattr(self, "psu"):
            self.psu.ch_1.output_enabled = False
        log.info("Outputs disabled")


class ShmooCurve:
    """Handle tying one run's results to the ShmooWidget, in the role of a pymeasure curve."""

    def __init__(self, results, wdg):
        self.results = results
        self.wdg = wdg

    def update_data(self):
        self.wdg.redraw(self)


class ShmooWidget(TabWidget, QtWidgets.QWidget):
    """Matplotlib pass/fail heatmap of the most recently loaded run."""

    def __init__(self, name, refresh_time=0.5, parent=None):
        super().__init__(name, parent=parent)
        self.curves = []
        self.figure = Figure(layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax = self.figure.add_subplot()

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(NavigationToolbar2QT(self.canvas, self))
        layout.addWidget(self.canvas)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(refresh_time * 1e3))
        self.draw()

    def new_curve(self, results, color=None, **kwargs):
        return ShmooCurve(results, self)

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

        # NaN marks points that haven't been run (yet)
        grid = np.full((len(frequencies), len(voltages)), np.nan)
        data = results.data
        if data is not None:
            for _, row in data.iterrows():
                i = np.abs(frequencies - row["frequency"]).argmin()
                j = np.abs(voltages - row["voltage"]).argmin()
                grid[i, j] = row["pass"]

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
        self.ax.legend(
            handles=[Patch(color=PASS_COLOR, label="Pass"), Patch(color=FAIL_COLOR, label="Fail"),
                     Patch(color=UNTESTED_COLOR, label="Not run")],
            loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False,
        )
        self.canvas.draw_idle()


class MainWindow(ManagedWindow):
    def __init__(self):
        shmoo_widget = ShmooWidget("Shmoo")
        super().__init__(
            procedure_class=ShmooProcedure,
            inputs=[
                "psu_address", "wavegen_address", "current_limit",
                "voltage_start", "voltage_end", "voltage_step",
                "frequency_start", "frequency_end", "frequency_step",
                "settle_time",
            ],
            displays=["voltage_start", "voltage_end", "frequency_start", "frequency_end"],
            x_axis="voltage",
            y_axis="voltage_meas",
            widget_list=(shmoo_widget,),
        )
        self.setWindowTitle("V/F Shmoo")


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
