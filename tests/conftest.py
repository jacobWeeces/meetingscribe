import uuid
import pytest


@pytest.fixture
def kc_namespace():
    """A unique Keychain service name so tests never touch real data."""
    return f"MeetingScribeTest-{uuid.uuid4()}"
