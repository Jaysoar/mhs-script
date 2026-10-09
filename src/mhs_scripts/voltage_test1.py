import easy_scpi as scpi 
import time
import argparse

def main() -> None:
    # parser = argparse.ArgumentParser(description="voltage shmoo")

    # parser.add_argument("start", type=float, help="start voltage")
    # parser.add_argument("stop", type=float, help="stop voltage")
    # parser.add_argument("step", type=float, help="step voltage increment")
    # parser.add_argument("wait", type=float, help="wait time [s] in between")
    # args = parser.parse_args()

    ip = "169.254.225.182"
    port = "5025"

    # Define an instrument
    inst = scpi.Instrument(
        port = f"TCPIP::{ip}::{port}::SOCKET",
        port_match=False,  # VISA discovery never lists ::SOCKET resources
        read_termination="\n",
        backend="@py",
    ) 

    # Connect to an instrument
    inst.connect()

    inst.apply("CH1", 0.1, 0.1)                     # APPLY CH1,3.3,0.1  -> set 3.3 V, 0.1 A limit
    inst.output.state("ON", "(@1)")                 # OUTPUT:STATE ON,(@1)
    print(inst.measure.voltage("CH1", query=True))  # MEASURE:VOLTAGE? CH1
    # print(inst.system.error())                      # SYSTEM:ERROR?  -> check the last command worked
    # # Read the voltage [MEASure:VOLTage:DC?]
    # inst.measure.voltage.dc()
    # # or
    # inst.meas.volt.dc()

    # # Passing args to a query [MEASure:VOLTage:DC? MIN]
    # inst.measure.voltage.dc("MIN", query=True)

    # inst.source.voltage('1')
