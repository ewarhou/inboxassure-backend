# Generated by Django 5.0.2 on 2025-01-25 15:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spamcheck', '0006_alter_userspamcheck_unique_together'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userspamcheck',
            name='status',
            field=models.CharField(choices=[('pending', 'Pending'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('failed', 'Failed')], default='pending', max_length=20),
        ),
    ]
