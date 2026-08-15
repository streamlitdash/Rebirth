"""Gunicorn settings for process-local refresh and progress state."""

import os


# The in-memory snapshot is process-local, so use one worker and several threads.
workers = 1
threads = 4
worker_class = "gthread"
timeout = int(os.getenv("GUNICORN_TIMEOUT_SECONDS", "300"))
graceful_timeout = 30
keepalive = 5
