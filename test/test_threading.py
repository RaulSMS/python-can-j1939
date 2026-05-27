import threading
import time

import pytest

import j1939
from test_helpers.feeder import Feeder


def _make_ecu():
    """Return a bare ECU (no CAN bus) via Feeder's mock send path."""
    return j1939.ElectronicControlUnit(send_message=lambda *a, **kw: None)


def test_timer_no_drift():
    ecu = _make_ecu()
    timestamps = []
    done = threading.Event()

    def callback(cookie):
        timestamps.append(time.monotonic())
        if len(timestamps) >= 10:
            done.set()
            return False   # stop rescheduling
        return True        # reschedule

    ecu.add_timer(0.050, callback)
    fired = done.wait(timeout=3.0)
    ecu.stop()

    assert fired, "Timer did not fire 10 times within 3 seconds"
    assert len(timestamps) == 10

    intervals = [timestamps[i+1] - timestamps[i] for i in range(9)]
    for idx, interval in enumerate(intervals):
        assert abs(interval - 0.05) < 0.01, (
            f"Interval {idx} was {interval*1000:.1f}ms, expected ~50ms (±10ms)"
        )


def test_slow_callback_no_protocol_impact(feeder):
    """A slow application timer callback must not delay BAM reassembly."""

    slow_fired = threading.Event()

    def slow_callback(cookie):
        slow_fired.set()
        time.sleep(0.150)   # simulate heavy work
        return True

    feeder.ecu.add_timer(0.020, slow_callback)
    # Wait until the slow callback has fired at least once so it is
    # definitely holding the (old single) job thread during the BAM.
    slow_fired.wait(timeout=1.0)

    # 20-byte BAM: BAM announce + 3 DT frames
    pgn_value = 0xFEC8  # arbitrary broadcast PGN
    src = 0x01
    # Build raw CAN message sequence (same pattern as test_ecu.py)
    can_id_bam = 0x1CECFF01   # TP.CM BAM from 0x01 to global
    can_id_dt  = 0x1CEBFF01   # TP.DT  from 0x01 to global

    feeder.can_messages = [
        (Feeder.MsgType.CANRX, can_id_bam,
         [32, 20, 0, 3, 255, pgn_value & 0xFF, (pgn_value >> 8) & 0xFF, 0], 0.0),
        (Feeder.MsgType.CANRX, can_id_dt,
         [1, 1, 2, 3, 4, 5, 6, 7], 0.0),
        (Feeder.MsgType.CANRX, can_id_dt,
         [2, 8, 9, 10, 11, 12, 13, 14], 0.0),
        (Feeder.MsgType.CANRX, can_id_dt,
         [3, 15, 16, 17, 18, 19, 20, 255], 0.0),
    ]

    received = threading.Event()

    def on_message(priority, pgn, sa, timestamp, data):
        if pgn == pgn_value:
            received.set()

    feeder.ecu.subscribe(on_message)
    feeder.ecu.accept_all_messages = lambda: None  # already set by Feeder init

    ca = feeder.accept_all_messages()
    start = time.monotonic()
    feeder._inject_messages_into_ecu()

    # BAM with 3 DT frames at 50ms inter-frame gap = ~150ms minimum.
    # Allow 400ms — still well under the 150ms slow callback sleeping
    # indefinitely on the old single thread.
    delivered = received.wait(timeout=0.4)
    elapsed = time.monotonic() - start

    feeder.ecu.unsubscribe(on_message)
    feeder.ecu.remove_timer(slow_callback)

    assert delivered, (
        f"BAM message was not reassembled within 400ms (elapsed {elapsed*1000:.0f}ms). "
        "Slow callback may be blocking the protocol thread."
    )


def test_concurrent_add_remove_no_crash():
    ecu = _make_ecu()
    errors = []
    stop = threading.Event()

    def noop(cookie):
        return True

    def hammer():
        try:
            deadline = time.monotonic() + 0.3
            while time.monotonic() < deadline:
                ecu.add_timer(0.01, noop)
                ecu.remove_timer(noop)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)
        assert not t.is_alive(), "Hammer thread deadlocked"

    ecu.stop()

    assert not errors, f"Exceptions during concurrent timer ops: {errors}"


def test_memory_access_event_latency():
    from j1939.memory_access import MemoryAccess, DMState

    ecu = _make_ecu()
    ca = ecu.add_ca(name=j1939.Name(
        arbitrary_address_capable=0,
        industry_group=j1939.Name.IndustryGroup.Industrial,
        vehicle_system_instance=1,
        vehicle_system=1,
        function=1,
        function_instance=1,
        ecu_instance=1,
        manufacturer_code=1,
        identity_number=1,
    ), device_address=0x80)

    ma = MemoryAccess(ca)

    callback_times = []
    set_time = []

    def notify():
        callback_times.append(time.monotonic())

    ma.set_notify(notify)
    ma.state = DMState.WAIT_RESPONSE

    set_time.append(time.monotonic())
    ma._proceed_event.set()

    # Give the servicer thread up to 50ms to respond
    deadline = time.monotonic() + 0.050
    while not callback_times and time.monotonic() < deadline:
        time.sleep(0.001)

    ecu.stop()

    assert callback_times, "notify callback was never called after _proceed_event.set()"
    latency = callback_times[0] - set_time[0]
    assert latency < 0.005, (
        f"MemoryAccess notify latency was {latency*1000:.2f}ms, expected < 5ms"
    )


def test_subscribe_unsubscribe_race(feeder):
    """Concurrent subscribe/unsubscribe while messages arrive must not crash."""
    errors = []
    received_count = [0]
    stop = threading.Event()

    def counting_cb(priority, pgn, sa, timestamp, data):
        received_count[0] += 1

    def subscribe_loop():
        try:
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                feeder.ecu.subscribe(counting_cb)
                time.sleep(0.001)
                feeder.ecu.unsubscribe(counting_cb)
        except Exception as exc:
            errors.append(exc)

    # Keep at least one stable subscriber so messages are delivered
    feeder.ecu.subscribe(counting_cb)

    sub_thread = threading.Thread(target=subscribe_loop)
    sub_thread.start()

    # Inject broadcast messages repeatedly
    can_id = 0x18FEC801  # broadcast from 0x01, PGN 0xFEC8
    inject_deadline = time.monotonic() + 0.5
    while time.monotonic() < inject_deadline:
        feeder.message_queue.put((Feeder.MsgType.CANRX, can_id,
                                  bytearray([1, 2, 3, 4, 5, 6, 7, 8]), 0.0))
        time.sleep(0.01)

    sub_thread.join(timeout=2.0)
    feeder.ecu.unsubscribe(counting_cb)

    assert not errors, f"Exceptions during subscribe/unsubscribe race: {errors}"
    assert received_count[0] > 0, "No messages were received during the race"
