# Generated for TestPortal — adds image support to questions/options.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0002_aisettings_aigenerationlog'),
    ]

    operations = [
        migrations.AddField(
            model_name='question',
            name='image',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='option',
            name='image',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='option',
            name='text',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
