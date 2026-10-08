import pytest

from app.schema_loader import SchemaInfo, get_schema


@pytest.fixture(scope="session")
def schema() -> SchemaInfo:
    return get_schema()
