from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('spamcheck', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userspamcheckreport',
            name='is_good',
            field=models.BooleanField(default=False, help_text='Whether this account meets the spamcheck conditions'),
        ),
    ] 
