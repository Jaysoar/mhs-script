
from pymeasure.instruments.keysight import KeysightEDU36311A

PSU_IP = "169.254.168.144"

def main() -> None:
    psu = KeysightEDU36311A(f"TCPIP::{PSU_IP}::5025::SOCKET")
    psu.channels[1].voltage_setpoint = 6.0