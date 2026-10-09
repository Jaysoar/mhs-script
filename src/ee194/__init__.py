from scpi.keysight import EDU36311A
import time
import argparse

def main() -> None:
    parser = argparse.ArgumentParser(description="voltage shmoo")

    parser.add_argument("start", type=float, help="start voltage")
    parser.add_argument("stop", type=float, help="stop voltage")
    parser.add_argument("step", type=float, help="step voltage increment")
    parser.add_argument("wait", type=float, help="wait time [s] in between")
    args = parser.parse_args()

    device = EDU36311A("169.254.225.182")  # Update with IP addr of PSU
    device.connect()

    print(device.get_instrument_identification())

    device.enable_output(EDU36311A.Channel.CH1)

    current_voltage = args.start
    while current_voltage <= args.stop:
        print(f"Setting to {current_voltage} V")
        device.set_voltage(current_voltage, EDU36311A.Channel.CH1)

        actual_voltage = device.get_voltage(EDU36311A.Channel.CH1)
        print(f"Actual: {actual_voltage} V")
        time.sleep(args.wait)
        current_voltage += args.step

    device.disable_output(EDU36311A.Channel.CH1)
    device.set_voltage(0, EDU36311A.Channel.CH1)