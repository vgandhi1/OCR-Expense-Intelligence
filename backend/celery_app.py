import os

from celery import Celery

_redis = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery(
    "ocr_expense",
    broker=_redis,
    backend=_redis,
)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    include=["tasks"],
)
