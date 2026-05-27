import pytest

from test_helpers.feeder import Feeder


@pytest.fixture()
def feeder():
    # setup
    f = Feeder()
    yield f
    # teardown
    f.stop()
