import requests
from datetime import timedelta, date
from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from .models import Project

User = get_user_model()

def send_warning_email(user, days_remaining, stdout=None):
    """
    Sends an inactivity warning notification email via the Mailgun API.
    """
    try:
        domain = getattr(settings, 'MAILGUN_DOMAIN')
        api_key = getattr(settings, 'MAILGUN_API_KEY')
        from_email = getattr(settings, 'MAILGUN_FROM_EMAIL')

        url = f"https://api.mailgun.net/v3/{domain}/messages"
        subject = "[DNA Subway 2.0] Important: Inactivity warning for your projects"
        time_frame = f"{days_remaining} days" if days_remaining > 1 else "1 day"
        if days_remaining == 7:
            time_frame = "1 week"

        text = (
            f"Hello {user.username},\n\n"
            f"We noticed you haven't logged into DNA Subway 2.0 recently. To optimize our server storage, "
            f"projects belonging to inactive accounts are automatically scheduled for maintenance.\n\n"
            f"You have {time_frame} remaining to log in before your projects and associated files are marked for deletion. Your account will still be available, but you will lose all projects and data.\n"
            f"Simply log into your account to reset this timer.\n\n"
            f"Best regards,\nThe DNA Subway Team"
        )

        html = (
            f"<p>Hello <strong>{user.username}</strong>,</p>"
            f"<p>We noticed you haven't logged into DNA Subway 2.0 recently. To optimize our server storage, "
            f"projects belonging to inactive accounts are automatically scheduled for maintenance.</p>"
            f"<p>You have <strong>{time_frame}</strong> remaining to log in before your projects and associated files "
            f"are marked for deletion. Your account will still be available, but you will lose all projects and data.</p>"
            f"<p>Simply log into your account to reset this timer.</p>"
            f"<p>Best regards,<br>The DNA Subway Team</p>"
        )

        data = {
            'from': from_email,
            'to': user.email,
            'subject': subject,
            'text': text,
            'html': html
        }

        response = requests.post(url, auth=('api', api_key), data=data)

        if response.status_code != 200:
            if stdout:
                stdout.write(f"Failed to send inactivity email to {user.email}: {response.text}\n")
            return False
    except Exception as e:
        if stdout:
            stdout.write(f"Error sending inactivity email to {user.email}: {e}\n")
        return False

    return True

def mark_inactive_user_projects_deleted(dry_run=True, days=180, current_date=None, stdout=None):
    # Track the actual calendar day for accurate email notifications
    real_today = timezone.localdate()

    # Handle the simulation date parameter for cohort matching
    if current_date is None:
        today = real_today
    elif isinstance(current_date, str):
        today = datetime.strptime(current_date, "%Y-%m-%d").date()
    else:
        today = current_date
    # Policy Enforcement Rollout Date
    POLICY_START_DATE = date(2026, 6, 5)
    days_since_start = (today - POLICY_START_DATE).days

    base_users = User.objects.exclude(username__startswith="guest_").exclude(
        is_staff=True
    ).exclude(is_superuser=True)

    # schedules setup: (inactive_days, days_left, bulk_catchup_trigger_day)
    schedules = [(150, 30, 0), (173, 7, 23), (179, 1, 29)]

    if stdout:
        action = "Listing" if dry_run else "Processing"
        stdout.write(f"--- {action} inactive registered user notifications and project cleanup ---\n")
        stdout.write(f"    [Simulation Date: {today} | Real-World Date: {real_today}]\n\n")

    # --------------------------------------------------
    # STEP 1 & 2: Process Notifications (Rolling + Bulk Legacy Catch-up)
    # --------------------------------------------------
    for inactive_days, _, legacy_trigger_day in schedules:
        threshold_date = today - timedelta(days=inactive_days)
        
        # 1. Rolling Cohort Selection based on the simulation timeline
        users_to_notify = base_users.filter(last_login__date=threshold_date)
        
        # 2. Legacy Cohort Catch-up based on the simulation timeline
        if days_since_start == legacy_trigger_day:
            legacy_users = base_users.filter(last_login__date__lt=POLICY_START_DATE)
            users_to_notify |= legacy_users.filter(last_login__date__lte=threshold_date)
        users_to_notify = users_to_notify.distinct()

        for user in users_to_notify.iterator():
            if not user.email:
                continue
            # --- DYNAMIC RE-CALCULATION BASED ON THE REAL TODAY ---
            # Standard deletion timeline is 180 days after last login
            standard_deletion_date = user.last_login.date() + timedelta(days=days)
            # Safety rule: No legacy projects are deleted before Day 30 of the rollout
            earliest_deletion_date = POLICY_START_DATE + timedelta(days=30)
            
            actual_deletion_date = max(standard_deletion_date, earliest_deletion_date)
            
            # Get remaining days relative to the REAL world calendar day
            dynamic_days_left = (actual_deletion_date - real_today).days

            # Safeguard: If the script is run late and they are past threshold, default to 1 day notice
            if dynamic_days_left < 1:
                dynamic_days_left = 1

            if dry_run:
                if stdout:
                    stdout.write(f"[DRY RUN] Would send {dynamic_days_left}-day warning email to {user.email} (Last login: {user.last_login.date()})\n")
            else:
                success = send_warning_email(user, dynamic_days_left, stdout=stdout)
                if success and stdout:
                    stdout.write(f"Sent {dynamic_days_left}-day warning email to {user.email}\n")

    # --------------------------------------------------
    # STEP 3: Handle Project Soft-Deletions (180+ Days)
    # --------------------------------------------------
    if days_since_start >= 30:
        cutoff_date = today - timedelta(days=days)
        eligible = base_users.filter(last_login__date__lte=cutoff_date)
        
        projects_to_update = Project.objects.filter(
            user__in=eligible,
            deleted=False
        )

        if dry_run:
            total_users = eligible.count()
            total_projects = projects_to_update.count()
            if stdout:
                stdout.write(f"\n[DRY RUN] Found {total_users} users past deletion threshold.\n")
                stdout.write(f"[DRY RUN] Would mark {total_projects} active projects as deleted.\n")
        else:
            updated_count = projects_to_update.update(deleted=True)
            if stdout:
                stdout.write(f"\nSuccessfully marked {updated_count} projects as deleted.\n")
