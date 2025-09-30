from hera.workflows import (
    DAG,
    WorkflowTemplate,
    script,
    Task,
    Container,
)
from hera.shared import global_config
from hera.workflows.models import Toleration, Arguments, Parameter, ImagePullPolicy
import os


@script()
def compute_snapshots_configurations(unit: str, time_delta: int, start_date: str, end_date: str):
    """
    Computes the configurations for timestamp snapshots.
    This function takes a unit (e.g., days, hours, minutes) a time delta (e.g., 1, 2, 3) and a start and end date.
    It generates a list of snapshot between the start and end date based on the unit and time delta.
    """
    import json
    import sys
    from datetime import datetime, timedelta

    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    snapshots = []
    current = start
    while current <= end:
        snapshots.append({"snapshot": current.isoformat()})
        current += timedelta(**{unit: time_delta})
    json.dump(snapshots, sys.stdout)


if __name__ == "__main__":
    global_config.host = f'https://{os.environ.get("ARGO_SERVER")}'
    global_config.token = os.environ.get("ARGO_TOKEN")
    global_config.namespace = os.environ.get("ARGO_NAMESPACE", "argo")

    with WorkflowTemplate(
        name="pysunlight-dag",
        entrypoint="pysunlight-dag",
        tolerations=[Toleration(
            key="gpu", operator="Exists", effect="PreferNoSchedule")], # If you want to schedule on GPU nodes
        arguments=Arguments(parameters=[
            Parameter(name="unit", description="Unit of time", default="hours", enum=["seconds", "minutes", "hours", "days"]),
            Parameter(name="time_delta", description="Delta inside the unit", default="1"),
            Parameter(name="start_date", description="Start date for the snapshots"),
            Parameter(name="end_date", description="End date for the snapshots"),
        ]),
    ) as wt:
        pysunlight = Container(
            name="pysunlight",
            image="sunlight/pysunlight-docker-pysunlight",
            inputs=[
                Parameter(name="pysunlight"),
            ],
            image_pull_policy=ImagePullPolicy.if_not_present,
            args=["--snapshot", "{{inputs.parameters.snapshot}}"],
        )

        with DAG(name="benchmark-dag"):
            task_compute_snapshots_configurations = compute_snapshots_configurations(
                arguments={"unit": "{{workflow.parameters.unit}}",
                           "time_delta": "{{workflow.parameters.time_delta}}",
                           "start_date": "{{workflow.parameters.start_date}}",
                           "end_date": "{{workflow.parameters.end_date}}"
                       }
            )

            task_pysunlight = Task(
                name="pysunlight",
                template=pysunlight,
                arguments=Arguments(
                    parameters=[Parameter(name="snapshot", value="{{item.snapshot}}")],
                ),
                with_param=task_compute_snapshots_configurations.result
            )

            task_compute_snapshots_configurations >> task_pysunlight
        wt.create()