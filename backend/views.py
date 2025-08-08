from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import HttpResponse, Http404, JsonResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
import io
import json
import os
import subprocess
import random
import re
import requests
import string
import tempfile
import time
#from django.shortcuts import render
from .models import UserProfile, Ethnicity, EmailVerifyToken, PasswordResetToken, Project, DataFile, ProjectDataFile, NanoporeSampleSet, NanoporeSequence, ProjectNanoporeSequence, FastpJob, FastpResult, PorechopJob, PorechopResult, MedakaJob, MedakaResult, BlastJob, BlastResult, BlastData, MuscleJob, MuscleData, MuscleSimilarity, PhylipNJJob, PhylipNJData, PhylipMLJob, PhylipMLData, ReferenceData, SampleData, ConsensusData, ProjectBlastDone, EnhancedPermissionToken, PodFile, BasecallingJob, Job, UserNanoporeSequence
from .utils import parse_reads, cleanSequenceName, sequence_trim, blast, muscle, phylip_ml, phylip_nj, consense, multi_seq_muscle_jobs, job_status_check, local_sequence_trim, suggested_trim, undo_sequence_trim, local_consense, local_blast, local_muscle, local_phylip_nj, local_phylip_ml, get_quality_scores, is_low_quality, is_text_file, extract_genbank_data, extract_sequences, ensure_instance_ready, get_service_token, generate_user_token, connect_to_tapis
import gzip
import shutil
from .tasks import run_fastp_task, run_porechop_task, run_medaka_task  # Celery task
from Bio import SeqIO
import base64
from .models import Author, MuscleTrim
from tapipy.tapis import Tapis

PROTOCOL = getattr(settings, 'PROTOCOL') or "https://"

# Return dictionary with error if not POST, otherwise return False
def not_post(request):
    if request.method != 'POST':
        return {'error': 'Method not allowed', 'status': 405}
    return False

# Return dictionary with error if user not authenticated, otherwise return False
def not_authenticated(request):
    if not request.user.is_authenticated:
        return {'error': 'User not authenticated', 'status': 401}
    return False

# Return request body parsed as JSON data, otherwise return False
def load_json_data(request):
    try:
        data = json.loads(request.body)
        return data
    except json.JSONDecodeError:
        return False

# Return project with given id, otherwise return False
def get_project(pid):
    try:
        project = Project.objects.get(id=pid)
        return project
    except Project.DoesNotExist:
        return False

# Return dictionary with error if project does not belong to current user, otherwise return False
def not_user_project(project, request):
    if project.user != request.user:
        return {'error': 'You do not have permission to edit this project.', 'status': 403}
    return False

# Return dictionary with error if not post request or request body cannot be parsed as JSON,
# otherwise return dictionary with request body parsed as JSON data
def parse_data(request):
    is_not_post = not_post(request)
    if is_not_post:
        return is_not_post

    data = load_json_data(request)
    if not data:
        return {'error': 'Invalid JSON data', 'status': 400}


    return {'data': data}

def is_logged_in_post(request):
    if request.method != 'POST':
        return {'error': 'Method not allowed', 'status': 405}
    if not request.user:
        return {'error': 'User does not exist', 'status': 401}
    if not request.user.is_authenticated:
        return {'error': 'User not authenticated', 'status': 401}
    return {'status': 'success'}

# Return dictionary with error if not post request or request body cannot be parsed as JSON or user not authenticated,
# otherwise return dictionary with request body parsed as JSON data
def parse_user_data(request):
    parsed_data = parse_data(request)
    if 'error' in parsed_data:
        return {'error': parsed_data['error'], 'status': parsed_data['status']}
    is_not_authenticated = not_authenticated(request)
    data = parsed_data['data']

    if is_not_authenticated:
        return is_not_authenticated

    return {'data': data}

# Return dictionary with error if not post request or request body cannot be parsed as JSON or user not authenticated
# or project does not belong to current user, otherwise return dictionary with request body parsed as JSON data
def parse_user_project_data(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return {'error': parsed_data['error'], 'status': parsed_data['status']}

    data = parsed_data['data']
    pid = data.get('pid')

    project = get_project(pid)
    if not project:
        return {'error': 'Project not found.', 'status': 404}

    if project.deleted:
        return {'error': 'Project deleted.', 'status': 404}

    is_not_user_project = not_user_project(project, request)
    if is_not_user_project:
        return is_not_user_project

    # If everything is fine, return the data and project
    return {'data': data, 'project': project}

# If there is a CSRF failure, return a JSON response saying to redirect to the home page.
# This will occur if a user logs out in a different tab or browser window.
def csrf_failure(request, reason=""):
    return JsonResponse({'error': 'CSRF failed', 'redirect': '/'}, status=403)


@csrf_exempt
def registered_users(request):
    users = User.objects.exclude(username__startswith='guest_')

    usernames = users.values_list('username', flat=True)
    emails = users.filter(~Q(email=''), email__isnull=False).values_list('email', flat=True)

    return JsonResponse({
        'usernames': list(usernames),
        'emails': list(emails),
    })

@csrf_exempt
def user_registered(request):
    username = request.GET.get('username')
    email = request.GET.get('email')

    if username and email:
        exists = User.objects.filter(username=username, email=email).exists()
    elif username:
        exists = User.objects.filter(username=username).exists()
    elif email:
        exists = User.objects.filter(email=email).exists()
    else:
        return JsonResponse({'error': 'username or email parameter is required'}, status=400)

    return JsonResponse({'registered': exists})

# This is the endpoint for user registration
@csrf_exempt
def register(request):
    # POST JSON data
    parsed_data = parse_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']

    # Extract required fields from JSON data
    first_name = data.get('first')
    last_name = data.get('last')
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    verify = data.get('verify')
    country = data.get('country')
    zip_code = data.get('zip')
    gender = data.get('gender')
    occupation = data.get('occupation')
    source = data.get('source')
    ethnicity_str = data.get('ethnicity')
    # Ethnicity is a multi-select; make it always be an array, even if only one option was chosen
    if isinstance(ethnicity_str, str):
        ethnicity_list = [ethnicity_str]
    else:
        ethnicity_list = data.get('ethnicity', [])

    # Check if password and verify match
    if password != verify:
        return JsonResponse({'error': 'Passwords do not match'}, status=400)

    # Check if all fields have values
    if not all([first_name, last_name, username, email, password, country, zip_code, gender, occupation, source, ethnicity_list]):
        return JsonResponse({'error': 'All fields are required'}, status=400)

    # You cannot use the same username as someone else
    if User.objects.filter(username=username).exists():
        return JsonResponse({'error': 'User with this username already exists'}, status=400)

    # You cannot use the same email as someone else
    if User.objects.filter(email=email).exists():
        return JsonResponse({'error': 'User with this email already exists'}, status=400)

    # Check if zip_code is longer than 12 characters
    if len(zip_code) > 12:
        return JsonResponse({'error': 'Postal code must be no longer than 12 characters'}, status=400)

    # Check if country is longer than 2 characters (it's an option with max 2 characters)
    if len(country) > 2:
        return JsonResponse({'error': 'Country must be no longer than 2 characters'}, status=400)

    # Check if username is longer than 150 characters
    if len(username) > 150:
        return JsonResponse({'error': 'Username must be no longer than 150 characters'}, status=400)

    # Check if first_name is longer than 150 characters
    if len(first_name) > 150:
        return JsonResponse({'error': 'First name must be no longer than 150 characters'}, status=400)

    # Check if last_name is longer than 150 characters
    if len(last_name) > 150:
        return JsonResponse({'error': 'Last name must be no longer than 150 characters'}, status=400)

    # Check if email is longer than 254 characters
    if len(email) > 254:
        return JsonResponse({'error': 'Email must be no longer than 254 characters'}, status=400)

    # Check if username contains only allowed characters
    if not re.match(r'^[\w.@+-]+$', username):
        return JsonResponse({'error': 'Username can only contain alphanumeric, _, @, +, . and - characters'}, status=400)

    # Check if gender is a key in GENDER_CHOICES
    if gender not in [choice[0] for choice in UserProfile.GENDER_CHOICES]:
        return JsonResponse({'error': 'Invalid gender value'}, status=400)

    # Check if occupation is a key in OCCUPATION_CHOICES
    if occupation not in [choice[0] for choice in UserProfile.OCCUPATION_CHOICES]:
        return JsonResponse({'error': 'Invalid occupation value'}, status=400)

    # Check if source is a key in SOURCE_CHOICES
    if source not in [choice[0] for choice in UserProfile.SOURCE_CHOICES]:
        return JsonResponse({'error': 'Invalid source value'}, status=400)

    # Create User instance
    user = User.objects.create(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        password=make_password(password)  # Hashing the password
    )

    # Create Ethnicity instance based on the provided list
    ethnicity = Ethnicity.objects.create(
        native='na' in ethnicity_list,
        asian='as' in ethnicity_list,
        black='aa' in ethnicity_list,
        hispanic='hs' in ethnicity_list,
        middle='me' in ethnicity_list,
        pacific='pi' in ethnicity_list,
        white='wh' in ethnicity_list,
        other='oo' in ethnicity_list,
        unanswered='pn' in ethnicity_list
    )

    # Create UserProfile instance
    profile = UserProfile.objects.create(
        user=user,
        country=country,
        postal_code=zip_code,
        gender=gender,
        occupation=occupation,
        source=source,
        ethnicity=ethnicity
    )
    auth_user = authenticate(request, username=username, password=password)
    if auth_user is not None:
        login(request, auth_user)
        # Generate and return session token
        session_token = request.session.session_key or request.session.create()  # Ensure session is created

    return JsonResponse({'success': 'User registered successfully', 'redirect': '/'}, status=201)

# This is the endpoint for user login
@csrf_exempt
def login_view(request):
    # POST JSON data
    parsed_data = parse_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    # Extract fields from JSON data
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    if password and (username or email):
        user = None

        if username:
            # Authenticate with username
            user = authenticate(request, username=username, password=password)
        elif email:
            # Check if the provided email is associated with a user
            try:
                user_obj = User.objects.get(email=email)
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                return JsonResponse({'error': 'Invalid email or password'}, status=401)

        if user is not None:
            login(request, user)
            # Generate and return session token
            session_token = request.session.session_key or request.session.create()  # Ensure session is created
            return JsonResponse({'session_token': session_token, 'redirect': '/dashboard'}, status=200)
        else:
            return JsonResponse({'error': 'Invalid email or password'}, status=401)
    else:
        return JsonResponse({'error': 'Username/email and password are both required'}, status=400)

def get_csrf_token(request):
    csrf_token = get_token(request)
    return JsonResponse({'csrfToken': csrf_token})

@csrf_exempt
def request_password_reset(request):
    # POST JSON data
    parsed_data = parse_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    username = data.get('username')
    email = data.get('email')

    if not username and not email:
        return JsonResponse({'error': 'Username or email is required.'}, status=400)

    try:
        if username and email:
            user = User.objects.get(username=username, email=email)
        elif username:
            user = User.objects.get(username=username)
        elif email:
            user = User.objects.get(email=email)
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)
    if not email:
        email = user.email

    # Create password reset token
    reset_token = PasswordResetToken.create_token(user)

    # Send email with reset token
    if not send_password_reset_email(email, reset_token.token):
        return JsonResponse({'error': 'Failed to send password reset email'}, status=500)

    return JsonResponse({'success': 'Password reset email sent'}, status=200)

def verify_email(request):
    parsed_data = is_logged_in_post(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    user = request.user
    email = user.email

    verify_token = EmailVerifyToken.create_token(user)

    if not send_verification_email(email, PROTOCOL + request.get_host() + "/backend/verify/" + verify_token.token + "/"):
        return JsonResponse({'error': 'Failed to send verification email'}, status=500)
    return JsonResponse({'success': 'Verification email successfully sent'}, status=200)

@csrf_exempt
def confirm_verify_email(request, token):
    try:
        email_token = EmailVerifyToken.objects.get(token=token)
    except EmailVerifyToken.DoesNotExist:
        return JsonResponse({'error': 'Invalid or already used token'}, status=400)

    if email_token.expires_at < timezone.now():
        # Token has expired
        return JsonResponse({'error': 'Token has expired'}, status=400)

    # Update user's verified status
    user = email_token.user
    if hasattr(user, 'userprofile'):
        # Include fields from UserProfile model
        user_profile = user.userprofile
        user_profile.verified = True
        user_profile.save()

    # Delete the reset token
    email_token.delete()

    return HttpResponseRedirect(settings.REACT_URL)

def send_password_reset_email(to_email, token):
    try:
        # Mailgun API endpoint
        url = f"https://api.mailgun.net/v3/{getattr(settings, 'MAILGUN_DOMAIN')}/messages"

        # Mailgun API credentials
        api_key = getattr(settings, 'MAILGUN_API_KEY')
        from_email = getattr(settings, 'MAILGUN_FROM_EMAIL')

        # Email data
        subject = '[DNA Subway 2.0] Reset your Password'
        text = f"Someone, perhaps you, has requested to change the password for this account.\nIf it wasn't you, you may disregard this message.\n\nUse this link to reset your password: {getattr(settings, 'REACT_URL')}reset/{token}. This link will expire after one hour."
        body = f"<p>Someone, perhaps you, has requested to change the password for this account.<br />If it wasn't you, you may disregard this message.</p><p>Use this link to reset your password: <a href=\"{getattr(settings, 'REACT_URL')}reset/{token}\">{getattr(settings, 'REACT_URL')}reset/{token}</a>. This link will expire after one hour.</p>"
        data = {
            'from': from_email,
            'to': to_email,
            'subject': subject,
            'text': text,
            'html': body
        }

        # Make POST request to Mailgun API
        response = requests.post(url, auth=('api', api_key), data=data)

        # Check if the email was successfully sent
        if response.status_code == 200:
            print("Password reset email sent successfully")
        else:
            print("Failed to send password reset email")
            return False
    except Exception as e:
        print(f"Error sending password reset email: {e}")
        return False

    return True

def send_verification_email(to_email, backend_verify_url):
    try:
        # Mailgun API endpoint
        url = f"https://api.mailgun.net/v3/{getattr(settings, 'MAILGUN_DOMAIN')}/messages"

        # Mailgun API credentials
        api_key = getattr(settings, 'MAILGUN_API_KEY')
        from_email = getattr(settings, 'MAILGUN_FROM_EMAIL')

        # Email data
        subject = '[DNA Subway 2.0] Verify your email'
        text = f"Someone, perhaps you, used this email address for their DNA Subway 2.0 account.\nIf it wasn't you, you may disregard this message.\n\nUse this link to verify your email: {backend_verify_url}. This link will expire after one hour."
        body = f"<p>Someone, perhaps you, used this email address for their DNA Subway 2.0 account.<br />If it wasn't you, you may disregard this message.</p><p>Use this link to verify your email: <a href=\"{backend_verify_url}\">{backend_verify_url}</a>. This link will expire after one hour.</p>"
        data = {
            'from': from_email,
            'to': to_email,
            'subject': subject,
            'text': text,
            'html': body
        }

        # Make POST request to Mailgun API
        response = requests.post(url, auth=('api', api_key), data=data)

        # Check if the email was successfully sent
        if response.status_code == 200:
            print("Email verification email sent successfully")
        else:
            print("Failed to send verification email")
            return False
    except Exception as e:
        print(f"Error sending verification: {e}")
        return False

    return True

def send_permission_request_result_email(to_email, decision, reason):
    try:
        # Mailgun setup
        domain = getattr(settings, 'MAILGUN_DOMAIN')
        api_key = getattr(settings, 'MAILGUN_API_KEY')
        from_email = getattr(settings, 'MAILGUN_FROM_EMAIL')

        url = f"https://api.mailgun.net/v3/{domain}/messages"

        subject = f"[DNA Subway 2.0] Your permission request was {decision}"
        text = f"Your request for elevated privileges was {decision}.\n\nReason you gave:\n{reason}"
        html = (
            f"<p>Your request for elevated privileges was <strong>{decision}</strong>.</p>"
            f"<p><strong>Reason you gave:</strong><br>{reason}</p>"
        )

        data = {
            'from': from_email,
            'to': to_email,
            'subject': subject,
            'text': text,
            'html': html
        }

        response = requests.post(url, auth=('api', api_key), data=data)

        if response.status_code != 200:
            print(f"Failed to send permission result email: {response.text}")
            return False
    except Exception as e:
        print(f"Error sending permission result email: {e}")
        return False

    return True

@csrf_exempt
def confirm_password_reset(request):
    parsed_data = parse_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    code = data.get('code')
    password = data.get('password')
    verify_password = data.get('verify')

    if password != verify_password:
        return JsonResponse({'error': 'Passwords do not match'}, status=400)

    try:
        reset_token = PasswordResetToken.objects.get(token=code)
    except PasswordResetToken.DoesNotExist:
        return JsonResponse({'error': 'Invalid or already used token'}, status=400)

    if reset_token.expires_at < timezone.now():
        # Token has expired
        return JsonResponse({'error': 'Token has expired'}, status=400)

    # Update user's password
    user = reset_token.user
    user.password = make_password(password)
    user.save()

    # Delete the reset token
    reset_token.delete()

    return JsonResponse({'success': 'Password reset successful'}, status=200)

def get_user_by_reset_token(request):
    token = request.GET.get('token')

    if not token:
        return JsonResponse({'error': 'Token is required.'}, status=400)

    try:
        reset_token = PasswordResetToken.objects.select_related('user').get(token=token)
    except PasswordResetToken.DoesNotExist:
        return JsonResponse({'error': 'Invalid token.'}, status=404)

    if reset_token.expires_at < timezone.now():
        return JsonResponse({'error': 'Token has expired.'}, status=410)

    user = reset_token.user
    return JsonResponse({
        'username': user.username,
        'email': user.email,
    }, status=200)

# Mapping between ethnicity fields and their corresponding strings
ETHNICITY_MAPPING = {
    'white': 'wh',
    'native': 'na',
    'asian': 'as',
    'black': 'aa',
    'hispanic': 'hs',
    'middle': 'me',
    'pacific': 'pi',
    'other': 'oo',
    'unanswered': 'pn',
}
def get_user_fields(request):
    # Retrieve the user associated with the session ID
    user = request.user

    if user.is_authenticated:
        if request.method == 'GET':
            # Retrieve all fields of the user model
            user_fields = {
                'username': user.username,
                'first': user.first_name,
                'last': user.last_name,
                'email': user.email,
                'id': user.id,
                'superuser': user.is_superuser,
            }

            # Check if UserProfile exists for the user
            if hasattr(user, 'userprofile'):
                # Include fields from UserProfile model
                user_profile = user.userprofile
                pending_access_request = EnhancedPermissionToken.objects.filter(user=user, status='pending').exists() and not user_profile.elevated_access
                user_fields.update({
                    'country': user_profile.country,
                    'zip': user_profile.postal_code,
                    'gender': user_profile.gender,
                    'occupation': user_profile.occupation,
                    'source': user_profile.source,
                    'pending_access_request': pending_access_request,
                    'elevated_access': user_profile.elevated_access,
                    'verified': user_profile.verified,
                })

                # Retrieve ethnicity fields as a list of strings
                ethnicity_list = []
                for field, value in user_profile.ethnicity.__dict__.items():
                    if value == True:
                        ethnicity_list.append(ETHNICITY_MAPPING.get(field, ''))

                user_fields['ethnicity'] = ethnicity_list

            return JsonResponse(user_fields)
        elif request.method == "POST":
            # Parse JSON data from the request body
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'error': 'Invalid JSON data'}, status=400)

            # Update user fields based on POST data
            try:
                user.first_name = data.get('first', user.first_name)
                user.last_name = data.get('last', user.last_name)
                username = data.get('username', user.username)
                if User.objects.filter(username=username).exists() and username != user.username:
                    return JsonResponse({'error': 'User with this username already exists'}, status=400)
                user.username = username
                email = data.get('email', user.email)
                email_changed = email != user.email
                if User.objects.filter(email=email).exists() and email_changed:
                    return JsonResponse({'error': 'User with this email already exists'}, status=400)
                user.email = email
                user.save()

                # Update UserProfile fields if available
                if hasattr(user, 'userprofile'):
                    user_profile = user.userprofile
                    user_profile.country = data.get('country', user_profile.country)
                    user_profile.postal_code = data.get('zip', user_profile.postal_code)
                    user_profile.gender = data.get('gender', user_profile.gender)
                    user_profile.occupation = data.get('occupation', user_profile.occupation)
                    user_profile.source = data.get('source', user_profile.source)
                    if email_changed:
                        user_profile.verified = False
                    user_profile.save()

                    # Update ethnicity record associated with the UserProfile
                    ethnicity_list = data.get('ethnicity', [])
                    if len(ethnicity_list) > 0:
                        ethnicity_record, _ = Ethnicity.objects.get_or_create(userprofile=user_profile)
                        ethnicity_record.white = 'wh' in ethnicity_list
                        ethnicity_record.native = 'na' in ethnicity_list
                        ethnicity_record.asian = 'as' in ethnicity_list
                        ethnicity_record.black = 'aa' in ethnicity_list
                        ethnicity_record.hispanic = 'hs' in ethnicity_list
                        ethnicity_record.middle = 'me' in ethnicity_list
                        ethnicity_record.pacific = 'pi' in ethnicity_list
                        ethnicity_record.other = 'oo' in ethnicity_list
                        ethnicity_record.unanswered = 'pn' in ethnicity_list
                        ethnicity_record.save()

            except Exception as e:
                return JsonResponse({'error': str(e)}, status=400)

            return JsonResponse({'success': 'User fields updated successfully'})
    else:
        return JsonResponse({'error': 'User not authenticated'}, status=401)

def logout_view(request):
    if request.user.is_authenticated:
        logout(request)
        return JsonResponse({'success': 'Logged out successfully', "redirect": ""})
    else:
        return JsonResponse({'error': 'User not authenticated'}, status=401)

@csrf_exempt
def create_guest_user(request):
    if request.method == 'POST':
        # Attempt to create a guest user with a unique username
        for _ in range(10):  # Retry up to 10 times
            username = generate_guest_username()
            if not User.objects.filter(username=username).exists():
                # If no user exists with the generated username, create the guest user
                guest_user = User.objects.create_user(username=username)
                login(request, guest_user)
                return JsonResponse({'username': username, 'success': 'Guest user created successfully', 'redirect': '/'})
        # If all retries fail, return an error
        return JsonResponse({'error': 'Failed to create guest user'}, status=500)

    return JsonResponse({'error': 'Method not allowed'}, status=405)

def generate_guest_username():
    # Generate a random username starting with 'guest_' followed by random characters
    random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f'guest_{random_chars}'

def create_project(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    # Extract fields from the JSON data
    title = data.get('title', '').strip()
    description = data.get('description', '').strip()
    project_type = data.get('project_type', 'PHY')
    sequencing_type = data.get('sequencing_type', 'sanger')
    barcode_type = data.get('barcode_type', 'Other')

    # Check if project_type is valid
    if project_type not in dict(Project.PROJECT_TYPES).keys():
        return JsonResponse({'error': 'Invalid project_type'}, status=400)

    # Check if sequencing_type is valid
    if sequencing_type not in dict(Project.SEQUENCING_TYPES).keys():
        return JsonResponse({'error': 'Invalid sequencing_type'}, status=400)

    # Check if sequencing_type is valid
    if barcode_type not in dict(Project.BARCODE_TYPES).keys():
        return JsonResponse({'error': 'Invalid barcode_type'}, status=400)

    # Check if title and description are not empty and within character limits
    if not title or len(title) > 64:
        return JsonResponse({'error': 'Title must be non-empty and at most 64 characters'}, status=400)

    if len(description) > 160:
        return JsonResponse({'error': 'Description must be at most 160 characters'}, status=400)

    # Check if a project with the same title exists for the current user and not deleted
    existing_project = Project.objects.filter(user=request.user, title=title, deleted=False).first()
    if existing_project:
        return JsonResponse({'error': 'Project with the same title already exists'}, status=400)

    # Create the project
    new_project = Project(user=request.user, title=title, description=description,
                      project_type=project_type, sequencing_type=sequencing_type, barcode_type=barcode_type)
    new_project.save()

    return JsonResponse({'success': 'Project created successfully', 'redirect': '/pages/starter?pid=' + str(new_project.id)})

def update_project(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']

    title = data.get('title', '').strip()
    description = data.get('description', '').strip()

    # Check if title and description are not empty and within character limits
    if len(title) > 64:
        return JsonResponse({'error': 'Title must be non-empty and at most 64 characters'}, status=400)

    if len(description) > 160:
        return JsonResponse({'error': 'Description must be at most 160 characters'}, status=400)

    # Check if a project with the same title exists for the current user and not deleted
    existing_project = Project.objects.filter(user=request.user, title=title, deleted=False).first()
    if existing_project and existing_project.id != project.id:
        return JsonResponse({'error': 'Project with the same title already exists'}, status=400)

    # Create the project
    if title:
        project.title = title
    if description:
        project.description = description
    project.save()
    return JsonResponse({'success': 'Project updated successfully'})

def user_projects(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    # Retrieve projects for the current authenticated user
    projects = Project.objects.filter(user=request.user, deleted=False).order_by('-id')

    # Paginate the projects with 10 projects per page
    #paginator = Paginator(projects, 10)
    #page_number = request.GET.get('page')
    #projects_page = paginator.get_page(page_number)

    # Serialize project data
    serialized_projects = []
    for project in projects:
        serialized_project = {
            'id': project.id,
            'username': project.user.username,
            'title': project.title,
            'description': project.description,
            'sequencing_type': project.sequencing_type,
            'project_type': project.project_type,
            'created_date': project.created.strftime('%Y-%m-%d'),  # Format date as YYYY-MM-DD
            'public': project.public
        }
        serialized_projects.append(serialized_project)

    return JsonResponse({'success': 'Projects retrieved', 'projects': serialized_projects}, safe=False)

def project_info(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    pid = request.GET.get('pid')
    # Retrieve project info for the current authenticated user
    try:
        project = Project.objects.filter(id=pid, user=request.user)
    except ValueError:
        return JsonResponse({'error': 'Project not found'}, status=404)
    if not project:
        return JsonResponse({'error': 'Project not found'}, status=404)
    else:
        project = project.first()
    # Retrieve project data files for the current authenticated user
    project_data_files = ProjectDataFile.objects.filter(project=project).select_related('data_file').order_by('data_file__name')
    serialized_sequences = []
    for project_data_file in project_data_files:
        data_file = project_data_file.data_file
        source_origin = (
            data_file.process_id
            or (data_file.source_file_id.id if data_file.source_file_id else None)
            or data_file.accession_number
            or data_file.order_id
            or (data_file.reference_data.id if data_file.reference_data else None)
            or (data_file.sample_data.id if data_file.sample_data else None)
            or (data_file.nanopore_seq_id.id if data_file.nanopore_seq_id else None)
        )
        consensus_data = ConsensusData.objects.filter(consensus=project_data_file.data_file).last()
        quality_scores = get_quality_scores(project_data_file.data_file.associated_abi.name) if data_file.associated_abi else None
        serialized_sequence = {
            'paired_id': None,
            'consensus_id': None,
            'consensus_name': None,
            'file_id': project_data_file.data_file.id,
            'display_name': data_file.name,
            'trace_file_path': data_file.associated_abi.name if data_file.associated_abi else None,
            'low_quality': is_low_quality(quality_scores) if quality_scores else False,
            'quality_scores': quality_scores,
            'trim_start': data_file.trim_start if data_file.trim_start else 0,
            'trim_end': data_file.trim_end if data_file.trim_end else 0,
            'sequence': data_file.reads,
            'read_type': data_file.read_type,
            'forward_file_id': data_file.forward_read.id if data_file.forward_read else None,
            'reverse_file_id': data_file.reverse_read.id if data_file.reverse_read else None,
            'forward_display_name': data_file.forward_read.name if data_file.forward_read else None,
            'reverse_display_name': data_file.reverse_read.name if data_file.reverse_read else None,
            'forward_align': consensus_data.forward_align if consensus_data else None,
            'reverse_align': consensus_data.reverse_align if consensus_data else None,
            'is_reference': data_file.read_type == "E",
            'source': data_file.source,
            'source_origin': source_origin,
            'blast_job': False,
            'blast_results': None,
            'is_public': data_file.is_public,
            'in_sequence_repository': data_file.in_sequence_repository,
        }

        # Check for BlastJob
        blast_job_exists = BlastJob.objects.filter(data_file=project_data_file.data_file, job__status__in=['PENDING', 'RUNNING', 'FINISHED']).exists()
        project_blast_done = ProjectBlastDone.objects.filter(project_data_file=project_data_file).exists()
        if blast_job_exists:
            serialized_sequence['blast_job'] = project_blast_done

            # Fetch BlastData and related BlastResults
            blast_data = BlastData.objects.filter(blast_file=project_data_file.data_file).first()
            if blast_data:
                blast_results = BlastResult.objects.filter(blast_data=blast_data)

                # If there are no results, return an empty array
                serialized_sequence['blast_results'] = [
                    {
                        'id': result.id,
                        'accession': result.accession,
                        'details': result.details,
                        'length': result.length,
                        'evalue': result.evalue,
                        'mismatches': result.mismatches,
                        #'sequence': result.sequence,
                        'bitscore': result.bitscore,
                    }
                    for result in blast_results
                ] if blast_results else []
        serialized_sequences.append(serialized_sequence)

    # Now iterate again to set the 'is_paired' flag
    for sequence in serialized_sequences:
        # Check if the current sequence's file_id matches another sequence's forward or reverse file_id
        for other_sequence in serialized_sequences:
            if other_sequence['forward_file_id'] == sequence['file_id'] or other_sequence['reverse_file_id'] == sequence['file_id']:
                sequence['paired_id'] = other_sequence['reverse_file_id'] if other_sequence['forward_file_id'] == sequence['file_id'] else other_sequence['forward_file_id']
                sequence['consensus_id'] = other_sequence['file_id']
                sequence['consensus_name'] = other_sequence['display_name']
                break  # No need to continue checking once it's paired
    serialized_nanopore_sequences = []
    # Get all ProjectNanoporeSequences for the project and retrieve their related NanoporeSequences
    nanopore_sequences = ProjectNanoporeSequence.objects.filter(project=project) \
        .select_related('nanopore_sequence') \
        .prefetch_related(
            Prefetch(
                'nanopore_sequence__fastpjob_set',
                queryset=FastpJob.objects.filter(project=project).exclude(status='failed'),
                to_attr='related_fastp_jobs'
            ),
            Prefetch(
                'nanopore_sequence__porechopjob_set',
                queryset=PorechopJob.objects.filter(project=project).exclude(status='failed'),
                to_attr='related_porechop_jobs'
            ),
            Prefetch(
                'nanopore_sequence__medakajob_set',
                queryset=MedakaJob.objects.filter(project=project).exclude(status='failed'),
                to_attr='related_medaka_jobs'
            ),
        ) \
        .order_by('nanopore_sequence__name')

    # Prepare the data to return: list of id and name of each NanoporeSequence
    nanopore = []
    for pns in nanopore_sequences:
        nanopore_sequence = pns.nanopore_sequence
        sample_set = NanoporeSampleSet.objects.filter(directory__in=[
            nanopore_sequence.file.name[:len(sample_set.directory)] for sample_set in NanoporeSampleSet.objects.all()
        ]).first()
        # Retrieve related FastpResult and FastpJob (if they exist)
        fastp_result = FastpResult.objects.filter(project_nanopore_sequence=pns).first()
        fastp_jobs = getattr(nanopore_sequence, 'related_fastp_jobs', [])
        # Retrieve related PorechopResult and PorechopJob (if they exist)
        porechop_result = PorechopResult.objects.filter(project_nanopore_sequence=pns).first()
        porechop_jobs = getattr(nanopore_sequence, 'related_porechop_jobs', [])
        # Retrieve related MedakaResult and MedakaJob (if they exist)
        medaka_result = MedakaResult.objects.filter(project_nanopore_sequence=pns).first()
        medaka_jobs = getattr(nanopore_sequence, 'related_medaka_jobs', [])
        nanopore.append({
            'nanopore_sequence_id': nanopore_sequence.id,
            'sample_set_name': sample_set.name if sample_set else None,
            'sample_set': True if sample_set else False,
            'name': nanopore_sequence.name,
            'fastp_result': {
                'filtered_file': fastp_result.filtered_file.url if fastp_result else None,
                'json_file': fastp_result.json_file.url if fastp_result else None,
                'html_file': fastp_result.html_file.url if fastp_result else None,
                'processed_at': fastp_result.processed_at if fastp_result else None
            } if fastp_result else None,
            'fastp_run': True if fastp_jobs else False,
            'porechop_result': {
                'chopped_file': porechop_result.chopped_file.url if porechop_result else None,
                'html_file': porechop_result.html_log_file.url if porechop_result else None,
                'processed_at': porechop_result.processed_at if porechop_result else None
            } if porechop_result else None,
            'porechop_run': True if porechop_jobs else False,
            'medaka_result': {
                'medaka_output_dir': medaka_result.medaka_output_dir if medaka_result else None,
                'processed_at': medaka_result.processed_at if medaka_result else None
            } if medaka_result else None,
            'medaka_run': True if medaka_jobs else False,
        })
    # Filter for MuscleJobs with valid status (not CANCELLED or FAILED)
    muscle_jobs = MuscleJob.objects.filter(
        job__project_id=project.id,
        job__status__in=['PENDING', 'RUNNING', 'FINISHED']  # Adjust statuses as needed
    ).order_by('-id')

    # Check if any valid muscle job exists
    has_muscle_job = muscle_jobs.exists()
    serialized_muscle_data = None
    phylip_nj_job = False
    phylip_nj_data = None
    phylip_nj_data_id = None
    phylip_nj_outgroup = ""
    phylip_ml_job = False
    phylip_ml_data = None
    phylip_ml_data_id = None
    phylip_ml_outgroup = ""

    if has_muscle_job:
        # Get the most recent MuscleJob
        latest_muscle_job = muscle_jobs.first()

        # Prefetch related data for MuscleData, MuscleSequence, MuscleConservation, etc.
        muscle_data = MuscleData.objects.filter(muscle_job=latest_muscle_job).prefetch_related(
            'sequences',  # MuscleSequence
            'conservations',  # MuscleConservation
            'variations',  # MuscleVariation
            'consensus',  # MuscleConsensus
        ).first()

        if muscle_data:
            similarity = {}
            phylip_nj_job = PhylipNJJob.objects.filter(muscle_data=muscle_data).exclude(Q(job__status='FAILED') | Q(job__status='STOPPED')).last()
            phylip_ml_job = PhylipMLJob.objects.filter(muscle_data=muscle_data).exclude(Q(job__status='FAILED') | Q(job__status='STOPPED')).last()

            if phylip_nj_job:
                phylip_nj_outgroup = phylip_nj_job.outgroup
                phylip_nj_data = PhylipNJData.objects.filter(phylipnj_job=phylip_nj_job).first()
                phylip_nj_data_id = phylip_nj_data.id if phylip_nj_data else None
                phylip_nj_data = phylip_nj_data.outtree if phylip_nj_data else None
                phylip_nj_job = True
            if phylip_ml_job:
                phylip_ml_outgroup = phylip_ml_job.outgroup
                phylip_ml_data = PhylipMLData.objects.filter(phylipml_job=phylip_ml_job).first()
                phylip_ml_data_id = phylip_ml_data.id if phylip_ml_data else None
                phylip_ml_data = phylip_ml_data.outtree if phylip_ml_data else None
                phylip_ml_job = True
            # Query the MuscleSimilarity records associated with the muscle_data
            similarity_records = MuscleSimilarity.objects.filter(muscle_data=muscle_data)

            # Loop through the records and organize them in the similarity dictionary
            for sim in similarity_records:
                if sim.sequence1 not in similarity:
                    similarity[sim.sequence1] = {}

                similarity[sim.sequence1][sim.sequence2] = str(sim.similarity_percentage)

            # Add missing '-' for self comparisons
            sequences = list(similarity.keys())
            for seq in sequences:
                similarity[seq][seq] = '-'

            # Ensure all pairs are bidirectional
            for seq1 in sequences:
                for seq2 in sequences:
                    if seq2 not in similarity[seq1]:
                        similarity[seq1][seq2] = '-'
                    if seq1 not in similarity[seq2]:
                        similarity[seq2][seq1] = similarity[seq1][seq2]
            # Serialize the muscle data
            serialized_muscle_data = {
                   'id': muscle_data.id,
                   'sequences': {
                        seq.name: seq.bases.split(",")
                        for seq in muscle_data.sequences.all()
                    },
                   'conservations': [
                        cons.value for cons in muscle_data.conservations.order_by('position')
                    ],
                   'variations': [
                        var.variations.split(",") for var in muscle_data.variations.order_by('position')
                    ],
                   'consensus': muscle_data.consensus.sequence if hasattr(muscle_data, 'consensus') else None,
                   'similarity': similarity if similarity else None,
                   'left_trim': muscle_data.trim.left_trim if hasattr(muscle_data, 'trim') else 0,
                   'right_trim': muscle_data.trim.right_trim if hasattr(muscle_data, 'trim') else 0,
            } if hasattr(muscle_data, 'consensus') else None

    project_data = {
        'id': project.id,
        'muscle_job': has_muscle_job,
        'muscle_data': serialized_muscle_data,
        'phylip_nj_job': phylip_nj_job,
        'phylip_nj_data': phylip_nj_data,
        'phylip_nj_data_id': phylip_nj_data_id,
        'phylip_nj_outgroup': phylip_nj_outgroup,
        'phylip_ml_job': phylip_ml_job,
        'phylip_ml_data': phylip_ml_data,
        'phylip_ml_data_id': phylip_ml_data_id,
        'phylip_ml_outgroup': phylip_ml_outgroup,
        'title': project.title,
        'description': project.description,
        'sequencing_type': project.sequencing_type,
        'project_type': project.project_type,
        'barcode_type': project.barcode_type,
        'created_date': project.created.strftime('%Y-%m-%d'),  # Format date as YYYY-MM-DD
        'username': project.user.username,
        'uid': project.user.id,
        'sequences': serialized_sequences,
        'nanopore_sequences': nanopore,
    }
    return JsonResponse({'success': 'Project retrieved', 'project': project_data})

def public_projects(request):
    # Retrieve public projects
    projects = Project.objects.filter(public=True, deleted=False).order_by('-id')

    # Serialize project data
    serialized_projects = []
    for project in projects:
        # Retrieve username of the user associated with the project
        username = project.user.username

        serialized_project = {
            'id': project.id,
            'title': project.title,
            'description': project.description,
            'sequencing_type': project.sequencing_type,
            'project_type': project.project_type,
            'created_date': project.created.strftime('%Y-%m-%d'),  # Format date as YYYY-MM-DD
            'username': username,
        }
        serialized_projects.append(serialized_project)

    return JsonResponse({'success': 'Projects retrieved', 'projects': serialized_projects}, safe=False)

def delete_project(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    project.deleted = True
    project.save()
    return JsonResponse({'success': 'Project deleted successfully'}, status=200)

def toggle_project_share_status(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    project.public = not project.public
    project.save()
    return JsonResponse({'success': 'Project share status toggled successfully'}, status=200)

def download_and_create_datafiles(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    # Get the 'o' and 'f' parameters from the request
    o = data.get('o', None)
    f = data.get('f', None)

    # Check if 'o' and 'f' parameters are present
    if o is None or not f:
        return JsonResponse({'error': 'Missing required parameters.'}, status=400)

    # Make the request to the external API
    api_url = 'https://dnalc.cshl.edu/genewiz/files-new'
    response = requests.get(api_url, params={'o': str(o), 'f': ','.join(map(str, f))})

    # Check if the request was successful
    if response.status_code != 200:
        return JsonResponse({'error': 'Failed to retrieve files from external API.'}, status=500)

    # Iterate over the response data
    warning_message = ""
    for file_info in response.json():
        file_url = file_info.get('file')
        file_id = file_info.get('id')

        message, sequence, trace_exists, record, _ = parse_reads(file_url)
        if message:
            print(message)
            continue

        name = cleanSequenceName(file_url)
        data_file_exists = ProjectDataFile.objects.filter(project=project, data_file__name=name)
        if data_file_exists:
            warning_message += "File with display name '" + name + "' already added to this project.\n"
            continue

        # Save the file to the DataFile model
        data_file = DataFile.objects.create(
            user=request.user,
            name=name,
            trace_exists=trace_exists,
            read_type="F",
            reads=sequence,
            source="import",
            order_id=o,
        )
        response = requests.get(file_url)

        abi_content = ContentFile(response.content)
        abi_file_name = f"{data_file.id}.abi"
        abi_file_path = default_storage.save(f"abi_files/{abi_file_name}", abi_content)
        data_file.associated_abi.name = abi_file_path
        data_file.save()

        header = re.sub(r'\s+', '_', data_file.name)
        fasta_content = ContentFile(f">{header}\n{data_file.reads}\n")
        fasta_file_name = f"{data_file.id}.fasta"
        fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", fasta_content)
        data_file.associated_fasta.name = fasta_file_path
        data_file.save()

        # Create a ProjectDataFile instance
        project_data_file = ProjectDataFile.objects.create(
            project=project,
            data_file=data_file
        )

    return JsonResponse({'success': 'Data files created successfully.', 'message': warning_message}, status=200)

@csrf_exempt
def process_abi_file(request):
    PROTOCOL = request.scheme + "://"
    # Check if the request method is not POST
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)

    # Load JSON data from request body
    try:
        data = json.loads(request.body)
        file_id = data.get('file_id')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)

    # Check if file_name is not provided
    if not file_id:
        return JsonResponse({'error': 'File not provided'}, status=400)

    dataFile = DataFile.objects.get(id=file_id)
    if not dataFile:
        return JsonResponse({'error': 'No such file'}, status=404)

    abi_file = dataFile.associated_abi
    if not abi_file:
        return JsonResponse({'error': 'No such trace file'}, status=404)

        
    # Open ABI file and extract data
    message, sequence, trace_exists, record, _ = parse_reads(PROTOCOL + request.get_host() + "/backend" + "/" + abi_file.name)
    if message:
        return JsonResponse({'error': message}, status=500)

    seq_display_id = dataFile.name + ".abi"
    quality_scores = list(record.letter_annotations['phred_quality'])
    max_peak = max(
        record.annotations['abif_raw']['DATA9'] +
        record.annotations['abif_raw']['DATA10'] +
        record.annotations['abif_raw']['DATA11'] +
        record.annotations['abif_raw']['DATA12']
    )
    base_position = []
    for base_ascii in record.annotations['abif_raw']['FWO_1']:
        base = chr(base_ascii)
        if base not in ['A', 'C', 'G', 'T']:
            continue
        base_position.append(chr(base_ascii))

    if not len(base_position) == 4:
        return JsonResponse({'error': 'Unexpected bases in abi file'}, status=400)

    trace_values = {
        base_position[0]: [int(100 * value / max_peak) for value in record.annotations['abif_raw']['DATA9']],
        base_position[1]: [int(100 * value / max_peak) for value in record.annotations['abif_raw']['DATA10']],
        base_position[2]: [int(100 * value / max_peak) for value in record.annotations['abif_raw']['DATA11']],
        base_position[3]: [int(100 * value / max_peak) for value in record.annotations['abif_raw']['DATA12']]
    }

    base_locations = record.annotations['abif_raw']["PLOC1"]

    # Return the JSON data
    return JsonResponse({
        'seq_display_id': seq_display_id,
        'sequence': sequence,
        'qualityScores': quality_scores,
        'trace_values': trace_values,
        'base_locations': base_locations,
        'offset': 0,
        'file_id': dataFile.id,
        'seq_id': False
    })

@csrf_exempt
def check_job_status(request):
    try:
        input_json = json.loads(request.body)
        event_data = json.loads(input_json["event"]["data"])
        print("Do job status check")
        job_status_check(event_data["jobUuid"], event_data["newJobStatus"])
    except Exception as e:
        print(e)
    return JsonResponse({'error': 'File not provided'}, status=200)

def trim_project_sequences(request):
    PROTOCOL = request.scheme + "://"
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    project_data_files = ProjectDataFile.objects.filter(project=project, data_file__read_type__in=["F", "R"])
    for project_data_file in project_data_files:
        if not project_data_file.data_file.left_trim and not project_data_file.data_file.right_trim:
            trim_suggestion = suggested_trim(PROTOCOL + request.get_host() + "/backend", project_data_file.data_file)
            if trim_suggestion["status"] == "success":
                local_sequence_trim(project_data_file.data_file, trim_suggestion["left"], trim_suggestion["right"])
                remove_associated_consensus_files(project, project_data_file.data_file)
    return JsonResponse({"status": "success"})

def trim_sequence(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_id = data.get('file_id')
    left_trim = data.get('left_trim', None)
    right_trim = data.get('right_trim', None)
    # Check if file_name is not provided
    if not file_id:
        return JsonResponse({'error': 'File not provided'}, status=400)

    dataFile = DataFile.objects.get(id=file_id)
    if not dataFile:
        return JsonResponse({'error': 'No such file'}, status=404)

    #trim_result = sequence_trim(request.user, PROTOCOL + request.get_host() + "/backend", dataFile, left_trim, right_trim, projectId)
    trim_result = local_sequence_trim(dataFile, left_trim, right_trim)
    remove_associated_consensus_files(project, dataFile)

    # Return the JSON data
    return JsonResponse(trim_result)

def suggest_trim(request):
    PROTOCOL = request.scheme + "://"
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']

    # Check if file is provided
    file_id = data.get('file_id')
    if not file_id:
        return JsonResponse({'error': 'File not provided'}, status=400)

    dataFile = DataFile.objects.get(id=file_id)
    if not dataFile:
        return JsonResponse({'error': 'No such file'}, status=404)

    #trim_result = sequence_trim(request.user, PROTOCOL + request.get_host() + "/backend", dataFile, left_trim, right_trim, projectId)
    trim_suggestion = suggested_trim(PROTOCOL + request.get_host() + "/backend", dataFile)

    # Return the JSON data
    return JsonResponse(trim_suggestion)

def remove_associated_consensus_files(project, data_file):
    other_data_files = ProjectDataFile.objects.filter(project=project).select_related('data_file').order_by('data_file__name')
    for other_data_file in other_data_files:
        forward_file_id = other_data_file.data_file.forward_read.id if other_data_file.data_file.forward_read else None
        reverse_file_id = other_data_file.data_file.reverse_read.id if other_data_file.data_file.forward_read else None
        if forward_file_id == data_file.id or reverse_file_id == data_file.id:
            other_data_file.delete()

def undo_trim(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    # Check if file is provided
    file_id = data.get('file_id')
    if not file_id:
        return JsonResponse({'error': 'File not provided'}, status=400)

    dataFile = DataFile.objects.get(id=file_id)
    if not dataFile:
        return JsonResponse({'error': 'No such file'}, status=404)

    trim_undone = undo_sequence_trim(dataFile)
    remove_associated_consensus_files(project, dataFile)

    # Return the JSON data
    return JsonResponse(trim_undone)

def undo_consensus(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_id = data.get('consensus_id')
    # Check if file is provided
    if not file_id:
        return JsonResponse({'error': 'File not provided'}, status=400)

    dataFile = DataFile.objects.get(id=file_id)
    if not dataFile:
        return JsonResponse({'error': 'No such file'}, status=404)

    project_data_file = ProjectDataFile.objects.filter(project=project, data_file=dataFile).first()
    if not project_data_file:
        return JsonResponse({'error': 'No such project file'}, status=404)

    if dataFile.forward_read:
        dataFile.forward_read.read_type = "F"
        dataFile.forward_read.save()
    if dataFile.reverse_read:
        dataFile.reverse_read.read_type = "F"
        dataFile.reverse_read.save()
    if ProjectDataFile.objects.filter(data_file=dataFile).count() < 2 and dataFile.source != "sample" and dataFile.source != "reference":
        if dataFile.associated_abi:
            os.remove(dataFile.associated_abi.name)
        if dataFile.associated_fasta:
            os.remove(dataFile.associated_fasta.name)
        dataFile.delete()
    project_data_file.delete()

    return JsonResponse({'status': 'success', 'message': 'Consensus undone.'}, status=200)

def blast_sequence(request):
    PROTOCOL = request.scheme + "://"
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_ids = data.get('file_id') # This can be a single ID or a list of IDs
    clade = data.get('clade',None)
    if not file_ids:
        return JsonResponse({'error': 'File not provided'}, status=400)

    # Ensure file_ids is a list, if not, make it a list for uniform processing
    if not isinstance(file_ids, list):
        file_ids = [file_ids]

    blast_results = []
    errors = []

    for file_id in file_ids:
        try:
            dataFile = DataFile.objects.get(id=file_id)
        except DataFile.DoesNotExist:
            errors.append({'file_id': file_id, 'error': 'No such file'})
            continue

        project_data_file = ProjectDataFile.objects.filter(project=project, data_file=dataFile).first()
        if not project_data_file:
            errors.append({'file_id': file_id, 'error': 'User cannot BLAST this file'})
            continue

        blast_job = BlastJob.objects.filter(data_file=dataFile, clade=(clade if clade else 'default'), job__status__in=['PENDING', 'RUNNING', 'FINISHED'])
        if blast_job:
            blast_data = BlastData.objects.filter(blast_file=dataFile).first()
            if blast_data:
                blast_result = BlastResult.objects.filter(blast_data=blast_data)
                if blast_result:
                    blast_results.append({'file_id': file_id, 'message': "BLAST job finished"})
                    ProjectBlastDone.objects.create(project_data_file=project_data_file, clade=(clade if clade else 'default'))
                    continue

        blast_result = local_blast(request.user, PROTOCOL + request.get_host() + "/backend", dataFile, clade, project.id)
        ProjectBlastDone.objects.create(project_data_file=project_data_file, clade=(clade if clade else 'default'))

        if blast_result['status'] == 'error':
            errors.append({'file_id': file_id, 'error': blast_result['message']})
        else:
            blast_results.append({'file_id': file_id, 'message': blast_result['message']})

    # Construct the response with both results and errors
    response = {
        'results': blast_results,
        'errors': errors
    }

    # Return response based on errors and results
    if not blast_results and errors:
        response["status"] = "error"
        return JsonResponse(response, status=400)

    response["status"] = "success"
    return JsonResponse(response, status=200)

def phylip_nj_sequence(request):
    PROTOCOL = request.scheme + "://"
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    muscle_data_id = data.get('muscle_data_id')
    outgroup = data.get('outgroup',None)
    if not muscle_data_id:
        return JsonResponse({'error': 'Muscle data file not provided or muscle not run'}, status=400)
    muscle_data = MuscleData.objects.get(id=muscle_data_id)
    if not muscle_data:
        return JsonResponse({'error':'No such file'}, status=404)
    #phylip_nj_result = phylip_nj(request.user, PROTOCOL + request.get_host()+'/backend', muscle_data, outgroup, project.id)
    phylip_nj_result = local_phylip_nj(request.user, PROTOCOL + request.get_host()+'/backend', muscle_data, outgroup, project.id)
    return JsonResponse(phylip_nj_result)

def phylip_ml_sequence(request):
    PROTOCOL = request.scheme + "://"
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    muscle_data_id = data.get('muscle_data_id')
    outgroup = data.get('outgroup',None)
    if not muscle_data_id:
        return JsonResponse({'error': 'muscle data/file not found'}, status=400)
    muscle_data = MuscleData.objects.get(id=muscle_data_id)
    if not muscle_data:
        return JsonResponse({'error':'No such file'}, status=404)
    #phylip_ml_result = phylip_ml(request.user, PROTOCOL + request.get_host()+'/backend', muscle_data, outgroup, project.id)
    phylip_ml_result = local_phylip_ml(request.user, PROTOCOL + request.get_host()+'/backend', muscle_data, outgroup, project.id)
    return JsonResponse(phylip_ml_result)

def muscle_sequence(request):
    PROTOCOL = request.scheme + "://"
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_ids = data.get('file_ids', [])
    if not file_ids:
        return JsonResponse({'error': 'File not provided'}, status=400)
    if len(file_ids)<3:
        return JsonResponse({'error': 'provide at least 3 single sequence fasta files'}, status=400)
    muscle_result = local_muscle(request.user, PROTOCOL + request.get_host()+'/backend', file_ids, project.id)
    return JsonResponse(muscle_result)

def consense_sequence(request):
    PROTOCOL = request.scheme + "://"
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file1_id = data.get('file1_id')
    file2_id = data.get('file2_id')
    file1_read_type = data.get('file1_read_type')
    file2_read_type = data.get('file2_read_type')
    pairLabel = data.get('pairLabel')
    if not file1_id or not file2_id or not file1_read_type or not file2_read_type:
        return JsonResponse({'error': 'File info not provided'}, status=400)
    dataFile_1 = DataFile.objects.get(id=file1_id)
    dataFile_2 = DataFile.objects.get(id=file2_id)
    if not dataFile_1 or not dataFile_2:
        return JsonResponse({'error':'One of the selected files does not exist'}, status=404)
    file1_reverse = file1_read_type == "R"
    file2_reverse = file2_read_type == "R"
    if file1_reverse and not file2_reverse:
        dataFile_1.read_type = "R"
        dataFile_2.read_type = "F"
    else:
        dataFile_1.read_type = "F"
        dataFile_2.read_type = "R"

    dataFile_1.save()
    dataFile_2.save()
    #consense_result = consense(request.user, PROTOCOL + request.get_host()+'/backend', dataFile_1, dataFile_2, file1_reverse, file2_reverse, project.id)
    consense_result = local_consense(PROTOCOL + request.get_host()+'/backend', dataFile_1, dataFile_2, file1_reverse, file2_reverse, project, pairLabel)
    return JsonResponse(consense_result)

def auto_pair_sequences(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    PROTOCOL = request.scheme + "://"

    # Step 1: Get all consensus DataFiles for this project
    consensus_files_in_project = ProjectDataFile.objects.filter(
        project=project,
        data_file__read_type__in=["C"]
    ).values_list('data_file_id', flat=True)

    # Step 2: Get forward/reverse IDs used in those consensus files
    used_in_project_consensus_ids = DataFile.objects.filter(
        id__in=consensus_files_in_project
    ).values_list('forward_read_id', 'reverse_read_id')

    # Step 3: Flatten the list and remove Nones
    excluded_ids = set(
        x for pair in used_in_project_consensus_ids for x in pair if x is not None
    )

    # Step 4: Select unpaired, F/R files not used in this project’s consensus
    project_data_files = ProjectDataFile.objects.filter(
        project=project,
        data_file__read_type__in=["F", "R"],
    ).exclude(
        data_file__id__in=excluded_ids
    ).select_related('data_file')

    data_files = [pdf.data_file for pdf in project_data_files]
    data_files.sort(key=lambda df: df.name.lower())

    used_ids = set()
    paired_count = 0

    for i in range(len(data_files)):
        df1 = data_files[i]
        if df1.id in used_ids:
            continue

        name1 = df1.name
        for j in range(i + 1, len(data_files)):
            df2 = data_files[j]
            if df2.id in used_ids:
                continue

            name2 = df2.name
            lshortest = min(len(name1), len(name2))
            mismatch = ''
            k = 0
            for k in range(lshortest):
                if name1[k] != name2[k]:
                    mismatch = name1[k] + name2[k]
                    break

            if k > lshortest / 2 and mismatch and mismatch[0].upper() in 'RF' and mismatch[1].upper() in 'RF':
                file1_is_reverse = mismatch[0].upper() == 'R'
                file2_is_reverse = mismatch[1].upper() == 'R'
                if not df1.reads or not df2.reads:
                    continue

                df1.read_type = 'R' if file1_is_reverse else 'F'
                df2.read_type = 'R' if file2_is_reverse else 'F'
                df1.save()
                df2.save()

                local_consense(
                    PROTOCOL + request.get_host() + '/backend',
                    df1, df2,
                    file1_is_reverse, file2_is_reverse,
                    project,
                    pairLabel=None
                )

                used_ids.update({df1.id, df2.id})
                paired_count += 1
                break  # stop looking for matches for df1

    return JsonResponse({'status': 'done', 'paired_count': paired_count})

def upload_fastq_directory(fastq_dir, project, base_path = None, skip_root = False):
    """
    Process folders in the given directory, check for existing Nanopore sequences,
    concatenate .fastq.gz files, and link them to the project if not already linked.

    Parameters:
    - fastq_dir: str - Path to the directory containing fastq.gz folders.
    - project: Project - The project to link the Nanopore sequences to.

    Returns:
    - JsonResponse: Status and any warnings generated during the process.
    """
    if not os.path.isdir(fastq_dir):
        return JsonResponse({'error': 'Directory not found'}, status=400)

    # Initialize the warnings list and added_sequences flag
    warnings = []
    nanopore_sequence_ids = []
    sequences_added = False

    def process_folder(folder_name, folder_path):
        nonlocal sequences_added
        existing_pns = ProjectNanoporeSequence.objects.filter(
            project=project, nanopore_sequence__name=folder_name
        ).exists()

        if existing_pns:
            warnings.append(f"NanoporeSequence '{folder_name}' already exists for this project.")
            return

        fastq_files = [f for f in os.listdir(folder_path) if f.endswith('.fastq.gz') or f.endswith(".fq.gz")]
        if not fastq_files:
            return

        output_file = os.path.join(fastq_dir, f"{folder_name}.fastq.gz")
        if not os.path.exists(output_file):
            with gzip.open(output_file, 'wb') as f_out:
                for fastq_file in fastq_files:
                    file_path = os.path.join(folder_path, fastq_file)
                    with gzip.open(file_path, 'rb') as f_in:
                        shutil.copyfileobj(f_in, f_out)
        nanopore_sequence, created = NanoporeSequence.objects.get_or_create(
            name=folder_name,
            file=output_file
        )
        nanopore_sequence_ids.append(nanopore_sequence.id)

        ProjectNanoporeSequence.objects.get_or_create(
            project=project,
            nanopore_sequence=nanopore_sequence
        )

        sequences_added = True

    # Traverse directory structure to find folders containing .fastq.gz files
    for root, dirs, files in os.walk(fastq_dir):
        # Process files directly in the root directory
        if not skip_root and root == fastq_dir:
            if any(f.endswith('.fastq.gz') or f.endswith('.fq.gz') for f in files):
                process_folder(base_path if base_path else os.path.basename(root), root)
        # Process each subdirectory containing .fastq.gz files
        for directory in dirs:
            dir_path = os.path.join(root, directory)
            if any(f.endswith('.fastq.gz') or f.endswith(".fq.gz") for f in os.listdir(dir_path)):
                # Process each subfolder containing fastq.gz files
                process_folder(directory, dir_path)

    return {'status': 'success', 'message': 'Processing completed.', 'warnings': warnings, 'sequences_added': sequences_added, 'nanopore_sequence_ids': nanopore_sequence_ids}

def process_nanopore_sample_set(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    set_ids = data.get('set_id')

    if not set_ids:
        return JsonResponse({'error': 'set_id is required'}, status=400)

    # Convert set_id to a list if it's a string
    if isinstance(set_ids, str):
        set_ids = [set_ids]

    if not isinstance(set_ids, list):
        return JsonResponse({'error': 'set_id must be a list or a string'}, status=400)

    responses = []
    for set_id in set_ids:
        try:
            nanopore_sample_set = NanoporeSampleSet.objects.get(id=set_id)
            sample_dir = nanopore_sample_set.directory
            json_resp = upload_fastq_directory(sample_dir, project, skip_root=True)
            responses.append({
                'set_id': set_id,
                'status': 'success',
                'data': json_resp
            })
        except NanoporeSampleSet.DoesNotExist:
            responses.append({
                'set_id': set_id,
                'status': 'error',
                'error': f'NanoporeSampleSet with id {set_id} not found.'
            })
        except Exception as e:
            responses.append({
                'set_id': set_id,
                'status': 'error',
                'error': str(e)
            })

    return JsonResponse({'results': responses, 'status': 'success'}, status=200)

def get_nanopore_sample_sets(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Invalid request method, only GET allowed'}, status=405)

    # Get all NanoporeSampleSet objects
    nanopore_sample_sets = NanoporeSampleSet.objects.all()

    # Initialize a dictionary to store data grouped by category
    grouped_data = {}

    # Group the NanoporeSampleSet objects by their category
    for sample_set in nanopore_sample_sets:
        category = sample_set.category
        if category not in grouped_data:
            grouped_data[category] = []
        grouped_data[category].append({
            'id': sample_set.id,
            'name': sample_set.name
        })

    # Return the data as JSON
    return JsonResponse(grouped_data)

def get_reference_sets(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Invalid request method, only GET allowed'}, status=405)

    # Get all NanoporeSampleSet objects
    reference_sets = ReferenceData.objects.all()

    # Initialize a dictionary to store data grouped by category
    grouped_data = {}

    # Group the NanoporeSampleSet objects by their category
    for reference_set in reference_sets:
        category = reference_set.category
        if category not in grouped_data:
            grouped_data[category] = []
        grouped_data[category].append({
            'id': reference_set.id,
            'name': reference_set.name
        })

    # Return the data as JSON
    return JsonResponse(grouped_data)

def get_sample_sets(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Invalid request method, only GET allowed'}, status=405)

    # Get all NanoporeSampleSet objects
    sample_sets = SampleData.objects.all()

    # Initialize a dictionary to store data grouped by category
    grouped_data = {}

    # Group the NanoporeSampleSet objects by their category
    for sample_set in sample_sets:
        category = sample_set.category
        if category not in grouped_data:
            grouped_data[category] = []
        grouped_data[category].append({
            'id': sample_set.id,
            'name': sample_set.name
        })

    # Return the data as JSON
    return JsonResponse(grouped_data)

def submit_fastp_job(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']

    nanopore_sequence_ids = data.get('nanopore_sequence_id')

    if not nanopore_sequence_ids:
        return JsonResponse({'error': 'nanopore_sequence_id is required'}, status=400)

    # Normalize to a list if a single ID is provided
    if isinstance(nanopore_sequence_ids, int):
        nanopore_sequence_ids = [nanopore_sequence_ids]

    # Prepare a list to track jobs that were created
    jobs_created = []
    already_existing_jobs = []

    for nanopore_sequence_id in nanopore_sequence_ids:
        # Get the ProjectNanoporeSequence for the given nanopore sequence and project
        project_nanopore_sequence = get_object_or_404(ProjectNanoporeSequence,
                                                      project=project,
                                                      nanopore_sequence_id=nanopore_sequence_id)

        # Check if a FastpJob already exists for this sequence and project
        existing_job = FastpJob.objects.filter(nanopore_sequence_id=nanopore_sequence_id, project_id=project.id).exclude(status='failed').first()
        if existing_job:
            already_existing_jobs.append(nanopore_sequence_id)
            continue

        # Create a new FastpJob
        fastp_job = FastpJob.objects.create(
            nanopore_sequence_id=nanopore_sequence_id,
            project_id=project.id
        )

        # Queue the fastp task asynchronously with Celery
        run_fastp_task.delay(fastp_job.id)

        # Track the created job
        jobs_created.append(nanopore_sequence_id)

    # Return a response indicating the result
    if jobs_created:
        return JsonResponse({
            'status': 'success',
            'message': 'Fastp jobs created for sequences',
            'jobs_created': jobs_created,
            'already_existing_jobs': already_existing_jobs
        })

    return JsonResponse({
        'error': 'Fastp jobs already exist for all provided sequences',
        'already_existing_jobs': already_existing_jobs
    }, status=400)

def run_porechop(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    nanopore_sequence_ids = data.get('nanopore_sequence_id')  # Can be a single ID or a list

    if not nanopore_sequence_ids:
        return JsonResponse({'error': 'nanopore_sequence_id is required'}, status=400)

    # Normalize to a list if a single ID is provided
    if isinstance(nanopore_sequence_ids, int):
        nanopore_sequence_ids = [nanopore_sequence_ids]

    # Prepare lists to track job statuses
    jobs_created = []
    already_existing_jobs = []
    missing_fastp_files = []
    missing_project_nanopore_sequences = []

    # Loop through each nanopore_sequence_id to process them
    for nanopore_sequence_id in nanopore_sequence_ids:
        # Retrieve the ProjectNanoporeSequence for the user and project
        project_nanopore_sequence = ProjectNanoporeSequence.objects.filter(
            project__id=project.id, nanopore_sequence__id=nanopore_sequence_id, project__user=request.user
        ).first()

        if not project_nanopore_sequence:
            missing_project_nanopore_sequences.append(nanopore_sequence_id)
            continue

        # Check if there's a FastpResult with a filtered_file
        fastp_result = FastpResult.objects.filter(project_nanopore_sequence=project_nanopore_sequence).first()

        if not fastp_result or not fastp_result.filtered_file:
            missing_fastp_files.append(nanopore_sequence_id)
            continue

        # Check if a PorechopJob already exists for this project_nanopore_sequence
        existing_job = PorechopJob.objects.filter(
            nanopore_sequence=project_nanopore_sequence.nanopore_sequence,
            project=project_nanopore_sequence.project
        ).exclude(status='failed').first()

        if existing_job:
            already_existing_jobs.append(nanopore_sequence_id)
            continue

        # Create a new PorechopJob
        PorechopJob.objects.create(
            nanopore_sequence=project_nanopore_sequence.nanopore_sequence,
            project=project_nanopore_sequence.project,
            status='pending'
        )

        # Run the porechop task asynchronously
        run_porechop_task.delay(project_nanopore_sequence.id, fastp_result.filtered_file.path)

        # Track the created job
        jobs_created.append(nanopore_sequence_id)

    # Return a response indicating the result
    response = {
        'jobs_created': jobs_created,
        'already_existing_jobs': already_existing_jobs,
        'missing_fastp_files': missing_fastp_files,
        'missing_project_nanopore_sequences': missing_project_nanopore_sequences,
    }

    if jobs_created:
        response['status'] = 'success'
        return JsonResponse(response)

    response['error'] = 'No new Porechop jobs could be created'
    return JsonResponse(response, status=400)

def run_medaka(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    nanopore_sequence_ids = data.get('nanopore_sequence_id')  # Can be a single ID or a list

    if not nanopore_sequence_ids:
        return JsonResponse({'error': 'nanopore_sequence_id is required'}, status=400)

    # Normalize to a list if a single ID is provided
    if isinstance(nanopore_sequence_ids, int):
        nanopore_sequence_ids = [nanopore_sequence_ids]

    # Prepare lists to track job statuses
    jobs_created = []
    already_existing_jobs = []
    missing_porechop_results = []
    missing_project_nanopore_sequences = []

    # Loop through each nanopore_sequence_id to process them
    for nanopore_sequence_id in nanopore_sequence_ids:
        # Check if the ProjectNanoporeSequence exists
        project_nanopore_sequence = ProjectNanoporeSequence.objects.filter(
            project_id=project.id, nanopore_sequence_id=nanopore_sequence_id
        ).first()

        if not project_nanopore_sequence:
            missing_project_nanopore_sequences.append(nanopore_sequence_id)
            continue

        # Check if a PorechopResult exists for this ProjectNanoporeSequence
        porechop_result = PorechopResult.objects.filter(
            project_nanopore_sequence=project_nanopore_sequence
        ).first()
        if not porechop_result:
            missing_porechop_results.append(nanopore_sequence_id)
            continue

        # Ensure MedakaJob is not already running
        existing_job = MedakaJob.objects.filter(
            nanopore_sequence_id=nanopore_sequence_id, project_id=project.id
        ).exclude(status='failed').first()
        if existing_job:
            already_existing_jobs.append(nanopore_sequence_id)
            continue

        # Create a MedakaJob and trigger the task
        MedakaJob.objects.create(
            nanopore_sequence_id=nanopore_sequence_id,
            project_id=project.id,
            status='pending'
        )
        run_medaka_task.delay(project_nanopore_sequence.id)

        # Track the created job
        jobs_created.append(nanopore_sequence_id)

    # Return a response indicating the result
    response = {
        'jobs_created': jobs_created,
        'already_existing_jobs': already_existing_jobs,
        'missing_porechop_results': missing_porechop_results,
        'missing_project_nanopore_sequences': missing_project_nanopore_sequences,
    }

    if jobs_created:
        response['status'] = 'success'
        return JsonResponse(response)

    response['error'] = 'No new Medaka jobs could be created'
    return JsonResponse(response, status=400)

def upload_blast_results(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    blast_result_ids = data.get('blast_result_ids')

    # Get the names of existing ProjectDataFiles for this project to avoid duplicates
    existing_data_file_names = ProjectDataFile.objects.filter(project=project).values_list('data_file__name', flat=True)

    warnings = []
    sequences_added = False
    processed_accessions = set()  # Track processed accessions to handle new duplicates

    # Process each BlastResult by ID
    for blast_result_id in blast_result_ids:
        blast_result = get_object_or_404(BlastResult, id=blast_result_id)
        details = blast_result.details if blast_result.details else ""
        # Clean the details string by removing non-alphanumeric characters except spaces
        cleaned_details = re.sub(r'[^a-zA-Z0-9\s]', '', details)
        accession = (f"{blast_result.accession}|{'_'.join(cleaned_details.split()[:2])}")[:64]

        # Check if a DataFile with the same name already exists for this project
        if accession in existing_data_file_names or accession in processed_accessions:
            warnings.append("Skipped sequence with name " + blast_result.accession + " as a sequence with that name already exists in this project.")
            continue  # Skip this blast result if its name already exists

        # Create a new DataFile for each blast result
        data_file = DataFile.objects.create(
            user=request.user,
            name=accession,
            reads=blast_result.sequence,
            read_type="B",
            source="hit",
            accession_number=blast_result.accession,
            source_file_id=blast_result.blast_data.blast_file,
        )

        header = re.sub(r'\s+', '_', accession)
        # Create the FASTA file content
        fasta_content = f">{header}\n{blast_result.sequence}"

        # Generate a unique file name based on the data file ID
        fasta_file_name = f"{data_file.id}.fasta"

        # Save the FASTA file using default_storage
        fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", ContentFile(fasta_content))

        # Assign the path to the associated_fasta field and save the DataFile instance
        data_file.associated_fasta.name = fasta_file_path
        data_file.save()

        # Create a ProjectDataFile to associate the DataFile with the project
        ProjectDataFile.objects.create(
            project=project,
            data_file=data_file
        )
        sequences_added = True
        processed_accessions.add(accession)  # Mark this accession as processed

    return JsonResponse({'message': 'Blast results uploaded successfully.', 'sequences_added': sequences_added, 'warnings': warnings}, status=201)

def process_reference_data(request):
    PROTOCOL = request.scheme + "://"
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    reference_ids = data.get('reference_id')  # Can be a single ID or a list of IDs
    sample_ids = data.get('sample_id')  # Can be a single ID or a list of IDs

    # Check for missing parameters
    if not reference_ids and not sample_ids:
        return JsonResponse({'error': 'reference_id or sample_id is required'}, status=400)

    # Convert reference_ids and sample_ids to a list if they are single values
    if not isinstance(reference_ids, list):
        reference_ids = [reference_ids]
    if sample_ids and not isinstance(sample_ids, list):
        sample_ids = [sample_ids]

    warnings = []
    sequences_added = False

    # Process each reference ID in the list
    if reference_ids:
        # Process each reference ID in the list
        for reference_id in reference_ids:
            # Get the ReferenceData instance
            reference_data = get_object_or_404(ReferenceData, id=reference_id)
            reference_file_path = reference_data.file.path

            # Check if the reference file exists
            if not os.path.isfile(reference_file_path):
                warnings.append(f"Reference file not found for {reference_data.name}.")
                continue

            # Process the multi-sequence FASTA file
            sequences = SeqIO.parse(reference_file_path, "fasta")
            for seq in sequences:
                sequence_str = str(seq.seq)
                name = seq.id

                # Check if DataFile already exists
                existing_file = DataFile.objects.filter(
                    reads=sequence_str,
                    reference_data=reference_data
                ).first()

                if not existing_file:
                    # Save the single-sequence FASTA file
                    fasta_path = os.path.join('fasta_files/reference', reference_data.category, f"{name}.fasta")
                    os.makedirs(os.path.dirname(fasta_path), exist_ok=True)
                    with open(fasta_path, 'w') as fasta_file:
                        fasta_file.write(f">{name}\n{sequence_str}\n")
                # Create a new DataFile
                data_file = DataFile.objects.create(
                    user=request.user,  # Adjust the user as appropriate
                    name=name,
                    reads=sequence_str,
                    read_type='E',  # Set to Reference
                    source="reference",
                    reference_data=reference_data,
                    associated_fasta=f"fasta_files/reference/{reference_data.category}/{name}.fasta"
                )

                # Link the new DataFile to the project
                ProjectDataFile.objects.create(project=project, data_file=data_file)
                sequences_added = True

    # Process each sample ID in the list
    if sample_ids:
        for sample_id in sample_ids:
            # Get the SampleData instance
            sample_data = get_object_or_404(SampleData, id=sample_id)
            sample_file_path = sample_data.file.path

            # Check if the sample file exists
            if not os.path.exists(sample_file_path):
                warnings.append(f"Sample file not found for {sample_data.name}.")
                continue
            if sample_data.file_type == 'fasta':
                # Process the multi-sequence FASTA file for sample data
                sequences = SeqIO.parse(sample_file_path, "fasta")
                for seq in sequences:
                    sequence_str = str(seq.seq)
                    name = seq.id

                    # Check if DataFile already exists
                    existing_file = DataFile.objects.filter(
                        reads=sequence_str,
                        sample_data=sample_data
                    ).first()

                    if not existing_file:
                        # Save the single-sequence FASTA file
                        fasta_path = os.path.join('fasta_files/sample', sample_data.category, f"{name}.fasta")
                        os.makedirs(os.path.dirname(fasta_path), exist_ok=True)
                        with open(fasta_path, 'w') as fasta_file:
                            fasta_file.write(f">{name}\n{sequence_str}\n")
                    # Create a new DataFile for the sample
                    data_file = DataFile.objects.create(
                        user=request.user,
                        name=name,
                        reads=sequence_str,
                        read_type='F',
                        source="sample",
                        sample_data=sample_data,
                        associated_fasta=f"fasta_files/sample/{sample_data.category}/{name}.fasta"
                    )


                    # Link the new DataFile to the project
                    ProjectDataFile.objects.create(project=project, data_file=data_file)
                    sequences_added = True
            elif sample_data.file_type == 'abi':
                # Process the directory of ABI files
                abi_dir = sample_file_path
                if os.path.isdir(abi_dir):
                    for abi_file_name in os.listdir(abi_dir):
                        abi_file_path = os.path.join(sample_data.file.name, abi_file_name)
                        file_url = PROTOCOL + request.get_host() + "/backend/" + abi_file_path
                        name = cleanSequenceName(file_url)
                        if ProjectDataFile.objects.filter(project=project, data_file__name=name):
                            continue
                        message, sequence, trace_exists, record, _ = parse_reads(file_url)
                        if message:
                            continue
                        # Create a new DataFile for each ABI file
                        new_data_file = DataFile.objects.create(
                                user=request.user,
                                name=name,
                                reads=sequence,  # ABI files don't have readable sequence data like FASTA
                                read_type='F',
                                sample_data=sample_data,
                                source="sample",
                                associated_abi=abi_file_path,
                                trace_exists=True
                        )
                        header = re.sub(r'\s+', '_', new_data_file.name)
                        fasta_content = ContentFile(f">{header}\n{new_data_file.reads}\n")
                        fasta_file_name = f"{new_data_file.id}.fasta"
                        fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", fasta_content)
                        new_data_file.associated_fasta.name = fasta_file_path
                        new_data_file.save()

                        # Link the new DataFile to the project
                        ProjectDataFile.objects.create(project=project, data_file=new_data_file)
                        sequences_added = True
                else:
                    warnings.append(f"ABI directory not found for {sample_data.name}.")

    # Return a response indicating the process result
    if sequences_added:
        return JsonResponse({'success': 'Sequences processed and added successfully.', 'warnings': warnings})
    else:
        return JsonResponse({"status": "error", 'message': 'No new sequences added.', 'warnings': warnings})

def copy_file_to_storage(file_field, destination_name):
    # Open the original file
    with file_field.open('rb') as src_file:
        # Save to the destination path within the storage system
        default_storage.save(destination_name, src_file)

def create_temp_directory():
    project_root = settings.BASE_DIR

    temp_dir = tempfile.mkdtemp(dir=project_root)

    return temp_dir

def upload_nanopore_directory(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    files = data.get('files')
    if not files:
        return JsonResponse({'error': 'files is required'}, status=400)

    temp_dir = create_temp_directory()
    warnings = []
    base_file_path = ""

    # Organize files by barcode folder
    for relative_path, base64_content in files.items():
        # Decode base64 content to bytes
        try:
            array_buffer = base64.b64decode(base64_content)
            with gzip.open(io.BytesIO(array_buffer), 'rb') as test_gzip:
                test_gzip.read(1)  # Read a small part to confirm it's gzipped
        except (base64.binascii.Error, OSError) as e:
            warnings.append(f"Failed to decode or validate gzip for file {relative_path}: {e}")
            continue

        stripped_filepath = os.path.join(*relative_path.split(os.path.sep)[1:])
        base_file_path = relative_path.split(os.path.sep)[0]
        full_path = os.path.join(temp_dir, stripped_filepath)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, 'wb') as file:
            file.write(array_buffer)

    json_resp = upload_fastq_directory(temp_dir, project, base_file_path)

    nanopore_sequence_ids = json_resp['nanopore_sequence_ids']
    # Define where to save the uploaded files
    base_dir = "fastq_files"

    for nanopore_sequence_id in nanopore_sequence_ids:
        nanopore_sequence = NanoporeSequence.objects.get(id=nanopore_sequence_id)

        concatenated_file_path =  f"{base_dir}/{nanopore_sequence_id}.fastq.gz"

        copy_file_to_storage(nanopore_sequence.file, concatenated_file_path)
        nanopore_sequence.file.name = concatenated_file_path
        nanopore_sequence.save()

    shutil.rmtree(temp_dir)

    # Return a response indicating the process result
    if json_resp['sequences_added']:
        return JsonResponse({'success': 'Sequences processed and added successfully.', 'warnings': warnings + json_resp['warnings']})
    else:
        return JsonResponse({"status": "error", 'message': "\n" + "\n".join(json_resp['warnings'])})

def upload_nanopore_files(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    folder = data.get('folder')
    files = data.get('files')

    if not files or not folder:
        return JsonResponse({'error': 'files and folder are required'}, status=400)

    existing_pns = ProjectNanoporeSequence.objects.filter(
        project=project, nanopore_sequence__name=folder
    ).exists()

    if existing_pns:
        return JsonResponse({'error': f"NanoporeSequence '{folder}' already exists for this project."}, status=400)

    temp_dir = create_temp_directory()
    warnings = []

    # Organize files by barcode folder
    for relative_path, base64_content in files.items():
        # Decode base64 content to bytes
        try:
            array_buffer = base64.b64decode(base64_content)
            with gzip.open(io.BytesIO(array_buffer), 'rb') as test_gzip:
                test_gzip.read(1)  # Read a small part to confirm it's gzipped
        except (base64.binascii.Error, OSError) as e:
            warnings.append(f"Failed to decode or validate gzip for file {relative_path}: {e}")
            continue

        full_path = os.path.join(temp_dir, relative_path)

        with open(full_path, 'wb') as file:
            file.write(array_buffer)

    # Create a NanoporeSequence if it doesn't exist
    nanopore_sequence = NanoporeSequence.objects.create(name=folder)
    # Link NanoporeSequence to the project
    ProjectNanoporeSequence.objects.create(
        project=project,
        nanopore_sequence=nanopore_sequence
    )
    fastq_files = [f for f in os.listdir(temp_dir) if f.endswith('.fastq.gz') or f.endswith(".fq.gz")]
    base_dir = "fastq_files"
    concatenated_file_path =  f"{base_dir}/{nanopore_sequence.id}.fastq.gz"
    if not os.path.exists(concatenated_file_path):
        with gzip.open(concatenated_file_path, 'wb') as f_out:
            for fastq_file in fastq_files:
                file_path = os.path.join(temp_dir, fastq_file)
                with gzip.open(file_path, 'rb') as f_in:
                    shutil.copyfileobj(f_in, f_out)
    nanopore_sequence.file.name = concatenated_file_path
    nanopore_sequence.save()
    print(temp_dir)
    #shutil.rmtree(temp_dir)
    # Return a response indicating the process result
    return JsonResponse({'success': 'Sequences processed and added successfully.'})

def rename_nanopore_file(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method, only POST allowed'}, status=405)
    try:
        data = json.loads(request.body)
        pid = data.get('pid')
        ns_id = data.get('nanoporeSeqId')
        seq_name = data.get('seqName')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not pid or not seq_name or not ns_id:
        return JsonResponse({'error': 'pid and seq_name and ns_id are required'}, status=400)

    # Get the project associated with the given pid
    project = get_object_or_404(Project, id=pid)
    nanopore_sequence = NanoporeSequence.objects.get(id=ns_id)
    existing_pns = ProjectNanoporeSequence.objects.filter(
        project=project, nanopore_sequence=nanopore_sequence
    ).exists()
    if not existing_pns:
        return JsonResponse({'error': "NanoporeSequence does not exist for this project."}, status=400)

    sample = nanopore_sequence.file.name.startswith("fastq_files/sample/")
    if sample:
        return JsonResponse({'error': "Cannot edit this NanoporeSequence."}, status=400)
    existing_pns = ProjectNanoporeSequence.objects.filter(
        project=project, nanopore_sequence__name=seq_name
    ).exists()
    if existing_pns:
        return JsonResponse({'error': f"NanoporeSequence '{seq_name}' already exists for this project."}, status=400)

    nanopore_sequence.name = seq_name
    nanopore_sequence.save()

    datafiles = DataFile.objects.filter(source='nanopore', nanopore_seq_id=nanopore_sequence.id)
    for data_file in datafiles:
        data_file.name = seq_name
        if data_file.associated_fasta:
            header = re.sub(r'\s+', '_', seq_name)
            fasta_content = ContentFile(f">{header}\n{data_file.reads}\n")
            fasta_file_name = f"{data_file.id}.fasta"
            fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", fasta_content)
            data_file.associated_fasta.name = fasta_file_path
        data_file.save()

    return JsonResponse({'success': 'Sequence renamed.'})

def get_azenta_file_quality(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    # Get the 'o' and 'f' parameters from the query string
    o = request.GET.get('o', None)
    f = request.GET.get('f', None)

    # Check if 'o' and 'f' parameters are present
    if o is None or f is None:
        return JsonResponse({'error': 'Missing required parameters.'}, status=400)

    # Make the request to the external API
    api_url = 'https://dnalc.cshl.edu/genewiz/files-new'
    response = requests.get(api_url, params={'o': str(o), 'f': f})

    # Check if the request was successful
    if response.status_code != 200:
        return JsonResponse({'error': 'Failed to retrieve files from external API.'}, status=500)

    quality_map = {}
    try:
        # Iterate over the response data
        for file_info in response.json():
            file_url = file_info.get('file')
            file_id = file_info.get('id')
            quality_map[file_id] = is_low_quality(get_quality_scores(file_url))
    except ValueError:
        return JsonResponse({'error': 'Invalid response from external API.'}, status=500)

    return JsonResponse({'success': 'Quality data checked.', 'quality_map': quality_map}, status=200)

def upload_sanger_files(request):
    PROTOCOL = request.scheme + "://"
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method, only POST allowed'}, status=405)
    try:
        data = json.loads(request.body)
        pid = data.get('pid')
        files = data.get('files')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not pid or not files:
        return JsonResponse({'error': 'pid and files are required'}, status=400)

    # Get the project associated with the given pid
    project = get_object_or_404(Project, id=pid)

    temp_dir = create_temp_directory()
    warnings = []

    # Organize files by barcode folder
    for relative_path, file_data in files.items():
        file_content = file_data.get('content')

        if not file_content:
            warnings.append(f"File {relative_path} has no content.")
            continue

        try:
            # Decode Base64 content
            decoded_bytes = base64.b64decode(file_content)

            # Determine if the file is text or binary
            if is_text_file(decoded_bytes):  # Handle as text
                decoded_str = decoded_bytes.decode('utf-8')
                sequences = SeqIO.parse(io.StringIO(decoded_str), "fasta")
                for seq in sequences:
                    sequence_str = str(seq.seq)
                    name = seq.id
                    if ProjectDataFile.objects.filter(project=project, data_file__name=name):
                        warnings.append(f"File {name} already exists for project.")
                        continue
                    data_file = DataFile.objects.create(
                        user=request.user,
                        name=name,
                        reads=sequence_str,
                        read_type='F',
                        source="upload",
                    )
                    header = re.sub(r'\s+', '_', data_file.name)
                    fasta_content = ContentFile(f">{header}\n{data_file.reads}\n")
                    fasta_file_name = f"{data_file.id}.fasta"
                    fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", fasta_content)
                    data_file.associated_fasta.name = fasta_file_path
                    data_file.save()
                    ProjectDataFile.objects.create(project=project, data_file=data_file)
            else:  # Handle as binary
                name = cleanSequenceName(relative_path)
                if ProjectDataFile.objects.filter(project=project, data_file__name=name):
                    warnings.append(f"File {name} already exists for project.")
                    continue
                data_file = DataFile.objects.create(
                    user=request.user,
                    name=name,
                    reads="",  # ABI files don't have readable sequence data like FASTA
                    read_type='F',
                    trace_exists=True,
                    source="upload",
                )
                abi_content = ContentFile(decoded_bytes)
                abi_file_name = f"{data_file.id}.abi"
                abi_file_path = default_storage.save(f"abi_files/{abi_file_name}", abi_content)
                file_url = PROTOCOL + request.get_host() + "/backend/abi_files/" + abi_file_name
                message, sequence, trace_exists, record, _ = parse_reads(file_url)
                name = record.name
                if message:
                    data_file.delete()
                    continue
                # Create a new DataFile for each ABI file
                data_file.reads = sequence
                data_file.associated_abi.name = abi_file_path
                data_file.save()
                header = re.sub(r'\s+', '_', data_file.name)
                fasta_content = ContentFile(f">{header}\n{data_file.reads}\n")
                fasta_file_name = f"{data_file.id}.fasta"
                fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", fasta_content)
                data_file.associated_fasta.name = fasta_file_path
                data_file.save()

                # Link the new DataFile to the project
                ProjectDataFile.objects.create(project=project, data_file=data_file)
        except Exception as e:
            warnings.append(f"Failed to process file {relative_path}: {e}")

    # Return a response indicating the process result
    return JsonResponse({'success': 'Sequences processed successfully.', 'warnings': warnings})

def upload_bold_data(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method, only POST allowed'}, status=405)
    try:
        data = json.loads(request.body)
        processids = data.get("processids")
        pid = data.get('pid')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    # Check for missing parameters
    if not processids and not pid:
        return JsonResponse({'error': 'processids and pid are required'}, status=400)

    if isinstance(processids, list):
        processids = ",".join(processids)

    # Get the project associated with the given pid
    try:
        project = Project.objects.get(id=pid)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found.'}, status=404)

    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    # Ensure the current user owns the project
    if project.user != request.user:
        return JsonResponse({'error': 'You do not have permission to add to this project.'}, status=403)

    API_KEY = settings.BOLD_API_KEY
    # Construct the API URL
    api_url = (
        "https://data.boldsystems.org/api/records/retrieve?"
        f"processids={processids}&batch_size=5000"
    )
    headers = {
        "accept": "application/json",
        "api-key": API_KEY,
    }
    try:
        response = requests.get(api_url, headers=headers)
        if response.status_code != 200:
            return JsonResponse({'error': 'Could not get data from BOLD.'}, status=400)
        try:
            data = response.json()
        except json.JSONDecodeError:
            try:
                data = [json.loads(line) for line in response.text.splitlines() if line.strip()]
            except json.JSONDecodeError as e:
                return JsonResponse({'error': f'Failed to parse response as JSON: {str(e)}'}, status=500)
    except requests.exceptions.RequestException as e:
        return JsonResponse({"error": f"API request failed: {str(e)}"}, status=500)

    if isinstance(data, dict):  # If it's a single JSON object, wrap it in a list
        data = [data]
    elif not isinstance(data, list):
        return JsonResponse({'error': 'Unexpected response format from BOLD API.'}, status=400)

    warnings = []
    sequences_found = False
    for item in data:
        if "processid" in item and "identification" in item and "nuc" in item:
            sequences_found = True
            process_id = item["processid"]
            identification = item["identification"].split()[0]  # Take part before first whitespace
            reads = item["nuc"]

            name = (process_id + "|" + identification)[:255]

            data_file_exists = ProjectDataFile.objects.filter(project=project, data_file__name=name)
            if data_file_exists:
                warnings.append(f"File with display name {name} already added to this project.")
                continue

            # Save the file to the DataFile model
            data_file = DataFile.objects.create(
                user=request.user,
                name=name,
                read_type="F",
                reads=reads,
                source="bold",
                process_id=process_id,
            )

            header = re.sub(r'\s+', '_', data_file.name)
            fasta_content = ContentFile(f">{header}\n{data_file.reads}\n")
            fasta_file_name = f"{data_file.id}.fasta"
            fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", fasta_content)
            data_file.associated_fasta.name = fasta_file_path
            data_file.save()

            # Create a ProjectDataFile instance
            project_data_file = ProjectDataFile.objects.create(
                project=project,
                data_file=data_file
            )

    if sequences_found:
        return JsonResponse({"status": "success", "warnings": warnings}, status=200)

    return JsonResponse({"error": "Could not find sequences in BOLD"}, status=400)

def upload_genbank_data(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method, only POST allowed'}, status=405)
    try:
        data = json.loads(request.body)
        accessions = data.get("accessions")
        pid = data.get('pid')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    # Check for missing parameters
    if not accessions and not pid:
        return JsonResponse({'error': 'accessions and pid are required'}, status=400)

    if isinstance(accessions, list):
        accessions = ",".join(accessions)

    # Get the project associated with the given pid
    try:
        project = Project.objects.get(id=pid)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found.'}, status=404)

    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    # Ensure the current user owns the project
    if project.user != request.user:
        return JsonResponse({'error': 'You do not have permission to add to this project.'}, status=403)

    params = {
        "db": "nuccore",
        "id": accessions,
        "rettype": "gb",
    }

    GENBANK_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    sequences = []

    try:
        response = requests.get(GENBANK_URL, params=params)
        if response.status_code != 200:
            return JsonResponse({'error': 'Could not get data from GenBank.'}, status=400)
        genbank_data = response.text
        sequences = extract_genbank_data(genbank_data)
    except requests.exceptions.RequestException as e:
        return JsonResponse({"error": f"API request failed: {str(e)}"}, status=500)

    warnings = []
    for seq in sequences:
        name = seq["name"]
        reads = seq["reads"]
        accession_number = seq["accession"]

        data_file_exists = ProjectDataFile.objects.filter(project=project, data_file__name=name)
        if data_file_exists:
            warnings.append(f"File with display name {name} already added to this project.")
            continue

        # Save the file to the DataFile model
        data_file = DataFile.objects.create(
            user=request.user,
            name=name,
            read_type="F",
            reads=reads,
            source="genbank",
            accession_number=accession_number,
        )

        header = re.sub(r'\s+', '_', data_file.name)
        fasta_content = ContentFile(f">{header}\n{data_file.reads}\n")
        fasta_file_name = f"{data_file.id}.fasta"
        fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", fasta_content)
        data_file.associated_fasta.name = fasta_file_path
        data_file.save()

        # Create a ProjectDataFile instance
        project_data_file = ProjectDataFile.objects.create(
            project=project,
            data_file=data_file
        )
    return JsonResponse({"status": "success", "warnings": warnings}, status=200)

def upload_fasta_content(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method, only POST allowed'}, status=405)

    try:
        data = json.loads(request.body)
        fasta_content = data.get("fasta_content")
        pid = data.get('pid')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    # Check for missing parameters
    if not fasta_content or not pid:
        return JsonResponse({'error': 'fasta_content and pid are required'}, status=400)

    # Get the project associated with the given pid
    try:
        project = Project.objects.get(id=pid)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found.'}, status=404)

    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    # Ensure the current user owns the project
    if project.user != request.user:
        return JsonResponse({'error': 'You do not have permission to add to this project.'}, status=403)

    sequences = extract_sequences(fasta_content)
    if len(sequences) < 1:
        return JsonResponse({'error': 'Invalid FASTA format'}, status=400)

    sequences_added = False

    warnings = []
    for seq_record in sequences:
        name = seq_record.id
        reads = str(seq_record.seq)

        # Check if a data file with this name already exists for the project
        data_file_exists = ProjectDataFile.objects.filter(project=project, data_file__name=name)
        if data_file_exists.exists():
            warnings.append(f"File with display name {name} already added to this project.")
            continue

        # Save the file to the DataFile model
        data_file = DataFile.objects.create(
            user=request.user,
            name=name,
            read_type="F",
            reads=reads,
            source="paste",
        )

        header = re.sub(r'\s+', '_', data_file.name)
        fasta_content = ContentFile(f">{header}\n{data_file.reads}\n")
        fasta_file_name = f"{data_file.id}.fasta"
        fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", fasta_content)
        data_file.associated_fasta.name = fasta_file_path
        data_file.save()

        # Create a ProjectDataFile instance
        ProjectDataFile.objects.create(
            project=project,
            data_file=data_file
        )
        sequences_added = True

    return JsonResponse({"status": "success", "warnings": warnings}, status=200)

def request_enhanced_permission(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])
    user = request.user
    data = parsed_data['data']


    if not user or not hasattr(user, 'userprofile'):
        return JsonResponse({'error': 'User profile not found'}, status=400)
    if not user.userprofile.verified:
        return JsonResponse({'error': 'Email address is not verified'}, status=403)
    if user.userprofile.elevated_access:
        return JsonResponse({'error': 'User already has elevated privileges'}, status=400)

    if EnhancedPermissionToken.objects.filter(user=user, status='pending').exists():
        return JsonResponse({'error': 'A pending request already exists'}, status=400)

    reason = data.get('reason')
    if not reason:
        reason = ""

    token_obj = EnhancedPermissionToken.create_token(user, reason)

    return JsonResponse({
        'success': 'Permission request submitted',
        'token': token_obj.token
    }, status=200)

def update_permission_request_status(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    admin_user = request.user
    if not admin_user or not admin_user.is_superuser:
        return JsonResponse({'error': 'Only superusers can update requests'}, status=403)

    data = parsed_data['data']
    action = data.get("action")  # must be "approve" or "deny"
    if action not in ['approve', 'deny']:
        return JsonResponse({'error': 'Invalid action. Must be "approve" or "deny".'}, status=400)
    token = data.get("token")

    try:
        token_obj = EnhancedPermissionToken.objects.get(token=token, status='pending')
    except EnhancedPermissionToken.DoesNotExist:
        return JsonResponse({'error': 'No pending request found with this token'}, status=404)

    if action == 'approve':
        profile = token_obj.user.userprofile
        profile.elevated_access = True
        profile.save()
        token_obj.status = 'approved'
    else:
        token_obj.status = 'denied'

    token_obj.save()
    send_permission_request_result_email(token_obj.user.email, decision=token_obj.status, reason=token_obj.reason)

    return JsonResponse({'success': f'Request {token_obj.status}'}, status=200)

def list_pending_permission_requests(request):
    if request.method != "GET":
        return JsonResponse({'error': 'GET method required'}, status=405)

    if not request.user or not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    if not request.user.is_superuser:
        return JsonResponse({'error': 'Superuser access required'}, status=403)

    pending_approved_tokens = EnhancedPermissionToken.objects.filter(
        status__in=['pending', 'approved']
    ).select_related('user')
    data = [
        {
            'username': token.user.username,
            'email': token.user.email,
            'token': token.token,
            'status': token.status,
            'reason': token.reason
        }
        for token in pending_approved_tokens
    ]

    return JsonResponse({'requests': data}, status=200)

# Helper function
def toggle_datafile_boolean_field(request, field_name):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])
    data = parsed_data['data']
    datafile_id = data.get("datafile_id")
    datafile = get_object_or_404(DataFile, id=datafile_id, user=request.user)
    
    # Toggle the attribute associated with the provided field_name
    setattr(datafile, field_name, not getattr(datafile, field_name))
    datafile.save()
    
    # Return a success response with the updated status of the provided field_name
    return JsonResponse({'success': True, field_name: getattr(datafile, field_name)})

def toggle_visibility(request):
    return toggle_datafile_boolean_field(request, "is_public")

def toggle_sequence_repository(request):
    return toggle_datafile_boolean_field(request, "in_sequence_repository")

# Helper function
def get_filtered_user_datafiles(request, filter_dict, output_name):
    if request.method == 'GET':
        # Get user_id from query parameters
        user_id = request.GET.get('user_id')
        if not user_id:
            user_id = request.user.id if request.user else None
        if not user_id:
            return JsonResponse({'error': 'Missing user_id parameter'}, status=400)

        try:
            # Fetch the user object based on the provided user_id
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)

        # Get all DataFiles for the given user filtered by the provided filter_dict
        filtered_datafiles = DataFile.objects.filter(user=user, **filter_dict)
        data = [
            {
                'datafile_id': datafile.id,
                'name': datafile.name,
                'username': datafile.user.username,
                'created': datafile.created,
                'updated': datafile.updated,
                'is_public': datafile.is_public,
                'associated_abi': datafile.associated_abi.url if datafile.associated_abi else None,
                'authors': [
                    {'first_name': author.first_name, 'last_name': author.last_name, 'affiliation': author.affiliation}
                    for author in datafile.authors.all()
                ],
                'specimen': {
                    'codon': datafile.specimen.codon if hasattr(datafile, 'specimen') and datafile.specimen.codon else None,
                    'institution_storing': datafile.specimen.institution_storing if hasattr(datafile, 'specimen') and datafile.specimen.institution_storing else None,
                    'identifier_name': datafile.specimen.identifier_name if hasattr(datafile, 'specimen') and datafile.specimen.identifier_name else None,
                    'identifier_email': datafile.specimen.identifier_email if hasattr(datafile, 'specimen') and datafile.specimen.identifier_email else None,
                    'genus': datafile.specimen.genus if hasattr(datafile, 'specimen') and datafile.specimen.genus else None,
                    'species': datafile.specimen.species if hasattr(datafile, 'specimen') and datafile.specimen.species else None,
                    'date_collected': datafile.specimen.date_collected.strftime('%Y-%m-%d') if hasattr(datafile, 'specimen') and datafile.specimen.date_collected else None,
                    'country': datafile.specimen.country if hasattr(datafile, 'specimen') and datafile.specimen.country else None,
                    'state_province': datafile.specimen.state_province if hasattr(datafile, 'specimen') and datafile.specimen.state_province else None,
                    'city': datafile.specimen.city if hasattr(datafile, 'specimen') and datafile.specimen.city else None,
                    'habitat': datafile.specimen.habitat if hasattr(datafile, 'specimen') and datafile.specimen.habitat else None,
                    'exact_site': datafile.specimen.exact_site if hasattr(datafile, 'specimen') and datafile.specimen.exact_site else None,
                    'isolation_source': datafile.specimen.isolation_source if hasattr(datafile, 'specimen') and datafile.specimen.isolation_source else None,
                    'sample_collected_from_host': datafile.specimen.sample_collected_from_host if hasattr(datafile, 'specimen') and datafile.specimen.sample_collected_from_host else None,
                    'host_organism_name': datafile.specimen.host_organism_name if hasattr(datafile, 'specimen') and datafile.specimen.host_organism_name else None,
                    'latitude': datafile.specimen.latitude if hasattr(datafile, 'specimen') and datafile.specimen.latitude else None,
                    'longitude': datafile.specimen.longitude if hasattr(datafile, 'specimen') and datafile.specimen.longitude else None,
                    'altitude': datafile.specimen.altitude if hasattr(datafile, 'specimen') and datafile.specimen.altitude else None,
                    'notes': datafile.specimen.notes if hasattr(datafile, 'specimen') and datafile.specimen.notes else None,
                    'sex': datafile.specimen.sex if hasattr(datafile, 'specimen') and datafile.specimen.sex else None,
                    'reproduction': datafile.specimen.reproduction if hasattr(datafile, 'specimen') and datafile.specimen.reproduction else None,
                    'life_stage': datafile.specimen.life_stage if hasattr(datafile, 'specimen') and datafile.specimen.life_stage else None,
                    'primer_used': datafile.specimen.primer_used if hasattr(datafile, 'specimen') and datafile.specimen.primer_used else None,
                }
            }
            for datafile in filtered_datafiles
        ]

        return JsonResponse({output_name: data}, safe=False)

    return JsonResponse({'error': 'Invalid request method'}, status=400)

def get_public_datafiles(request):
    """Retrieve all public DataFiles for a specific user based on user_id."""
    return get_filtered_user_datafiles(request, {"is_public": True}, "public_datafiles")

def get_sequence_repository_datafiles(request):
    """Retrieve all DataFiles for a specific user that are in the sequence repository."""
    return get_filtered_user_datafiles(request, {"in_sequence_repository": True}, "sequence_repository_datafiles")


def add_specimen(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])
    specimen_data = parsed_data['data']
    datafile_id = specimen_data.get("datafile_id")
    datafile = get_object_or_404(DataFile, id=datafile_id, user=request.user)

    specimen, created = Specimen.objects.update_or_create(
        datafile=datafile,
        defaults={
            'codon': specimen_data.get('codon'),
            'institution_storing': specimen_data.get('institution_storing'),
            'identifier_name': specimen_data.get('identifier_name'),
            'identifier_email': specimen_data.get('identifier_email'),
            'genus': specimen_data.get('genus'),
            'species': specimen_data.get('species'),
            'date_collected': specimen_data.get('date_collected'),
            'country': specimen_data.get('country'),
            'state_province': specimen_data.get('state_province'),
            'city': specimen_data.get('city'),
            'habitat': specimen_data.get('habitat'),
            'exact_site': specimen_data.get('exact_site'),
            'isolation_source': specimen_data.get('isolation_source'),
            'sample_collected_from_host': specimen_data.get('sample_collected_from_host'),
            'host_organism_name': specimen_data.get('host_organism_name'),
            'latitude': specimen_data.get('latitude'),
            'longitude': specimen_data.get('longitude'),
            'altitude': specimen_data.get('altitude'),
            'notes': specimen_data.get('notes'),
            'sex': specimen_data.get('sex'),
            'reproduction': specimen_data.get('reproduction'),
            'life_stage': specimen_data.get('life_stage'),
            'primer_used': specimen_data.get('primer_used'),
        }
    )

    return JsonResponse({'success': True, 'specimen_id': specimen.id})

def trim_muscle_alignment(request):
    if request.method == 'POST':
        try:
            # Parse JSON data from the request
            data = json.loads(request.body)
            left_trim = data.get('left_trim', 0)
            right_trim = data.get('right_trim', 0)
            muscle_data_id = data.get('muscle_data_id')

            muscle_data = get_object_or_404(MuscleData, id=muscle_data_id)

            # If original_associated_alignment doesn't exist, create a backup
            if not muscle_data.original_associated_alignment:
                original_associated_alignment_name = muscle_data.associated_alignment.name.replace(".fasta", "-original.fasta")
                shutil.copyfile(muscle_data.associated_alignment.name, original_associated_alignment_name)
                muscle_data.original_associated_alignment.name = original_associated_alignment_name
                muscle_data.save()

            # Prepare a list to store the trimmed sequences
            trimmed_sequences = []
            consensus = muscle_data.consensus  # Access the MuscleConsensus instance

            if consensus:
                trimmed_sequence = consensus.sequence[left_trim:len(consensus.sequence) - right_trim]
                trimmed_sequences.append(trimmed_sequence)

            # Trim each sequence and write the updated content to the file
            trimmed_records = []
            for record in SeqIO.parse(muscle_data.associated_alignment.name, "fasta"):
                trimmed_seq = record.seq[left_trim:len(record.seq) - right_trim]
                record.seq = trimmed_seq
                trimmed_records.append(record)

            # Write the trimmed sequences back to the file
            SeqIO.write(trimmed_records, muscle_data.associated_alignment.name, "fasta")

            # Create a new MuscleTrim record to record the trimming details
            muscle_trim, created = MuscleTrim.objects.update_or_create(
                muscle_data=muscle_data,
                defaults={'left_trim': left_trim, 'right_trim': right_trim},
                #create_defaults={'first_name': 'Bob', 'birthday': date(1940, 10, 9)}
            )

            return JsonResponse({'status': 'success', 'message': 'Alignment trimmed successfully'}, status=200)

        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


def undo_muscle_trim(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            muscle_data_id = data.get('muscle_data_id')

            muscle_data = get_object_or_404(MuscleData, id=muscle_data_id)

            # If a backup exists, revert the associated_alignment to its original state
            if muscle_data.original_associated_alignment:
                shutil.copyfile(muscle_data.original_associated_alignment.name, muscle_data.associated_alignment.name)
                try:
                    os.remove(muscle_data.original_associated_alignment.name)
                except:
                    pass
                muscle_data.original_associated_alignment = None  # Clear the backup

            # Reset the trim values in the MuscleTrim record
            MuscleTrim.objects.filter(muscle_data=muscle_data).update(left_trim=0, right_trim=0)

            muscle_data.save()

            return JsonResponse({'status': 'success', 'message': 'Trim reverted successfully.'}, status=200)

        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

# Function to duplicate datafiles
def duplicate_datafiles(request):
    # Parse user and project data
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    user = project.user  # Current user associated with the project

    datafile_ids = data.get('datafile_id', [])  # Array of datafile IDs from the input
    new_datafiles = []
    warnings = []

    for datafile_id in datafile_ids:
        try:
            # Retrieve the original datafile
            original_datafile = DataFile.objects.get(id=datafile_id)
            if original_datafile.user != user and not original_datafile.is_public:
                warnings.append(f"DataFile with ID {datafile_id} is not yours and not public.")
                continue
            data_file_exists = ProjectDataFile.objects.filter(project=project, data_file__name=original_datafile.name)
            if data_file_exists:
                warnings.append(f"File with display name {original_datafile.name} already added to this project.")
                continue

            new_datafile = DataFile.objects.get(id=datafile_id)

            # Duplicate the datafile with a new ID
            new_datafile.pk = None
            new_datafile._state.adding = True
            new_datafile.source="saved"
            new_datafile.source_file_id=original_datafile
            new_datafile.user=user
            new_datafile.in_sequence_repository=False
            new_datafile.is_public=False
            new_datafile.save()

            # Handle associated ABI file
            if original_datafile.associated_abi:
                abi_content = original_datafile.associated_abi.read()
                abi_name = f"abi_files/{new_datafile.id}.abi"
                new_datafile.associated_abi.save(
                    f"{new_datafile.id}.abi",
                    ContentFile(abi_content)
                )

            # Handle associated FASTA file
            if original_datafile.associated_fasta:
                fasta_content = original_datafile.associated_fasta.read()
                fasta_name = f"fasta_files/{new_datafile.id}.fasta"
                new_datafile.associated_fasta.save(
                    f"{new_datafile.id}.fasta",
                    ContentFile(fasta_content)
                )

            # Save the new datafile
            new_datafile.save()

            # Associate the new datafile with the project
            project_data_file = ProjectDataFile.objects.create(
                project=project,
                data_file=new_datafile
            )

            new_datafiles.append(new_datafile.id)
        except DataFile.DoesNotExist:
            warnings.append(f"DataFile with ID {datafile_id} does not exist.")
        except Exception as e:
            warnings.append(str(e))

    if len(new_datafiles) > 0:
        return JsonResponse({"status": "success", "new_datafile_ids": new_datafiles, "warnings": warnings}, status=200)
    else:
        return JsonResponse({"error": "No datafiles were able to be added.", "warnings": warnings}, status=400)

def get_phylip_outtree(request, method, data_id):
    if method == 'nj':
        try:
            data = PhylipNJData.objects.get(id=data_id)
        except PhylipNJData.DoesNotExist:
            raise Http404
    elif method == 'ml':
        try:
            data = PhylipMLData.objects.get(id=data_id)
        except PhylipMLData.DoesNotExist:
            raise Http404
    else:
        raise Http404

    response = HttpResponse(data.outtree, content_type='text/plain')
    response['Content-Disposition'] = f'attachment; filename="{method}_{data_id}.newick"'
    return response

def rename_sanger_file(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_id = data.get('sangerSeqId')
    seq_name = data.get('seqName')

    if not file_id:
        return JsonResponse({'error': 'file_id is required'}, status=400)

    # Get the project and data file
    try:
        data_file = DataFile.objects.get(id=file_id)
    except DataFile.DoesNotExist:
        return JsonResponse({'error': 'Data file not found'}, status=404)

    if data_file.source == "sample" or data_file.source == "reference":
        return JsonResponse({'error': 'You cannot rename sample or reference files'}, status=400)

    # Verify the file belongs to the project
    if not ProjectDataFile.objects.filter(project=project, data_file=data_file).exists():
        return JsonResponse({'error': 'Data file does not belong to this project'}, status=403)

    # Check if the name already exists in the project
    if ProjectDataFile.objects.filter(
        project=project,
        data_file__name=seq_name
    ).exclude(data_file_id=file_id).exists():
        return JsonResponse({'error': f"A sequence with name '{seq_name}' already exists in this project, please choose a new name or rename the project file"}, status=400)

    # Rename the file
    data_file.name = seq_name
    if data_file.associated_fasta:
        header = re.sub(r'\s+', '_', seq_name)
        fasta_content = ContentFile(f">{header}\n{data_file.reads}\n")
        fasta_file_name = f"{data_file.id}.fasta"
        fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", fasta_content)
        data_file.associated_fasta.name = fasta_file_path
    data_file.save()

    return JsonResponse({'success': 'Sequence renamed successfully'})

def toggle_sequence_read_type(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_id = data.get('sangerSeqId')

    if not file_id:
        return JsonResponse({'error': 'file_id is required'}, status=400)

    # Get the project and data file
    try:
        data_file = DataFile.objects.get(id=file_id)
    except DataFile.DoesNotExist:
        return JsonResponse({'error': 'Data file not found'}, status=404)

    project_data_file = ProjectDataFile.objects.filter(project=project, data_file=data_file).first()

    # Verify the file belongs to the project
    if not project_data_file:
        return JsonResponse({'error': 'Data file does not belong to this project'}, status=403)

    if data_file.read_type == "F":
        data_file.read_type = "R"
        data_file.save()
        return JsonResponse({'success': 'Sequence read type now reverse'})
    if data_file.read_type == "R":
        data_file.read_type = "F"
        data_file.save()
        return JsonResponse({'success': 'Sequence read type now forward'})
    return JsonResponse({'success': 'Sequence read type toggled'})

def delete_sanger_file(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_id = data.get('sangerSeqId')

    if not file_id:
        return JsonResponse({'error': 'file_id is required'}, status=400)

    # Get the project and data file
    try:
        data_file = DataFile.objects.get(id=file_id)
    except DataFile.DoesNotExist:
        return JsonResponse({'error': 'Data file not found'}, status=404)

    project_data_file = ProjectDataFile.objects.filter(project=project, data_file=data_file).first()

    # Verify the file belongs to the project
    if not project_data_file:
        return JsonResponse({'error': 'Data file does not belong to this project'}, status=403)

    if data_file.forward_read:
        data_file.forward_read.read_type = "F"
        data_file.forward_read.save()
    if data_file.reverse_read:
        data_file.reverse_read.read_type = "F"
        data_file.reverse_read.save()

    if ProjectDataFile.objects.filter(data_file=data_file).count() < 2 and data_file.source != "sample" and data_file.source != "reference":
        if data_file.associated_abi:
            os.remove(data_file.associated_abi.name)
        if data_file.associated_fasta:
            os.remove(data_file.associated_fasta.name)
        data_file.delete()
    project_data_file.delete()
    return JsonResponse({'success': 'Sequence removed successfully'})

def delete_nanopore_file(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_id = data.get('nanoporeSeqId')

    if not file_id:
        return JsonResponse({'error': 'file_id is required'}, status=400)

    # Get the project and data file
    try:
        nanopore_sequence = NanoporeSequence.objects.get(id=file_id)
    except NanoporeSequence.DoesNotExist:
        return JsonResponse({'error': 'Nanopore sequence not found'}, status=404)

    pns = ProjectNanoporeSequence.objects.filter(project=project, nanopore_sequence=nanopore_sequence).first()

    # Verify the file belongs to the project
    if not pns:
        return JsonResponse({'error': 'Nanopore sequence does not belong to this project'}, status=403)

    data_files = DataFile.objects.filter(source='nanopore', nanopore_seq_id=nanopore_sequence)
    ProjectDataFile.objects.filter(project=project, data_file__in=data_files).delete()
    data_files.delete()
    FastpJob.objects.filter(nanopore_sequence=nanopore_sequence, project=project).delete()
    PorechopJob.objects.filter(nanopore_sequence=nanopore_sequence, project=project).delete()
    MedakaJob.objects.filter(nanopore_sequence=nanopore_sequence, project=project).delete()

    directories = NanoporeSampleSet.objects.values_list('directory', flat=True)
    in_sample_directory = any(nanopore_sequence.file.name.startswith(dir) for dir in directories)
    if not in_sample_directory and ProjectNanoporeSequence.objects.filter(nanopore_sequence=nanopore_sequence).count() < 2:
        nanopore_sequence.delete()
    pns.delete()
    return JsonResponse({'success': 'Sequence removed successfully'})

def get_tutorial_status(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    pid = request.GET.get('pid')
    # Retrieve project info for the current authenticated user
    try:
        project = Project.objects.filter(id=pid, user=request.user)
    except ValueError:
        return JsonResponse({'error': 'Project not found'}, status=404)
    if not project:
        return JsonResponse({'error': 'Project not found'}, status=404)
    else:
        project = project.first()

    other_projects_exist = Project.objects.filter(
        user=request.user,
        project_type=project.project_type,
        sequencing_type=project.sequencing_type,
    ).exclude(id=project.id).exists()

    return JsonResponse({
        "other_projects_exist": other_projects_exist,
        "tutorial_disabled": False,
    })

def create_basecall_job(tapis, user, uploaded_ids, model, kit, output):
    url_start = settings.REACT_URL + "backend/pod5_files/"
    path_end = ".pod5"
    fileInputs = []
    for i in uploaded_ids:
        fileInputs.append({"name": str(i) + path_end, "sourceUrl": url_start + str(i) + path_end, "targetPath": "."})
    job_params = {
         "name": "basecall",
         "appId": "dnasubway-dorado",
         "appVersion":"0.0.3",
         "fileInputs": fileInputs,
         "parameterSet":{
             "envVariables":[{"key":"MODEL", "value":model},{"key": "KIT_NAME", "value": kit},{"key": "OUTPUT_NAME", "value": output}]
         }
    }
    job_response = tapis.jobs.submitJob(**job_params)
    job_uuid = job_response.get('uuid')
    job = Job.objects.create(user=user, appId='dnasubway-dorado', uuid=job_uuid, status='PENDING')
    basecall_job = BasecallingJob.objects.create(job=job, model=model, output_name=output, kit_name=kit)
    return job_uuid

def basecall_jobs(request):
    if request.method != "GET":
        return JsonResponse({'error': 'GET method required'}, status=405)

    if not request.user or not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    jobs = (
        BasecallingJob.objects
        .select_related('job')
        .filter(job__user=request.user)
    )

    data = [
        {
            'created_at': job.created_at.strftime("%Y-%m-%d"),
            'output_name': job.output_name,
            'status': job.job.status
        }
        for job in jobs
    ]

    return JsonResponse({'basecalling_jobs': data}, status=200)

def basecall(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    user = request.user

    active_job_exists = BasecallingJob.objects.filter(
        job__user=user
    ).exclude(job__status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED']).exists()

    if active_job_exists:
        return JsonResponse({
            'error': 'You already have an active base-calling job. Please wait for it to finish before starting another.'
        }, status=400)

    output = data.get('output_name', 'testname')

    duplicate_job_exists = BasecallingJob.objects.filter(
        job__user=user,
        output_name=output
    ).exists()

    if duplicate_job_exists:
        return JsonResponse({
            'error': 'You already have a base-calling job with the chosen output name. Choose a different output name.'
        }, status=400)

    files = data.get('files')
    model = data.get('model', 'fast')
    kit = data.get('kit', 'SQK-RBK114-24')
    if not files:
        return JsonResponse({'error': 'files is required'}, status=400)

    uploaded_ids = []
    active, err = ensure_instance_ready(settings.INSTANCE_NAME)
    if err:
        return JsonResponse({'error': err})

    get_service_token()
    user_token = generate_user_token(user.username)
    tapis = connect_to_tapis(user.username, user_token)
    pod_files = []

    for relative_path, base64_content in files.items():
        try:
            # Decode the base64 content
            raw_bytes = base64.b64decode(base64_content)
        except Exception as e:
            warnings.append(f"Failed to decode {relative_path}: {e}")
            continue

        # Create PodFile instance
        podfile_instance = PodFile.objects.create(user=user, name=os.path.basename(relative_path))

        # Save file using Django's storage system
        podfile_filename = f"{podfile_instance.id}.pod5"
        podfile_instance.file.save(podfile_filename, ContentFile(raw_bytes), save=True)

        uploaded_ids.append(podfile_instance.id)

    job_uuid = create_basecall_job(tapis, user, uploaded_ids, model, kit, output)
    return JsonResponse({
        'success': f'{len(uploaded_ids)} files uploaded.',
        'uploaded_ids': uploaded_ids,
    })


def usernanoporesequences(request):
    if request.method == 'GET':
        # Get user_id from query parameters
        user_id = request.GET.get('user_id')
        if not user_id:
            user_id = request.user.id if request.user and request.user else None
        if not user_id:
            return JsonResponse({'error': 'Missing user'}, status=400)

        try:
            # Fetch the user object based on the provided user_id
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)

        # Query all UserNanoporeSequence for this user and select related NanoporeSequence
        user_seqs = UserNanoporeSequence.objects.filter(user=user).select_related('nanopore_sequence')

        # Build list of dicts with id, name, and file URL for each NanoporeSequence
        data = []
        for us in user_seqs:
            seq = us.nanopore_sequence
            data.append({
                'id': seq.id,
                'name': seq.name,
            })

        return JsonResponse({'nanopore_sequences': data}, status=200)

    return JsonResponse({'error': 'Invalid request method'}, status=400)


def upload_user_nanopore_file(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    nanoporesequence_ids = data.get('seq_ids')

    if not nanoporesequence_ids:
        return JsonResponse({'error': 'seq_ids is required'}, status=400)

    # Convert seq_ids to a list if it's a string
    if isinstance(seq_ids, str):
        seq_ids = [seq_ids]

    if not isinstance(seq_ids, list):
        return JsonResponse({'error': 'seq_ids must be a list or a string'}, status=400)

    responses = []
    for seq_id in seq_ids:
        try:
            nanopore_sequence = NanoporeSequence.objects.get(id=seq_id)
            # Link NanoporeSequence to the project
            ProjectNanoporeSequence.objects.get_or_create(
                project=project,
                nanopore_sequence=nanopore_sequence
            )
            responses.append({
                'seq_id': seq_id,
                'status': 'success',
            })
        except NanoporeSequence.DoesNotExist:
            responses.append({
                'seq_id': seq_id,
                'status': 'error',
                'error': f'NanoporeSequence with id {seq_id} not found.'
            })
        except Exception as e:
            responses.append({
                'seq_id': seq_id,
                'status': 'error',
                'error': str(e)
            })

    return JsonResponse({'results': responses, 'status': 'success'}, status=200)
