"""Freeze queued task identity and captured export input."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE FUNCTION reject_task_input_update() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.input IS DISTINCT FROM OLD.input OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
         OR NEW.document_id IS DISTINCT FROM OLD.document_id OR NEW.kind IS DISTINCT FROM OLD.kind
         OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key THEN
        RAISE EXCEPTION 'Task request is immutable';
      END IF;
      RETURN NEW;
    END; $$""")
    op.execute(
        "CREATE TRIGGER immutable_task_input BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION reject_task_input_update()"
    )


def downgrade():
    op.execute("DROP TRIGGER immutable_task_input ON tasks")
    op.execute("DROP FUNCTION reject_task_input_update()")
