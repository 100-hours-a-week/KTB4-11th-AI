import os

import pytest
import sqlalchemy as sa

TEST_DSN_ENV = "KTB_TEST_POSTGRES_DSN"


@pytest.fixture(scope="session")
def _span_recorder():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, Sampler

    class SwitchSampler(Sampler):
        on = False

        def should_sample(self, *args, **kwargs):
            return (ALWAYS_ON if self.on else ALWAYS_OFF).should_sample(*args, **kwargs)

        def get_description(self) -> str:
            return "SwitchSampler"

    sampler = SwitchSampler()
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=sampler)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    assert trace.get_tracer_provider() is provider
    return sampler, exporter


@pytest.fixture
def spans(_span_recorder):
    sampler, exporter = _span_recorder
    exporter.clear()
    sampler.on = True
    yield exporter
    sampler.on = False


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    dsn = os.environ.get(TEST_DSN_ENV)
    if not dsn:
        pytest.skip(f"{TEST_DSN_ENV} is not set")
    return dsn


@pytest.fixture(scope="session")
def pg_engine(pg_dsn):
    engine = sa.create_engine(pg_dsn)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_conn(pg_engine):
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        yield conn
        transaction.rollback()
