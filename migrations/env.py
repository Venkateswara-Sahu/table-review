import os

from alembic import context
from sqlalchemy import create_engine

from table_review.jobs import models as job_models  # noqa: F401
from table_review.reviews import models as review_models  # noqa: F401
from table_review.storage.models import Base


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    migrate(connection)
else:
    engine = create_engine(os.environ["TABLE_REVIEW_DATABASE_URL"], hide_parameters=True)
    with engine.connect() as connection:
        migrate(connection)
