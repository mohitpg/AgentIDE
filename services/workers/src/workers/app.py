"""
Celery application factory.

Broker  : RabbitMQ  (reliable delivery, priority queues, DLQ)
Backend : Redis     (fast result store + pub/sub for progress events)

Queues:
  default   – miscellaneous short tasks
  indexing  – repo / doc ingestion (chunk → embed → upsert)
  web       – page fetching and crawling

Run with:
  celery -A workers.app:celery_app worker --loglevel=info -Q default,indexing,web

Scheduled jobs (Celery Beat) run alongside:
  celery -A workers.app:celery_app beat --loglevel=info
"""

from __future__ import annotations

import os

from celery import Celery
from kombu import Exchange, Queue

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://agentide:agentide@localhost:5672/")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "agentide",
    broker=RABBITMQ_URL,
    backend=REDIS_URL,
    include=[
        "workers.tasks.mock",
        "workers.tasks.maintenance",
        "workers.tasks.indexing",
        "workers.tasks.web",
    ],
)

# ── Queue definitions ────────────────────────────────────────────────────────
default_exchange = Exchange("agentide", type="direct")

celery_app.conf.task_queues = (
    Queue("default", default_exchange, routing_key="default"),
    Queue("indexing", default_exchange, routing_key="indexing"),
    Queue("web", default_exchange, routing_key="web"),
)
celery_app.conf.task_default_queue = "default"
celery_app.conf.task_default_exchange = "agentide"
celery_app.conf.task_default_routing_key = "default"

# ── Routing rules ────────────────────────────────────────────────────────────
celery_app.conf.task_routes = {
    "workers.tasks.indexing.*": {"queue": "indexing"},
    "workers.tasks.web.*": {"queue": "web"},
}

# ── Serialization & misc ─────────────────────────────────────────────────────
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,  # ack only on success/failure, not on receive
    worker_prefetch_multiplier=1,  # fair dispatch for long tasks
    result_expires=3600,  # results live 1 h in Redis
)

# ── Scheduled jobs (Celery Beat) ──────────────────────────────────────────────
celery_app.conf.beat_schedule = {
    "registry-health-check": {
        "task": "workers.tasks.maintenance.registry_health_check",
        "schedule": 60.0,  # every 60 s
        "options": {"queue": "default"},
    },
}

