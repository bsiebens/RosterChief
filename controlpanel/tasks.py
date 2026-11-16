from celery import shared_task

from .services.provisioning import run_provisioning


@shared_task(name="controlpanel.provision_club")
def provision_club_task(job_id: str) -> str:
    """Build a club's schema, default plan, domain and owner roster entry for a ClubProvisioning request."""
    job = run_provisioning(job_id)
    return f"{job.status}: {job.name}"


@shared_task(name="controlpanel.hello_world")
def hello_world() -> str:
    """Dummy task for checking that the worker, beat and the control panel's job log work together. Safe to delete."""
    return "Hello, world!"
