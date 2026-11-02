import threading
import time
from phone_pilot.harmony.session_lock import get_device_lock, harmony_session_lock


def test_harmony_session_lock_basic():
    serial = "mock_device_serial_1"
    lock1 = get_device_lock(serial)
    lock2 = get_device_lock(serial)
    assert lock1 is lock2

    with harmony_session_lock(serial) as acquired:
        assert acquired is True


def test_harmony_session_lock_concurrency():
    serial = "mock_device_serial_2"
    order = []

    def t1_worker():
        with harmony_session_lock(serial):
            order.append("t1_start")
            time.sleep(0.1)
            order.append("t1_end")

    def t2_worker():
        time.sleep(0.02)
        with harmony_session_lock(serial):
            order.append("t2_done")

    th1 = threading.Thread(target=t1_worker)
    th2 = threading.Thread(target=t2_worker)
    th1.start()
    th2.start()
    th1.join()
    th2.join()

    assert order == ["t1_start", "t1_end", "t2_done"]
