from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "macrv",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.queue.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.queue.tasks.run_pr_review": {"queue": "reviews"},
        "app.queue.tasks.run_security_scan": {"queue": "security"},
    },
    task_default_queue="reviews",
)
