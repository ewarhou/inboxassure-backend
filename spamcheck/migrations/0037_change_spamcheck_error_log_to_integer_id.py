from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('spamcheck', '0036_add_account_selection_fields'),
    ]

    operations = [
        migrations.RunSQL(
            """
            -- Truncate the existing table (removing all data)
            TRUNCATE TABLE spamcheck_error_logs;
            
            -- Change the ID column from UUID to auto-incrementing integer
            ALTER TABLE spamcheck_error_logs 
            DROP PRIMARY KEY,
            MODIFY COLUMN id BIGINT AUTO_INCREMENT PRIMARY KEY;
            
            -- Reset the auto increment to start at 1
            ALTER TABLE spamcheck_error_logs AUTO_INCREMENT = 1;
            """,
            # Reverse SQL (if migration is reversed)
            """
            -- This would be complex to reverse, but we can at least
            -- try to convert the ID back to UUID
            ALTER TABLE spamcheck_error_logs 
            DROP PRIMARY KEY,
            MODIFY COLUMN id CHAR(36) PRIMARY KEY;
            """
        ),
    ] 