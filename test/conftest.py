import threading

import pytest

from test.helpers.feeder import Feeder


@pytest.fixture()
def feeder():
    # setup
    f = Feeder()
    try:
        yield f
    finally:
        # teardown — guarantee cleanup even if the test raises
        try:
            f.stop()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _assert_no_j1939_thread_leak():
    """Fail any test that leaves a j1939.* background thread alive.

    The poll window here (3.5s) is deliberately kept slightly above
    ElectronicControlUnit.stop()'s own default dispatch_join_timeout (3.0s):
    stop() only logs a warning (rather than failing) if the dispatch thread
    doesn't exit within that timeout, e.g. while a slow subscriber callback
    unwinds. If this fixture's window were shorter than stop()'s own
    tolerance, a thread that stop() itself considers "still fine, just slow"
    could trip a false-positive leak failure here. See #81.
    """
    before = {t.ident for t in threading.enumerate()
              if t.name.startswith('j1939.')}
    yield
    # Give freshly-stopped threads a chance to actually exit.
    import time
    deadline = time.monotonic() + 3.5
    while True:
        leaked = [t for t in threading.enumerate()
                  if t.name.startswith('j1939.')
                  and t.ident not in before
                  and t.is_alive()]
        if not leaked or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    assert not leaked, (
        "Test leaked j1939 background thread(s): "
        + ", ".join(t.name for t in leaked)
    )
