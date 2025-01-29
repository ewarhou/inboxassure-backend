"""Migration to remove execution state table."""
from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ('spamcheck', '0016_alter_userspamcheck_reports_waiting_time'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                'SET FOREIGN_KEY_CHECKS=0;',
                'DROP TABLE IF EXISTS spamcheck_executionstate;',
                'SET FOREIGN_KEY_CHECKS=1;'
            ],
            reverse_sql='SELECT 1;'
        ),
    ] 