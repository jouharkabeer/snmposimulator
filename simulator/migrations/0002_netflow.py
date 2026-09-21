from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("simulator", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="netflow_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="simulationstate",
            name="netflow_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="simulationstate",
            name="netflow_version",
            field=models.PositiveSmallIntegerField(default=9),
        ),
        migrations.AddField(
            model_name="simulationstate",
            name="netflow_collector_host",
            field=models.CharField(default="host.docker.internal", max_length=255),
        ),
        migrations.AddField(
            model_name="simulationstate",
            name="netflow_collector_port",
            field=models.PositiveIntegerField(default=2055),
        ),
        migrations.AddField(
            model_name="simulationstate",
            name="netflow_packets_sent",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="simulationstate",
            name="netflow_last_error",
            field=models.TextField(blank=True, default=""),
        ),
    ]
