from typing import NamedTuple


class J1939Message(NamedTuple):
    """A received J1939 message, passed to single-argument subscriber callbacks.

    Also unpacks positionally like the legacy 5-tuple
    ``(priority, pgn, source_address, timestamp, data)`` for callbacks that
    don't need ``dest_address``, since it's appended last.

    :ivar int priority:
        Priority of the message.
    :ivar int pgn:
        Parameter Group Number of the message.
    :ivar int source_address:
        Source address of the message.
    :ivar int timestamp:
        Timestamp of the CAN message.
    :ivar bytearray data:
        Data of the PDU.
    :ivar int dest_address:
        Destination address of the message. ``ParameterGroupNumber.Address.GLOBAL``
        for broadcast (PDU2) messages.
    """

    priority: int
    pgn: int
    source_address: int
    timestamp: int
    data: bytearray
    dest_address: int
