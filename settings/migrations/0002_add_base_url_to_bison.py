from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userbison',
            name='base_url',
            field=models.CharField(max_length=255, default='https://app.orbitmailboost.com'),
        ),
    ] 