from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='version',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('currentVersion', models.CharField(max_length=50)),
                ('build', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='CyberPanelCosmetic',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('MainDashboardCSS', models.TextField(default='')),
            ],
        ),
    ]
