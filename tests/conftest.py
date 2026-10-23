import pytest

from nas_tasker.dsm import DsmClient

from .mock_dsm import DSM_PASS, DSM_USER, MockDsm


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_dsm():
    return MockDsm()


@pytest.fixture
async def dsm(mock_dsm):
    client = DsmClient("http://dsm.test", DSM_USER, DSM_PASS, transport=mock_dsm.transport())
    yield client
    await client.aclose()
