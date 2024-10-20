from typing import Any

from django.core.management.base import BaseCommand

from core.tasks import check_time_logs


class Command(BaseCommand):
    def handle(self, *args: Any, **options: Any):
        check_time_logs()
