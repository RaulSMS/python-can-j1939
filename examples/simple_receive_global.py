import logging
import time

import j1939

logging.getLogger('j1939').setLevel(logging.DEBUG)
logging.getLogger('can').setLevel(logging.DEBUG)

def on_message(msg: j1939.J1939Message):
    """Receive incoming messages from the bus"""
    print(f"PGN {msg.pgn} from {msg.source_address:#04x} length {len(msg.data)}")

def main():
    print("Initializing")

    # create the ElectronicControlUnit (one ECU can hold multiple ControllerApplications)
    ecu = j1939.ElectronicControlUnit()

    # Connect to the CAN bus
    # Arguments are passed to python-can's can.interface.Bus() constructor
    # (see https://python-can.readthedocs.io/en/stable/bus.html).
    # ecu.connect(interface='socketcan', channel='can0')
    # ecu.connect(interface='kvaser', channel=0, bitrate=250000)
    ecu.connect(interface='pcan', channel='PCAN_USBBUS1', bitrate=250000)
    # ecu.connect(interface='ixxat', channel=0, bitrate=250000)
    # ecu.connect(interface='vector', app_name='CANalyzer', channel=0, bitrate=250000)
    # ecu.connect(interface='nican', channel='CAN0', bitrate=250000)    

    # subscribe to all (global) messages on the bus
    ecu.subscribe(on_message)

    time.sleep(120)

    print("Deinitializing")
    ecu.disconnect()

if __name__ == '__main__':
    main()
