from django.core.management.base import BaseCommand
from backend.cleanup_deleted_projects import cleanup_deleted_project_files


class Command(BaseCommand):
    help = (
        "Clean up files for deleted projects, including guest users’ projects. "
        "Guest projects created today are excluded from deletion marking. "
        "In dry-run mode (default), only list files."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only show what would be deleted, without deleting anything",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        cleanup_deleted_project_files(dry_run=dry_run, stdout=self.stdout, stderr=self.stderr)

