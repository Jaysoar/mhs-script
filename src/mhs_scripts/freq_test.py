
from pymeasure.instruments.keysight import KeysightEDU36311A

WAVEGEN_IP = "169.254.x.x" # replace with actual IP address

def main() -> None:
    wavegen = KeysightEDU36311A(f"TCPIP::{WAVEGEN_IP}::5025::SOCKET")
    wavegen.ch_1.voltage_setpoint = 1.0
    wavegen.ch_1.output_enabled = True
    print(wavegen.ch_1.voltage)