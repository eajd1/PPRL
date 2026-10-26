from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def connect(settings):
    engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True,
                           connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})
    return engine, sessionmaker(engine, expire_on_commit=False)
