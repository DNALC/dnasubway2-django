from django.core.management.base import BaseCommand
from backend.inactive_user_cleanup import mark_inactive_user_projects_deleted

class Command(BaseCommand):
    help = (
        "Mark projects as deleted for registered users who haven't logged in "
        "for over 6 months (180 days). In dry-run mode (default), only list what would happen."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only show how many projects would be marked, without modifying the database.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=180,
            help="Number of days of inactivity to trigger deletion (default: 180).",
        )
        parser.add_argument(
            "--current-date",
            type=str,
            default=None,
            help="Simulate running the script on a specific date (Format: YYYY-MM-DD).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        days = options["days"]
        current_date = options["current_date"]
        
        mark_inactive_user_projects_deleted(
            dry_run=dry_run, 
            days=days, 
            current_date=current_date,
            stdout=self.stdout
        )
