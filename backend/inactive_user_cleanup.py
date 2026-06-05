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

def mark_inactive_user_projects_deleted(dry_run=True, days=180, stdout=None):
    today = timezone.localdate()

    # Policy Enforcement Rollout Date
    POLICY_START_DATE = date(2026, 6, 5)

    if stdout:
        action = "Listing" if dry_run else "Processing"
        stdout.write(f"--- {action} inactive registered user notifications and project cleanup ---\n")

    # Target registered users only (exclude guests, staff, and superusers)
    base_users = User.objects.exclude(
        username__startswith="guest_"
    ).exclude(
        is_staff=True
    ).exclude(
        is_superuser=True
    )

    # --------------------------------------------------
    # STEP 1: Identify Notification Groups
    # --------------------------------------------------
    
    # Tier A: 30 Days Remaining (150 Days Inactive)
    # Rollout rule: On Day 1, catch everyone who is ALREADY past the 150-day mark.
    if today == POLICY_START_DATE:
        warn_30_users = base_users.filter(
            Q(last_login__date=today - timedelta(days=150)) |
            Q(last_login__date__lt=POLICY_START_DATE - timedelta(days=150))
        )
    else:
        warn_30_users = base_users.filter(last_login__date=today - timedelta(days=150))

    # Tier B: 7 Days Remaining (173 Days Inactive)
    # Rollout rule: Catch legacy users exactly 23 days into the policy.
    if today == POLICY_START_DATE + timedelta(days=23):
        warn_7_users = base_users.filter(
            Q(last_login__date=today - timedelta(days=173)) |
            Q(last_login__date__lt=POLICY_START_DATE - timedelta(days=150))
        )
    else:
        warn_7_users = base_users.filter(last_login__date=today - timedelta(days=173))

    # Tier C: 1 Day Remaining (179 Days Inactive)
    # Rollout rule: Catch legacy users exactly 29 days into the policy.
    if today == POLICY_START_DATE + timedelta(days=29):
        warn_1_users = base_users.filter(
            Q(last_login__date=today - timedelta(days=179)) |
            Q(last_login__date__lt=POLICY_START_DATE - timedelta(days=150))
        )
    else:
        warn_1_users = base_users.filter(last_login__date=today - timedelta(days=179))

    # --------------------------------------------------
    # STEP 2: Dispatch Emails
    # --------------------------------------------------
    notification_tiers = [
        (warn_30_users, 30),
        (warn_7_users, 7),
        (warn_1_users, 1)
    ]

    for user_queryset, days_left in notification_tiers:
        for user in user_queryset:
            if not user.email:
                continue

            if dry_run:
                if stdout:
                    stdout.write(f"[DRY RUN] Would send {days_left}-day warning email to {user.email} (Last login: {user.last_login.date()})\n")
            else:
                success = send_warning_email(user, days_left, stdout=stdout)
                if success and stdout:
                    stdout.write(f"Sent {days_left}-day warning email to {user.email}\n")

    # --------------------------------------------------
    # STEP 3: Handle Project Soft-Deletions (180+ Days)
    # --------------------------------------------------
    cutoff_date = today - timedelta(days=days)

    eligible_users = base_users.filter(
        last_login__date__lt=cutoff_date
    )

    # Apply the 30-day rollout leeway rule:
    # If we are within the initial 30 days of enforcement, completely protect
    # users whose last login was prior to today's rollout date.
    if today < POLICY_START_DATE + timedelta(days=30):
        eligible_users = eligible_users.exclude(last_login__date__lt=POLICY_START_DATE)

    projects_to_update = Project.objects.filter(
        user__in=eligible_users,
        deleted=False
    )

    if dry_run:
        total_users = eligible_users.count()
        total_projects = projects_to_update.count()
        if stdout:
            stdout.write(f"[DRY RUN] Found {total_users} users past deletion threshold (excluding protected legacy users).\n")
            stdout.write(f"[DRY RUN] Would mark {total_projects} active projects as deleted.\n")
    else:
        updated_count = projects_to_update.update(deleted=True)
        if stdout:
            stdout.write(f"Successfully marked {updated_count} projects as deleted.\n")
