import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scorecard.models import Base


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
