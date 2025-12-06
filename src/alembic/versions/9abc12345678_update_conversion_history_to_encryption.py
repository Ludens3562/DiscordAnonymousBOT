"""update conversion_history to use encryption

Revision ID: 9abc12345678
Revises: 8fb7bc527ddd
Create Date: 2025-12-06 20:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9abc12345678'
down_revision: Union[str, Sequence[str], None] = '8fb7bc527ddd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - rename user_id_signature to user_id_encrypted and add encryption_salt."""
    # Rename column and change type
    op.alter_column('conversion_history', 'user_id_signature',
                    new_column_name='user_id_encrypted',
                    type_=sa.String(512),
                    existing_type=sa.String(128),
                    existing_nullable=False)
    
    # Add encryption_salt column as nullable first
    op.add_column('conversion_history', sa.Column('encryption_salt', sa.Text(), nullable=True))
    
    # Note: Existing records will have NULL encryption_salt, which means they cannot be decrypted.
    # This is acceptable as ConversionHistory is mainly for audit purposes and is not decrypted in the current implementation.
    # Future records will have proper encryption_salt values.


def downgrade() -> None:
    """Downgrade schema - remove encryption_salt and rename user_id_encrypted back to user_id_signature."""
    # Remove encryption_salt column
    op.drop_column('conversion_history', 'encryption_salt')
    
    # Rename column back and change type
    op.alter_column('conversion_history', 'user_id_encrypted',
                    new_column_name='user_id_signature',
                    type_=sa.String(128),
                    existing_type=sa.String(512),
                    existing_nullable=False)
