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
from django.core.exceptions import SuspiciousFileOperation
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import io
import json
import os
import subprocess
import random
import re
import requests
import string
import time
import uuid
#from django.shortcuts import render
from .models import UserProfile, Ethnicity, EmailVerifyToken, PasswordResetToken, Project, DataFile, ProjectDataFile, NanoporeSampleSet, NanoporeSequence, ProjectNanoporeSequence, FastpJob, FastpResult, PorechopJob, PorechopResult, MedakaJob, MedakaResult, BlastJob, BlastResult, BlastData, MuscleJob, MuscleData, MuscleSimilarity, PhylipNJJob, PhylipNJData, PhylipMLJob, PhylipMLData, ReferenceData, SampleData, ConsensusData, ProjectBlastDone, EnhancedPermissionToken, PodFile, BasecallingJob, Job, UserNanoporeSequence, JobPodFile, DataFolder, SangerSequenceFolder, NanoporeSequenceFolder, Specimen, Author, MetabarcodingFile, MetadataFile, ProjectMetabarcodingFile, ProjectMetadataFile, DemuxJobDetail, DemuxResult, Dada2JobDetail, Dada2Result, RarefactionJobDetail, CoreMetricsJobDetail, CoreMetricsResult, GneissJobDetail, AncomJobDetail, PronameImportJobDetail, PronameImportResult, PronameFilterJobDetail, PronameFilterResult, PronameRefineJobDetail, PronameRefineResult, PronameTaxonomyJobDetail
from .utils import parse_reads, cleanSequenceName, sequence_trim, blast, muscle, phylip_ml, phylip_nj, consense, multi_seq_muscle_jobs, job_status_check, local_sequence_trim, suggested_trim, undo_sequence_trim, local_consense, local_blast, local_muscle, local_phylip_nj, local_phylip_ml, get_quality_scores, is_low_quality, is_text_file, extract_genbank_data, extract_sequences, ensure_instance_ready, get_service_token, generate_user_token, connect_to_tapis, placeholder_tapis_job, base10_to_base36, INSDC_COUNTRY_MAP, validate_fastq_gz, validate_qiime2_metadata_format, validate_qiime2_tsv, download_cyverse_file, extract_qiime2_metadata_sample_ids, get_max_rarefaction_depth, get_sampling_depth_guardrails, get_user_job_status, stop_job, validate_metabarcoding_pairs, find_best_medaka_model, is_gzip, is_fastq_text
import gzip
import shutil
from .tasks import run_fastp_task, run_porechop_task, run_medaka_task, run_basecall_task, submit_demux_job_task, submit_dada2_job_task, submit_rarefaction_job_task, submit_coremetrics_job_task, submit_gneiss_job_task, submit_ancom_job_task, submit_proname_import_job_task, submit_proname_filter_job_task, submit_proname_refine_job_task # Celery task
from Bio import SeqIO
import base64
from .models import Author, MuscleTrim
from tapipy.tapis import Tapis
from collections import defaultdict
from datetime import datetime
from .genbank_submit import GenbankRecord, GenbankSubmission

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
    institution = data.get('institution')
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

    # Check if institution is longer than 64 characters
    if len(institution) > 64:
        return JsonResponse({'error': 'Institution must be no longer than 64 characters'}, status=400)


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
        ethnicity=ethnicity,
        institution=institution
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
                    'institution': user_profile.institution,
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
                    user_profile.institution = data.get('institution', user_profile.institution)
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

def send_feedback_email(message):
    try:
        url = f"https://api.mailgun.net/v3/{getattr(settings, 'MAILGUN_DOMAIN')}/messages"
        api_key = getattr(settings, 'MAILGUN_API_KEY')
        from_email = getattr(settings, 'MAILGUN_FROM_EMAIL')

        data = {
            "from": from_email,
            "to": ["dnalcadmin@cshl.edu"],
            "cc": ["feitzin@cshl.edu", "williams@cshl.edu"],
            "subject": "[DNA Subway 2.0] Feedback",
            "text": message,
        }

        response = requests.post(url, auth=("api", api_key), data=data)

        return response.status_code == 200

    except Exception as e:
        print(f"Error sending feedback email: {e}")
        return False

def feedback(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])
    user = request.user
    data = parsed_data['data']


    if not user or not hasattr(user, 'userprofile'):
        return JsonResponse({'error': 'User profile not found'}, status=400)

    username = user.username
    email = user.email
    name = (user.first_name + " " + user.last_name).strip()
    subject = data.get('subject', 'General')

    if subject not in ["General", "Site", "Content", "Technical", "Help", "Other"]:
        subject = "General"

    comments = data.get('comments', '')

    if not comments:
        return JsonResponse(
            {"error": "Enter comments"},
            status=400
        )

    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    client_ip = ""
    if x_forwarded_for:
        client_ip = x_forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.META.get("REMOTE_ADDR")

    message = (
        f"Name: {name}\n"
        f"Username: {username}\n"
        f"Email: {email}\n"
        f"Client IP: {client_ip}\n"
        f"Subject: {subject}\n"
        f"Message:\n\n"
        f"{comments}\n"
    )

    lowered = comments.lower()
    contains_link = (
        "http:" in lowered or
        "https:" in lowered or
        "bit.ly" in lowered
    )

    if contains_link:
        return JsonResponse({
            "success": "Feedback form submitted successfully"
        })

    sent = send_feedback_email(message)

    if not sent:
        return JsonResponse(
            {"error": "Failed to send feedback email"},
            status=400
        )

    return JsonResponse({"success": "Feedback form submitted successfully"})

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
    read_type = data.get('read_type', 'single')

    # Check if project_type is valid
    if project_type not in dict(Project.PROJECT_TYPES).keys():
        return JsonResponse({'error': 'Invalid project_type'}, status=400)

    # Check if sequencing_type is valid
    if sequencing_type not in dict(Project.SEQUENCING_TYPES).keys():
        return JsonResponse({'error': 'Invalid sequencing_type'}, status=400)

    if read_type not in dict(Project.READ_TYPES).keys():
        return JsonResponse({'error': 'Invalid read_type'}, status=400)

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
                      project_type=project_type, sequencing_type=sequencing_type, barcode_type=barcode_type, read_type=read_type)
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
            'read_type': project.read_type,
            'created_date': project.created.strftime('%Y-%m-%d'),  # Format date as YYYY-MM-DD
            'public': project.public
        }
        serialized_projects.append(serialized_project)

    return JsonResponse({'success': 'Projects retrieved', 'projects': serialized_projects}, safe=False)

def project_info(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    pid = request.GET.get('pid')
    # Retrieve project info for the current authenticated user or public
    if request.user.is_superuser:
        project = Project.objects.filter(id=pid).first()
    else:
        project = Project.objects.filter(
            Q(id=pid) & (Q(user=request.user) | Q(public=True))
        ).first()
    if not project:
        return JsonResponse({'error': 'Project not found'}, status=404)
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
        consensus_data = ConsensusData.objects.filter(consensus=project_data_file.data_file).last() if project_data_file.data_file.source != "saved" else ConsensusData.objects.filter(consensus=project_data_file.data_file.source_file_id).last()
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
        .exclude(nanopore_sequence__source='proname_import') \
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
        with nanopore_sequence.file.open('rb') as compressed_file:
            with gzip.open(compressed_file, 'rt') as f:
                line1 = f.readline()
                line2 = f.readline()
        nanopore_first_lines = line1 + line2
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
        in_repository = UserNanoporeSequence.objects.filter(
          user=request.user,
          nanopore_sequence=nanopore_sequence
        ).exists()

        set_name = None
        if sample_set and sample_set.name:
            set_name = sample_set.name
        else:
            pir = getattr(nanopore_sequence, "proname_import_result", None)
            job = getattr(pir, "job", None)
            job_id = getattr(job, "id", None)

            if job_id is not None:
                set_name = f"pronameImport{job_id}"

        nanopore.append({
            'nanopore_sequence_id': nanopore_sequence.id,
            'source': nanopore_sequence.source,
            'in_sequence_repository': in_repository,
            'sample_set_name': set_name,
            'sample_set': True if sample_set else False,
            'name': nanopore_sequence.name,
            'first_lines': nanopore_first_lines,
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
                'reference_sequence': medaka_result.fasta_file_medaka_headers.url if medaka_result and medaka_result.fasta_file_medaka_headers else None,
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

    metabarcoding = {}
    demux = {}
    dada2 = {}
    rarefaction = {}
    coremetrics = {}
    ancom = {}
    proname_import = {}
    proname_filter = {}
    proname_refine = {}
    metadata = {}
    used_metadata_file_id = None
    max_rarefaction_depth = 100000
    max_sdepth = 20000
    min_sdepth = 10
    suggested_sdepth = 3000
    trim_table_found = False
    primary_found = False
    if project.project_type == "UB":
        project_metabarcoding_files = ProjectMetabarcodingFile.objects.filter(project=project).select_related('metabarcoding_file').order_by('metabarcoding_file__name')
        for project_metabarcoding_file in project_metabarcoding_files:
            metabarcoding_file = project_metabarcoding_file.metabarcoding_file
            metabarcoding[metabarcoding_file.id] = metabarcoding_file.name
        project_metadata_files = ProjectMetadataFile.objects.filter(project=project).select_related('metadata_file').order_by('metadata_file__name')
        for project_metadata_file in project_metadata_files:
            metadata_file = project_metadata_file.metadata_file
            metadata[metadata_file.id] = [metadata_file.name, metadata_file.validated]
        demux_job = (
            Job.objects.filter(
                project=project,
                appId=settings.QIIME2_DEMUX_APP_ID,
            )
            .order_by("-id")
            .first()
        )
        dada2_jobs = (
            Job.objects.filter(
                project=project,
                appId=settings.QIIME2_DADA2_APP_ID,
            )
            .order_by("-id")
        )
        rarefaction_jobs = (
            Job.objects.filter(
                project=project,
                appId=settings.QIIME2_RAREFACTION_APP_ID,
            )
            .order_by("-id")
        )
        coremetrics_jobs = (
            Job.objects.filter(
                project=project,
                appId=settings.QIIME2_COREMETRICS_APP_ID,
            )
            .order_by("-id")
        )
        ancom_jobs = (
            Job.objects.filter(
                project=project,
                appId=settings.QIIME2_ANCOM_APP_ID,
            )
            .order_by("-id")
        )
        proname_import_jobs = (
            Job.objects.filter(
                project=project,
                appId=settings.QIIME2_PRONAME_IMPORT_APP_ID,
            )
            .order_by("-id")
        )
        proname_filter_jobs = (
            Job.objects.filter(
                project=project,
                appId=settings.QIIME2_PRONAME_FILTER_APP_ID,
            )
            .order_by("-id")
        )
        proname_refine_jobs = (
            Job.objects.filter(
                project=project,
                appId=settings.QIIME2_PRONAME_REFINE_APP_ID,
            )
            .order_by("-id")
        )
        dada2 = {"running": False, "jobs": []}
        rarefaction = {"running": False, "jobs": []}
        coremetrics = {"running": False, "jobs": []}
        ancom = {"running": False, "jobs": []}
        proname_import = {"running": False, "jobs": []}
        proname_filter = {"running": False, "jobs": []}
        proname_refine = {"running": False, "jobs": []}
        if demux_job:
            running = demux_job.status not in ["FINISHED", "CANCELLED", "FAILED", "STOPPED"]

            rand_samples = None
            if hasattr(demux_job, "demux_detail"):
                rand_samples = demux_job.demux_detail.rand_samples
            summary_path = None
            if hasattr(demux_job, "demux_result") and demux_job.demux_result.demux_summary_qzv:
                summary_path = demux_job.demux_result.demux_summary_qzv.name
            demux["running"] = running
            demux["id"] = demux_job.id
            demux["status"] = demux_job.status
            if rand_samples:
                demux["randomSamples"] = rand_samples
            if summary_path:
                demux["results"] = {}
                demux["results"]["summary"] = summary_path
        if dada2_jobs.exists():
            # A DADA2 workflow is "running" if any job is not finished/cancelled/failed
            dada2["running"] = any(job.status not in ["FINISHED", "CANCELLED", "FAILED", "STOPPED"] for job in dada2_jobs)

            for job in dada2_jobs:
                job_data = {
                    "id": job.id,
                    "status": job.status,
                    "primary": job.primary,
                }

                # DADA2 parameters from Dada2JobDetail
                if hasattr(job, "dada2_detail"):
                    detail = job.dada2_detail
                    job_data.update({
                        "metadata_file_id": detail.metadata_file.id,
                        "trimLeft": detail.trimLeft,
                        "truncLen": detail.truncLen,
                        "trimLeftF": detail.trimLeftF,
                        "truncLenF": detail.truncLenF,
                        "trimLeftR": detail.trimLeftR,
                        "truncLenR": detail.truncLenR,
                    })

                # DADA2 results
                results = {}
                if hasattr(job, "dada2_result"):
                    result = job.dada2_result
                    if result.trim_table_qzv:
                        results["Trim Table"] = result.trim_table_qzv.name
                        if not trim_table_found or (job.primary and not primary_found):
                            max_rarefaction_depth = get_max_rarefaction_depth(result.trim_table_qzv.name)
                            if result.trim_table_qza:
                                min_sdepth, suggested_sdepth, max_sdepth = get_sampling_depth_guardrails(result.trim_table_qza.name)
                        trim_table_found = True
                    if result.stats_qzv:
                        results["Stats"] = result.stats_qzv.name
                    if result.rep_seqs_qzv:
                        results["Representative Sequences"] = result.rep_seqs_qzv.name
                if results:
                    job_data["results"] = results

                dada2["jobs"].append(job_data)

                if job.primary:
                    primary_found = True
            primary_found = False

        if rarefaction_jobs.exists():
            # A Rarefaction workflow is "running" if any job is not finished/cancelled/failed
            rarefaction["running"] = any(job.status not in ["FINISHED", "CANCELLED", "FAILED", "STOPPED"] for job in rarefaction_jobs)

            for job in rarefaction_jobs:
                job_data = {
                    "id": job.id,
                    "status": job.status,
                }

                # Rarefaction parameters from RarefactionJobDetail
                if hasattr(job, "rarefaction_detail"):
                    detail = job.rarefaction_detail
                    job_data.update({
                        "dada2_job_id": detail.dada2_job.id,
                        "minDepth": detail.minDepth,
                        "maxDepth": detail.maxDepth,
                    })

                # Rarefaction results
                results = {}
                if hasattr(job, "rarefaction_result"):
                    result = job.rarefaction_result
                    if result.alpha_rarefaction_qzv:
                        results["Alpha Rarefaction Plot"] = result.alpha_rarefaction_qzv.name
                if results:
                    job_data["results"] = results

                rarefaction["jobs"].append(job_data)

        if coremetrics_jobs.exists():
            # A Coremetrics workflow is "running" if any job is not finished/cancelled/failed
            coremetrics["running"] = any(job.status not in ["FINISHED", "CANCELLED", "FAILED", "STOPPED"] for job in coremetrics_jobs)

            for job in coremetrics_jobs:
                job_data = {
                    "id": job.id,
                    "status": job.status,
                    "primary": job.primary,
                }

                # Coremetrics parameters from CoremetricsJobDetail
                if hasattr(job, "coremetrics_detail"):
                    detail = job.coremetrics_detail
                    job_data.update({
                        "dada2_job_id": detail.dada2_job.id,
                        "sdepth": detail.sdepth,
                        "classifier": detail.classifier,
                    })

                # Coremetrics results
                results = {}
                if hasattr(job, "coremetrics_result"):
                    result = job.coremetrics_result
                    coremetrics_field_map = {
                        "evenness_correlation": "Pielou's Evenness Correlation",
                        "evenness_group_significance": "Pielou's Evenness Group Significance",
                        "evenness_raincloud": "Pielou's Evenness Raincloud",
                        "faith_pd_correlation": "Faith's Phylogenetic Diversity Correlation",
                        "faith_pd_group_significance": "Faith's Phylogenetic Diversity Group Significance",
                        "faith_pd_raincloud": "Faith's Phylogenetic Diversity Raincloud",
                        "observed_features_correlation": "Observed Features Correlation",
                        "observed_features_group_significance": "Observed Features Group Significance",
                        "observed_features_raincloud": "Observed Features Raincloud",
                        "shannon_correlation": "Shannon's Diversity Index Correlation",
                        "shannon_group_significance": "Shannon's Diversity Index Group Significance",
                        "shannon_raincloud": "Shannon's Diversity Index Raincloud",
                        "bray_curtis_bioenv": "Bray Curtis Distance Bioenv",
                        "bray_curtis_emperor": "Bray Curtis Distance Emperor",
                        "jaccard_emperor": "Jaccard Distance Emperor",
                        "unweighted_unifrac_bioenv": "Unweighted UniFrac Distance Bioenv",
                        "unweighted_unifrac_emperor": "Unweighted UniFrac Distance Emperor",
                        "weighted_unifrac_emperor": "Weighted UniFrac Distance Emperor",
                        "taxa_bar_plots": "Taxonomic Diversity Bar Plots",
                        "taxonomy_qzv": "Taxonomic Diversity Taxonomy",
                    }
                    for field_name, label in coremetrics_field_map.items():
                        file_field = getattr(result, field_name)
                        if file_field:
                            results[label] = file_field.name
                if results:
                    job_data["results"] = results

                coremetrics["jobs"].append(job_data)
            finished_coremetrics_jobs = coremetrics_jobs.filter(status="FINISHED")
            primary_coremetrics_job = None
            if finished_coremetrics_jobs.exists():
                primary_coremetrics_jobs = finished_coremetrics_jobs.filter(primary=True)
                primary_coremetrics_job = primary_coremetrics_jobs.first() if primary_coremetrics_jobs.exists() else finished_coremetrics_jobs.first()
            primary_coremetrics_detail = (
                getattr(primary_coremetrics_job, "coremetrics_detail", None)
                if primary_coremetrics_job
                else None
            )
            used_dada2_job = getattr(primary_coremetrics_detail, "dada2_job", None) if primary_coremetrics_detail else None
            used_dada2_job_detail = (
                Dada2JobDetail.objects.filter(job=used_dada2_job).first()
                if used_dada2_job
                else None
            )
            if not used_dada2_job_detail:
                used_dada2_job_detail = (
                    PronameRefineJobDetail.objects.filter(job=used_dada2_job).first()
                    if used_dada2_job
                    else None
                )

            used_metadata_file_id = (
                getattr(used_dada2_job_detail.metadata_file, "id", None)
                if used_dada2_job_detail and used_dada2_job_detail.metadata_file
                else None
            )

        if ancom_jobs.exists():
            # An ancom workflow is "running" if any job is not finished/cancelled/failed
            ancom["running"] = any(job.status not in ["FINISHED", "CANCELLED", "FAILED", "STOPPED"] for job in ancom_jobs)

            for job in ancom_jobs:
                job_data = {
                    "id": job.id,
                    "status": job.status,
                }

                # Ancom parameters from AncomJobDetail
                if hasattr(job, "ancom_detail"):
                    detail = job.ancom_detail
                    job_data.update({
                        "coremetrics_job_id": detail.coremetrics_job.id,
                        "category": detail.category,
                        "formula": detail.formula,
                        "taxalevel": detail.taxalevel,
                    })

                # Ancom results
                results = {}
                if hasattr(job, "ancom_result"):
                    result = job.ancom_result
                    if result.heatmap:
                        results["Heatmap"] = result.heatmap.name
                    if result.abundance_barplot:
                        results["Barplot"] = result.abundance_barplot.name
                    if result.ancom:
                        results["Ancom"] = result.ancom.name
                    if result.differentials:
                        results["Differentials"] = result.differentials.name
                if results:
                    job_data["results"] = results

                ancom["jobs"].append(job_data)

        if proname_import_jobs.exists():
            # A proname import workflow is "running" if any job is not finished/cancelled/failed
            proname_import["running"] = any(job.status not in ["FINISHED", "CANCELLED", "FAILED", "STOPPED"] for job in proname_import_jobs)

            for job in proname_import_jobs:
                job_data = {
                    "id": job.id,
                    "status": job.status,
                    "primary": job.primary,
                }

                # Proname Import parameters from PronameImportJobDetail
                if hasattr(job, "proname_import_detail"):
                    detail = job.proname_import_detail
                    job_data.update({
                        "forward_primer": detail.forward_primer,
                        "reverse_primer": detail.reverse_primer,
                        "kit": detail.kit,
                        "has_duplex": detail.has_duplex,
                        "trim_adapters": detail.trim_adapters,
                        "trim_primers": detail.trim_primers,
                    })

                # Proname Import results
                results = {}
                if hasattr(job, "proname_import_result"):
                    result = job.proname_import_result
                    if result.duplex_plot:
                        results["Duplex Plot"] = result.duplex_plot.name
                    if result.simplex_plot:
                        results["Simplex Plot"] = result.simplex_plot.name
                    if result.dual_plot:
                        results["Simplex/Duplex Plot"] = result.dual_plot.name
                    if result.simplex_distribution:
                        results["Simplex Distribution"] = result.simplex_distribution.name
                    if result.duplex_distribution:
                        results["Duplex Distribution"] = result.duplex_distribution.name
                    if result.dual_distribution:
                        results["Simplex/Duplex Distribution"] = result.dual_distribution.name
                if results:
                    job_data["results"] = results

                proname_import["jobs"].append(job_data)
        if proname_filter_jobs.exists():
            # A proname filter workflow is "running" if any job is not finished/cancelled/failed
            proname_filter["running"] = any(job.status not in ["FINISHED", "CANCELLED", "FAILED", "STOPPED"] for job in proname_filter_jobs)

            for job in proname_filter_jobs:
                job_data = {
                    "id": job.id,
                    "status": job.status,
                    "primary": job.primary,
                }

                # Proname Filter parameters from PronameFilterJobDetail
                if hasattr(job, "proname_filter_detail"):
                    detail = job.proname_filter_detail
                    job_data.update({
                        "data_type": detail.data_type,
                        "filt_min_length": detail.filt_min_length,
                        "filt_max_length": detail.filt_max_length,
                        "filt_min_qual": detail.filt_min_qual,
                    })

                # Proname Filter results
                results = {}
                if hasattr(job, "proname_filter_result"):
                    result = job.proname_filter_result
                    if result.duplex_plot:
                        results["Duplex Plot"] = result.duplex_plot.name
                    if result.simplex_plot:
                        results["Simplex Plot"] = result.simplex_plot.name
                    if result.dual_plot:
                        results["Simplex/Duplex Plot"] = result.dual_plot.name
                    if result.simplex_distribution:
                        results["Simplex Distribution"] = result.simplex_distribution.name
                    if result.duplex_distribution:
                        results["Duplex Distribution"] = result.duplex_distribution.name
                    if result.dual_distribution:
                        results["Simplex/Duplex Distribution"] = result.dual_distribution.name
                if results:
                    job_data["results"] = results

                proname_filter["jobs"].append(job_data)

        if proname_refine_jobs.exists():
            # A proname refine workflow is "running" if any job is not finished/cancelled/failed
            proname_refine["running"] = any(job.status not in ["FINISHED", "CANCELLED", "FAILED", "STOPPED"] for job in proname_refine_jobs)

            for job in proname_refine_jobs:
                job_data = {
                    "id": job.id,
                    "status": job.status,
                    "primary": job.primary,
                }

                # Proname Refine parameters from PronameRefineJobDetail
                if hasattr(job, "proname_refine_detail"):
                    detail = job.proname_refine_detail
                    job_data.update({
                        "chimera_db": detail.chimera_db,
                        "cluster_id": detail.cluster_id,
                        "min_reads_per_cluster": detail.min_reads_per_cluster,
                        "clustering_method": detail.clustering_method,
                        "medaka_model": detail.medaka_model,
                        "metadata_file": detail.metadata_file.id,
                    })

                # Proname Refine results
                results = {}
                if hasattr(job, "proname_refine_result"):
                    result = job.proname_refine_result
                    if result.rep_seqs_qzv:
                        results["Representative Sequences"] = result.rep_seqs_qzv.name
                    if result.trim_table_qzv:
                        results["Trim Table"] = result.trim_table_qzv.name
                        if not trim_table_found or (job.primary and not primary_found):
                            max_rarefaction_depth = get_max_rarefaction_depth(result.trim_table_qzv.name)
                            if result.trim_table_qza:
                                min_sdepth, suggested_sdepth, max_sdepth = get_sampling_depth_guardrails(result.trim_table_qza.name)
                        trim_table_found = True
                    if result.rooted_tree_qza:
                        results["Rooted Tree (Archive)"] = result.rooted_tree_qza.name
                if results:
                    job_data["results"] = results

                proname_refine["jobs"].append(job_data)
                if job.primary:
                    primary_found = True
            primary_found = False

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
        'read_type': project.read_type,
        'project_type': project.project_type,
        'barcode_type': project.barcode_type,
        'created_date': project.created.strftime('%Y-%m-%d'),  # Format date as YYYY-MM-DD
        'username': project.user.username,
        'uid': project.user.id,
        'sequences': serialized_sequences,
        'nanopore_sequences': nanopore,
        'metadata': metadata,
        'metabarcoding': metabarcoding,
        'max_rarefaction_depth': max_rarefaction_depth,
        'max_sdepth': max_sdepth,
        'min_sdepth': min_sdepth,
        'suggested_sdepth': suggested_sdepth,
        'demux': demux,
        'dada2': dada2,
        'rarefaction': rarefaction,
        'coremetrics': coremetrics,
        'ancom': ancom,
        'proname_import': proname_import,
        'proname_filter': proname_filter,
        'proname_refine': proname_refine,
        'used_coremetrics_metadata': used_metadata_file_id,
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
            'username': username,
            'title': project.title,
            'description': project.description,
            'sequencing_type': project.sequencing_type,
            'read_type': project.read_type,
            'project_type': project.project_type,
            'created_date': project.created.strftime('%Y-%m-%d'),  # Format date as YYYY-MM-DD
            'public': True,
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
        file_url = file_url.replace("http://gfx.dnalc.org", "https://dnalc.cshl.edu")

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
    if "status" in consense_result and consense_result["status"] == "error":
        return JsonResponse(consense_result, status=400)
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

    metadata_files = MetadataFile.objects.filter(
        project_links__project=project
    )

    metadata_files.update(validated=False)
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

        fasta_path = reference_set.file.path if hasattr(reference_set.file, "path") else default_storage.path(reference_set.file.name)
        seq_names = []
        try:
            if os.path.exists(fasta_path):
                seq_names = [record.id for record in SeqIO.parse(fasta_path, "fasta")]
        except Exception as e:
            seq_names = []

        grouped_data[category].append({
            'id': reference_set.id,
            'name': reference_set.name,
            'files': seq_names
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

        files_list = []
        file_path = sample_set.file.path if hasattr(sample_set.file, "path") else default_storage.path(sample_set.file.name)

        if sample_set.file_type == "fasta":
            try:
                if os.path.exists(file_path):
                    files_list = [record.id for record in SeqIO.parse(file_path, "fasta")]
            except Exception as e:
                files_list = []
        elif sample_set.file_type == "abi":
            if os.path.isdir(file_path):
                files_list = [
                    os.path.splitext(f)[0]
                    for f in os.listdir(file_path)
                    if f.lower().endswith(".ab1")
                ]
        grouped_data[category].append({
            'id': sample_set.id,
            'name': sample_set.name,
            'files': files_list
        })

    # Return the data as JSON
    return JsonResponse(grouped_data)

def validate_optional_int(value, field_name, min_val=None, max_val=None):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return JsonResponse(
            {'error': f'{field_name} must be an integer'},
            status=400
        )

    if min_val is not None and value < min_val:
        return JsonResponse(
            {'error': f'{field_name} must be >= {min_val}'},
            status=400
        )

    if max_val is not None and value > max_val:
        return JsonResponse(
            {'error': f'{field_name} must be <= {max_val}'},
            status=400
        )

    return None

def is_valid_adapter_input(adapter):
    """
    Returns True if input is a 15-40bp DNA sequence OR a FASTA format string.
    Returns False otherwise.
    """
    if not adapter or not isinstance(adapter, str):
        return False

    adapter = adapter.strip()

    # Criteria 1: 15-40bp ACTG sequence
    is_single_seq = bool(re.fullmatch(r'[ACTG]{15,40}', adapter, re.IGNORECASE))

    # Criteria 2: FASTA format
    # Basic check: starts with '>' and has at least one newline/sequence data
    is_fasta = adapter.startswith(">") and len(adapter) > 1

    return is_single_seq or is_fasta

def submit_fastp_job(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']

    nanopore_sequence_ids = data.get('nanopore_sequence_id')
    reads_to_process = data.get('reads_to_process')
    qualified_quality_phred = data.get('qualified_quality_phred')
    average_qual = data.get('average_qual')
    adapter = data.get('adapter')
    length_required = data.get('length_required')
    length_limit = data.get('length_limit')
    report_title = data.get('report_title')

    if not nanopore_sequence_ids:
        return JsonResponse({'error': 'nanopore_sequence_id is required'}, status=400)

    if adapter and not is_valid_adapter_input(adapter):
        return JsonResponse({'error': 'Not a valid adapter'}, status=400)

    if reads_to_process is not None:
        error = validate_optional_int(
            reads_to_process, 'reads_to_process', 0, 10000
        )
        if error:
            return error

    if qualified_quality_phred is not None:
        error = validate_optional_int(
            qualified_quality_phred, 'qualified_quality_phred', 0, 40
        )
        if error:
            return error

    if average_qual is not None:
        error = validate_optional_int(
            average_qual, 'average_qual', 0, 40
        )
        if error:
            return error

    if length_required is not None:
        error = validate_optional_int(
            length_required, 'length_required', 1, 500
        )
        if error:
            return error

    if length_limit is not None:
        error = validate_optional_int(
            length_limit, 'length_limit', 0, 5000
        )
        if error:
            return error

        if length_limit > 0 and length_required is not None and length_limit < length_required:
            return JsonResponse(
                {
                    'error': (
                        'length_limit must be greater than or equal to '
                        'length_required when length_limit > 0'
                    )
                },
                status=400
            )

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
        run_fastp_task.delay(fastp_job.id, reads_to_process, qualified_quality_phred, length_required, length_limit, report_title, average_qual, adapter)

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
    reference_choice = data.get('reference_choice', '')
    reference_file = data.get('reference_file')
    if reference_choice and reference_choice not in settings.MEDAKA_REFERENCES and reference_choice != "custom":
        return JsonResponse({'error': 'Bad value for medaka reference'}, status=400)

    if not nanopore_sequence_ids:
        return JsonResponse({'error': 'nanopore_sequence_id is required'}, status=400)

    reference_path = None
    if reference_choice in settings.MEDAKA_REFERENCES:
        reference_path = "medaka_reference_files/" + settings.MEDAKA_REFERENCES[reference_choice]

    if reference_choice == "custom":
        sequences = extract_sequences(reference_file)
        if len(sequences) < 1:
            return JsonResponse({'error': 'Invalid FASTA format'}, status=400)
        if len(sequences) > 1:
            return JsonResponse({'error': 'Multi-sequence FASTA format not permitted'}, status=400)
        for seq_record in sequences:
            name = seq_record.id
            reads = str(seq_record.seq)
            if len(reads) > 10000:
                return JsonResponse({'error': f"Sequence {name} is too long."}, status=400)
            data_file = DataFile.objects.create(
                user=request.user,
                name=name,
                read_type="F",
                reads=reads,
                source="upload",
            )
            header = re.sub(r'\s+', '_', name)
            fasta_content = ContentFile(f">{header}\n{reads}\n")
            fasta_file_name = f"{data_file.id}.fasta"
            reference_path = f"fasta_files/{fasta_file_name}"
            fasta_file_path = default_storage.save(reference_path, fasta_content)
            fasta_full_path = default_storage.path(fasta_file_path)
            subprocess.run(["samtools", "faidx", fasta_full_path], check=True)
            fai_path = fasta_full_path + ".fai"
            with open(fai_path, "rb") as f:
                fai_content = ContentFile(f.read())
            fai_storage_path = f"{reference_path}.fai"
            saved_fai_path = default_storage.save(fai_storage_path, fai_content)
            data_file.associated_fasta.name = fasta_file_path
            data_file.save()
            ProjectDataFile.objects.create(
                project=project,
                data_file=data_file
            )

    # Normalize to a list if a single ID is provided
    if isinstance(nanopore_sequence_ids, int):
        nanopore_sequence_ids = [nanopore_sequence_ids]

    # Prepare lists to track job statuses
    jobs_created = []
    already_existing_jobs = []
    missing_input_files = []
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

        porechop_result = PorechopResult.objects.filter(project_nanopore_sequence=project_nanopore_sequence).first()
        fastp_result = FastpResult.objects.filter(project_nanopore_sequence=project_nanopore_sequence).first()

        input_file_path = None
        if porechop_result and porechop_result.chopped_file:
            input_file_path = porechop_result.chopped_file.path
        elif fastp_result and fastp_result.filtered_file:
            input_file_path = fastp_result.filtered_file.path
        elif project_nanopore_sequence.nanopore_sequence and project_nanopore_sequence.nanopore_sequence.file:
            input_file_path = project_nanopore_sequence.nanopore_sequence.file.path

        if not input_file_path:
            missing_input_files.append(nanopore_sequence_id)
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
        run_medaka_task.delay(project_nanopore_sequence.id, reference_path, input_file_path)

        # Track the created job
        jobs_created.append(nanopore_sequence_id)

    # Return a response indicating the result
    response = {
        'jobs_created': jobs_created,
        'already_existing_jobs': already_existing_jobs,
        'missing_porechop_results': missing_input_files,
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

def upload_nanopore_directory(request):
    """
    Handles directory uploads.
    Expects 'file_data' as a list of files and 'paths' as a JSON array of relative paths.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    pid = request.POST.get('pid')
    project = get_project(pid)
    if not project or project.user != request.user:
        return JsonResponse({'error': 'Unauthorized or Project not found'}, status=403)

    uploaded_files = request.FILES.getlist('file_data')
    paths = json.loads(request.POST.get('paths', '[]'))
    if not uploaded_files:
        return JsonResponse({'error': 'No files provided'}, status=400)
    folders = {}
    for i, file_obj in enumerate(uploaded_files):
        filename = file_obj.name.lower()

        if not (filename.endswith('.fastq.gz') or filename.endswith('.fq.gz') or filename.endswith('.fq') or filename.endswith('.fastq')):
            continue

        path_parts = paths[i].split(os.path.sep)

        if len(path_parts) > 2:
            folder_name = path_parts[1]
        elif len(path_parts) > 1:
            folder_name = path_parts[0]
        else:
            folder_name = "unclassified"

        if folder_name not in folders:
            folders[folder_name] = []
        folders[folder_name].append(file_obj)

    warnings = []
    ids_added = []

    for folder_name, files in folders.items():
        if ProjectNanoporeSequence.objects.filter(project=project, nanopore_sequence__name=folder_name).exists():
            warnings.append(f"Sequence '{folder_name}' already exists.")
            continue
        valid_files_to_process = []

        for f in files:
            is_gz = is_gzip(f)
            if is_gz or is_fastq_text(f):
                valid_files_to_process.append((f, is_gz))
            else:
                warnings.append(f"File {f.name} skipped: Unsupported format.")

        if not valid_files_to_process:
            continue

        nanopore_sequence = NanoporeSequence.objects.create(name=folder_name)
        storage_path = f"fastq_files/{nanopore_sequence.id}.fastq.gz"
        full_path = os.path.join(settings.BASE_DIR, storage_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, 'wb') as destination:
            for f, is_gz in valid_files_to_process:
                if is_gz:
                    for chunk in f.chunks():
                        destination.write(chunk)
                else:
                    with gzip.GzipFile(fileobj=destination, mode='wb', compresslevel=6) as gz_wrapper:
                        for chunk in f.chunks():
                            gz_wrapper.write(chunk)

        nanopore_sequence.file.name = storage_path
        nanopore_sequence.save()
        ProjectNanoporeSequence.objects.create(
            project=project,
            nanopore_sequence=nanopore_sequence
        )
        ids_added.append(nanopore_sequence.id)

    MetadataFile.objects.filter(project_links__project=project).update(validated=False)
    if len(ids_added) == 0:
        return JsonResponse({'error': 'No valid files to add'}, status=400)

    return JsonResponse({
        'success': f'Processed {len(ids_added)} nanopore sequences.',
        'ids': ids_added,
        'warnings': warnings
    })

def upload_nanopore_files(request):
    """
    Handles specific folder upload where user provides a 'folder' name.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    pid = request.POST.get('pid')
    folder_name = request.POST.get('folder')
    uploaded_files = request.FILES.getlist('file_data')

    project = get_project(pid)
    if not project or project.user != request.user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    if ProjectNanoporeSequence.objects.filter(project=project, nanopore_sequence__name=folder_name).exists():
        return JsonResponse({'error': f"Sequence '{folder_name}' exists."}, status=400)

    valid_files_to_process = []
    for f in uploaded_files:
        filename = f.name.lower()
        is_gz = is_gzip(f)

        if is_gz and (filename.endswith('.fastq.gz') or filename.endswith('.fq.gz')):
            valid_files_to_process.append((f, True))
        elif not is_gz and filename.endswith(('.fastq', '.fq')) and is_fastq_text(f):
            valid_files_to_process.append((f, False))

    if not valid_files_to_process:
        return JsonResponse({'error': 'No valid fastq.gz files detected.'}, status=400)

    nanopore_sequence = NanoporeSequence.objects.create(name=folder_name)
    storage_path = f"fastq_files/{nanopore_sequence.id}.fastq.gz"
    full_path = os.path.join(settings.BASE_DIR, storage_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    with open(full_path, 'wb') as destination:
        for f, is_gzipped in valid_files_to_process:
            if is_gzipped:
                for chunk in f.chunks():
                    destination.write(chunk)
            else:
                with gzip.GzipFile(fileobj=destination, mode='wb', compresslevel=6) as gz_wrapper:
                    for chunk in f.chunks():
                        gz_wrapper.write(chunk)

    nanopore_sequence.file.name = storage_path
    nanopore_sequence.save()
    ProjectNanoporeSequence.objects.create(project=project, nanopore_sequence=nanopore_sequence)
    MetadataFile.objects.filter(project_links__project=project).update(validated=False)

    return JsonResponse({'success': 'Files uploaded.'})

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

    metadata_files = MetadataFile.objects.filter(
        project_links__project=project
    )

    metadata_files.update(validated=False)
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
            file_url = file_url.replace("http://gfx.dnalc.org", "https://dnalc.cshl.edu")
            file_id = file_info.get('id')
            quality_map[file_id] = is_low_quality(get_quality_scores(file_url))
    except ValueError:
        return JsonResponse({'error': 'Invalid response from external API.'}, status=500)

    return JsonResponse({'success': 'Quality data checked.', 'quality_map': quality_map}, status=200)

def upload_sanger_files(request):
    PROTOCOL = request.scheme + "://"
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    files = data.get('files')

    warnings = []
    processed_count = 0

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
                if not sequences:
                    warnings.append(f"File {relative_path} could not be read as FASTA file.")
                for seq in sequences:
                    sequence_str = str(seq.seq)
                    name = seq.id
                    if len(sequence_str) > 10000:
                        warnings.append(f"Sequence {name} is too long, no sequence may be uploaded if it is more than 10kb.")
                        continue
                    if ProjectDataFile.objects.filter(project=project, data_file__name=name).exists():
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
                    processed_count += 1
            else:  # Handle as binary
                name = cleanSequenceName(relative_path)
                if ProjectDataFile.objects.filter(project=project, data_file__name=name).exists():
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
                if message:
                    warnings.append(f"Failed to process file as sequence file: {relative_path}: {message}")
                    data_file.delete()
                    continue
                if len(sequence) > 10000:
                    warnings.append(f"Sequence {name} is too long, no sequence may be uploaded if it is more than 10kb.")
                    data_file.delete()
                    continue
                name = record.name
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
                processed_count += 1
        except Exception as e:
            warnings.append(f"Failed to process file {relative_path}: {e}")

    if processed_count == 0:
        return JsonResponse({'error': "No valid files were processed.", 'warnings': warnings}, status=400)

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
    except requests.exceptions.RequestException as e:
        return JsonResponse({"error": f"API request failed: {str(e)}"}, status=500)

    try:
        data = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return JsonResponse({'error': f'Failed to parse response as JSON: {str(e)}'}, status=500)

    if isinstance(data, dict):  # If it's a single JSON object, wrap it in a list
        data = [data]
    elif not isinstance(data, list):
        return JsonResponse({'error': 'Unexpected response format from BOLD API.'}, status=400)

    warnings = []
    sequences_found = False
    for item in data:
        if "processid" in item and "identification" in item and "nuc" in item:
            process_id = item["processid"]
            identification = item["identification"].split()[0]  # Take part before first whitespace
            reads = item["nuc"]
            if len(reads) > 10000:
                warnings.append(f"Sequence {name} is too long, no sequence may be uploaded if it is more than 10kb.")
                continue

            sequences_found = True
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

    return JsonResponse({"error": "Could not find sequences in BOLD or sequences too long"}, status=400)

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

    sequences_added = False
    warnings = []
    for seq in sequences:
        name = seq["name"]
        reads = seq["reads"]
        if len(reads) > 10000:
            warnings.append(f"Sequence {name} is too long, no sequence may be uploaded if it is more than 10kb.")
            continue
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
        sequences_added = True

    if not sequences_added:
        return JsonResponse({'error': "No valid files were processed.", 'warnings': warnings}, status=400)

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
        if len(reads) > 10000:
            warnings.append(f"Sequence {name} is too long, no sequence may be uploaded if it is more than 10kb.")
            continue

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

    if not sequences_added:
        return JsonResponse({'error': "No valid files were processed.", 'warnings': warnings}, status=400)

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
            'first_name': token.user.first_name,
            'last_name': token.user.last_name,
            'institution': token.user.userprofile.institution if hasattr(token.user, 'userprofile') else None,
            'token': token.token,
            'status': token.status,
            'reason': token.reason
        }
        for token in pending_approved_tokens
    ]

    return JsonResponse({'requests': data}, status=200)

def list_latest_app_jobs(request):
    if request.method != "GET":
        return JsonResponse({'error': 'GET method required'}, status=405)

    if not request.user or not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    if not request.user.is_superuser:
        return JsonResponse({'error': 'Superuser access required'}, status=403)

    qs = Job.objects.exclude(status="STARTING").filter(appId__startswith="ub_")

    username = request.GET.get("username")
    project_id = request.GET.get("project_id")
    task = request.GET.get("task")
    status = request.GET.get("status")

    if username:
        qs = qs.filter(user__username=username)

    if project_id:
        qs = qs.filter(project_id=project_id)

    if task:
        qs = qs.filter(appId=task)

    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by("-id")[:100]

    data = [
        {
            'email': job.user.email,
            'username': job.user.username,
            'first_name': job.user.first_name,
            'last_name': job.user.last_name,
            'project_id': job.project.id,
            'project_name': job.project.title,
            'jid': job.id,
            'status': job.status,
            'task_name': job.appId,
        }
        for job in qs
    ]

    return JsonResponse({'jobs': data}, status=200)


# Helper function
def toggle_datafile_boolean_field(request, field_name):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    datafile_id = data.get("datafile_id")
    nanoporesequence_id = data.get("nanoporesequence_id")

    if datafile_id:
        try:
            datafile = DataFile.objects.get(id=datafile_id, user=request.user)
        except DataFile.DoesNotExist:
            return JsonResponse({'error': 'DataFile not found'}, status=404)

        if not hasattr(datafile, field_name):
            return JsonResponse({'error': f'Invalid field: {field_name}'}, status=400)

        current_value = getattr(datafile, field_name)
        if not isinstance(current_value, bool):
            return JsonResponse({'error': f'Field {field_name} is not boolean'}, status=400)

        setattr(datafile, field_name, not current_value)
        datafile.save()

        return JsonResponse({'success': True, field_name: getattr(datafile, field_name)})

    elif nanoporesequence_id:
        try:
            nanopore_sequence = NanoporeSequence.objects.get(id=nanoporesequence_id)
        except NanoporeSequence.DoesNotExist:
            return JsonResponse({'error': 'NanoporeSequence not found'}, status=404)

        try:
            user_nanopore = UserNanoporeSequence.objects.get(
                user=request.user,
                nanopore_sequence=nanopore_sequence
            )
        except UserNanoporeSequence.DoesNotExist:
            return JsonResponse({'error': 'UserNanoporeSequence not found'}, status=404)

        if field_name != "is_public":
            return JsonResponse({'error': 'Invalid field for NanoporeSequence'}, status=400)

        user_nanopore.is_public = not user_nanopore.is_public
        user_nanopore.save()

        return JsonResponse({'success': True, field_name: user_nanopore.is_public})

    return JsonResponse(
        {'error': 'Either datafile_id or nanoporesequence_id is required'},
        status=400
    )

def toggle_visibility(request):
    return toggle_datafile_boolean_field(request, "is_public")

def toggle_sequence_repository(request):
    return toggle_datafile_boolean_field(request, "in_sequence_repository")

def toggle_nanopore_file_sequence_repository(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    user = request.user
    nanoporesequence_id = data.get('nanopore_id')

    if not nanoporesequence_id:
        return JsonResponse({'error': 'nanopore_id is required'}, status=400)

    try:
        nanopore_sequence = NanoporeSequence.objects.get(id=nanoporesequence_id)
    except NanoporeSequence.DoesNotExist:
        return JsonResponse({'error': 'NanoporeSequence not found'}, status=404)

    try:
        obj = UserNanoporeSequence.objects.get(
            user=user,
            nanopore_sequence=nanopore_sequence
        )
        obj.delete()
        action = 'removed'
    except UserNanoporeSequence.DoesNotExist:
        exists_same_name = UserNanoporeSequence.objects.filter(
            user=user,
            nanopore_sequence__name=nanopore_sequence.name
        ).exists()

        if exists_same_name:
            return JsonResponse({
                'status': 'error',
                'error': 'You already have a sequence with this name in your repository.'
            }, status=400)

        UserNanoporeSequence.objects.get_or_create(
            user=user,
            nanopore_sequence=nanopore_sequence
        )
        action = 'added'

    return JsonResponse({'status': 'success', 'action': action, 'nanopore_id': nanoporesequence_id})

def validate_consensus_export(data_file):
    if data_file.read_type != 'C':
        return "You can only export a consensus sequence"

    if data_file.source != 'consensus':
        return "You can only export a consensus sequence"

    if not (data_file.forward_read and data_file.reverse_read):
        return "Could not identify the forward and reverse reads"

    for read in [data_file.forward_read, data_file.reverse_read]:
        if not read.associated_abi:
            return "Both reads must have trace files"

        scores = get_quality_scores(read.associated_abi.name)
        if not scores or is_low_quality(scores):
            return "Both reads must not be low quality"

        if not (read.left_trim or read.right_trim):
            return "Both reads must be trimmed"

        if read.source in ("sample", "reference"):
            return "Neither read may be from sample or reference data"

    if not (data_file.left_trim or data_file.right_trim):
        return "Consensus must be trimmed"

    return None

def safe_annotate(submission, seq, primer, organism, trans_table):
    try:
        return bool(submission.annotate_barcode(seq, primer, organism, trans_table))
    except RuntimeError:
        return False

# Helper functions

def specimen_info(specimen):
    if not specimen:
        return {}

    return {
        'codon': specimen.codon,
        'institution_storing': specimen.institution_storing,
        'identifier_name': specimen.identifier_name,
        'identifier_email': specimen.identifier_email,
        'genus': specimen.genus,
        'species': specimen.species,
        'date_collected': specimen.date_collected.strftime('%Y-%m-%d') if specimen.date_collected else None,
        'country': specimen.country,
        'state_province': specimen.state_province,
        'city': specimen.city,
        'habitat': specimen.habitat,
        'exact_site': specimen.exact_site,
        'isolation_source': specimen.isolation_source,
        'sample_collected_from_host': specimen.sample_collected_from_host,
        'host_organism_name': specimen.host_organism_name,
        'latitude': specimen.latitude,
        'longitude': specimen.longitude,
        'altitude': specimen.altitude,
        'notes': specimen.notes,
        'sex': specimen.sex,
        'reproduction': specimen.reproduction,
        'life_stage': specimen.life_stage,
        'primer_used': specimen.primer_used,
    }

def datafile_info(datafile, user_elevated_access, submission):
    specimen = getattr(datafile, 'specimen', None)
    is_valid_export = not validate_consensus_export(datafile)

    return {
        'datafile_id': datafile.id,
        'name': datafile.name,
        'username': datafile.user.username,
        'created': datafile.created,
        'updated': datafile.updated,
        'is_public': datafile.is_public,
        'specimen_id': (
            f"DNAS2-{specimen.id:X}-{base10_to_base36(datafile.id)}"
            if datafile.exported and specimen
            else None
        ),
        'can_export': user_elevated_access and is_valid_export and not datafile.exported,
        'rbcL_primer_valid': (
            user_elevated_access
            and is_valid_export
            and safe_annotate(submission, datafile.reads, "RBCL", "sample", 1)
        ),
        'invertebrate_primer_valid': (
            user_elevated_access
            and is_valid_export
            and safe_annotate(submission, datafile.reads, "COI", "sample", 5)
        ),
        'vertebrate_primer_valid': (
            user_elevated_access
            and is_valid_export
            and safe_annotate(submission, datafile.reads, "COI", "sample", 2)
        ),
        'echinoderm_primer_valid': (
            user_elevated_access
            and is_valid_export
            and safe_annotate(submission, datafile.reads, "COI", "sample", 9)
        ),
        'source': datafile.source,
        'associated_abi': datafile.associated_abi.url if datafile.associated_abi else None,
        'authors': [
            {
                'first_name': author.first_name,
                'last_name': author.last_name,
                'affiliation': author.affiliation
            }
            for author in datafile.authors.all()
        ],
        'specimen': specimen_info(specimen),
    }

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

        user_elevated_access = user and hasattr(user, 'userprofile') and user.userprofile.elevated_access
        submission = GenbankSubmission() if user_elevated_access else None

        # Get all DataFiles for the given user filtered by the provided filter_dict
        filtered_datafiles = DataFile.objects.filter(user=user, **filter_dict).order_by('name')
        data = [
            datafile_info(datafile, user_elevated_access, submission)
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
    is_user_sequence = UserNanoporeSequence.objects.filter(nanopore_sequence=nanopore_sequence).exists()
    linked_to_other_projects = ProjectNanoporeSequence.objects.filter(nanopore_sequence=nanopore_sequence).count() > 1
    if not in_sample_directory and not linked_to_other_projects and not is_user_sequence:
        nanopore_sequence.delete()
    pns.delete()
    metadata_files = MetadataFile.objects.filter(
        project_links__project=project
    )

    metadata_files.update(validated=False)
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

    # Get all BasecallingJobs for the user
    jobs = (
        BasecallingJob.objects
        .select_related('job')
        .filter(job__user=request.user)
        .order_by('-created_at')
    )

    # Get all output_names
    output_names = [job.output_name for job in jobs]

    # Prefetch folders for UserNanoporeSequence linked to these output_names
    folders_qs = NanoporeSequenceFolder.objects.select_related('datafolder')

    sequences_qs = UserNanoporeSequence.objects.prefetch_related(
        Prefetch('nanoporesequencefolder_set', queryset=folders_qs)
    ).filter(
        nanoporesequencefolder__datafolder__name__in=output_names
    ).distinct()

    # Build mapping: output_name -> list of sequence IDs
    seq_map = {}
    for us in sequences_qs:
        # find folders that match output_names
        for folder in us.nanoporesequencefolder_set.all():
            folder_name = folder.datafolder.name
            if folder_name in output_names:
                seq_map.setdefault(folder_name, []).append(us.nanopore_sequence.id)

    # Build response data
    data = []
    for job in jobs:
        sequence_ids = seq_map.get(job.output_name, [])
        data.append({
            'created_at': job.created_at.strftime("%Y-%m-%d"),
            'output_name': job.output_name,
            'status': job.job.status,
            'id': job.id,
            'sequence_count': len(sequence_ids),
            'sequence_ids': sequence_ids
        })

    return JsonResponse({'basecalling_jobs': data}, status=200)

def basecall(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    user = request.user

    if not user or not hasattr(user, 'userprofile'):
        return JsonResponse({'error': 'User profile not found'}, status=400)
    if not user.userprofile.verified:
        return JsonResponse({'error': 'Email address is not verified'}, status=403)
    if not user.userprofile.elevated_access:
        return JsonResponse({'error': 'User is not allowed to basecall'}, status=403)

    active_job_exists = BasecallingJob.objects.filter(
        job__user=user
    ).exclude(job__status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED', 'FAILED_BOOT']).exists()

    if active_job_exists:
        return JsonResponse({
            'error': 'You already have an active or queued base-calling job. Please wait for it to finish before starting another.'
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

    podfile_ids = data.get('podfile_ids')
    if not podfile_ids or not isinstance(podfile_ids, list):
        return JsonResponse({'error': 'podfile_ids must be a list of IDs'}, status=400)

    model = data.get('model', 'fast')
    kit = data.get('kit', 'SQK-RBK114-24')

    job = placeholder_tapis_job(user, 'dnasubway-dorado')
    BasecallingJob.objects.create(job=job, model=model, output_name=output, kit_name=kit)

    linked_count = 0
    warnings = []

    for pid in podfile_ids:
        try:
            podfile_instance = PodFile.objects.get(id=pid, user=user)
            JobPodFile.objects.create(podfile=podfile_instance, job=job)
            linked_count += 1
        except PodFile.DoesNotExist:
            warnings.append(f"PodFile with ID {pid} not found or not owned by user.")

    if linked_count == 0:
        return JsonResponse({'error': 'No valid PodFiles provided.'}, status=400)

    run_basecall_task.delay(job.id, model, kit, output)

    return JsonResponse({
        'success': f'{linked_count} files linked to job.',
        'linked_ids': podfile_ids,
        'warnings': warnings,
    })

def usernanoporesequences(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Invalid request method'}, status=400)

    # Get user_id from query params or current user
    user_id = request.GET.get('user_id') or (request.user.id if request.user and request.user.is_authenticated else None)
    if not user_id:
        return JsonResponse({'error': 'Missing user'}, status=400)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    # All output_names from BasecallingJob for this user
    output_names = BasecallingJob.objects.filter(job__user=user).values_list("output_name", flat=True)

    # Prefetch all folders for this user's sequences
    folders_qs = NanoporeSequenceFolder.objects.select_related('datafolder')

    # Basecalled sequences: linked to a folder with name in output_names
    basecalled_qs = UserNanoporeSequence.objects.filter(
        user=user,
        nanoporesequencefolder__datafolder__name__in=output_names
    ).select_related('nanopore_sequence').prefetch_related(
        Prefetch('nanoporesequencefolder_set', queryset=folders_qs)
    ).distinct()

    basecalled = defaultdict(list)
    for us in basecalled_qs:
        folder = next((f for f in us.nanoporesequencefolder_set.all() if f.datafolder.name in output_names), None)
        if folder:
            df_name = folder.datafolder.name
            basecalled[df_name].append({
                "id": us.nanopore_sequence.id,
                "name": us.nanopore_sequence.name,
            })

    # Saved sequences: sequences not basecalled
    saved_qs = UserNanoporeSequence.objects.filter(user=user).exclude(
        id__in=basecalled_qs.values_list('id', flat=True)
    ).select_related('nanopore_sequence').prefetch_related(
        Prefetch('nanoporesequencefolder_set', queryset=folders_qs)
    ).distinct()

    saved = defaultdict(list)
    for us in saved_qs:
        if us.nanoporesequencefolder_set.exists():
            # group by folder name (take all folders)
            for folder in us.nanoporesequencefolder_set.all():
                df_name = folder.datafolder.name
                saved[df_name].append({
                    "id": us.nanopore_sequence.id,
                    "name": us.nanopore_sequence.name,
                })
        else:
            # sequences not in any folder
            saved["Ungrouped sequences"].append({
                "id": us.nanopore_sequence.id,
                "name": us.nanopore_sequence.name,
            })

    return JsonResponse({
        "basecalled": dict(basecalled),
        "saved": dict(saved),
    }, status=200)

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
    if isinstance(nanoporesequence_ids, str):
        nanoporesequence_ids = [nanoporesequence_ids]

    if not isinstance(nanoporesequence_ids, list):
        return JsonResponse({'error': 'seq_ids must be a list or a string'}, status=400)

    responses = []
    overall_status = "error"
    for seq_id in nanoporesequence_ids:
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
            overall_status = "success"
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

    if overall_status == "error":
        return JsonResponse({'results': responses, 'error': 'All files failed to upload', 'status': 'error'}, status=400)
    metadata_files = MetadataFile.objects.filter(
        project_links__project=project
    )

    metadata_files.update(validated=False)
    return JsonResponse({'results': responses, 'status': 'success'}, status=200)

def upload_pod5(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST method required"}, status=405)

    user = request.user
    if not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    if 'file' not in request.FILES:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    uploaded_file = request.FILES['file']
    try:
        podfile_instance = PodFile.objects.create(user=user, name=uploaded_file.name)

        podfile_filename = f"{podfile_instance.id}.pod5"
        podfile_instance.file.save(podfile_filename, uploaded_file, save=True)

        return JsonResponse({"id": podfile_instance.id})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

def upload_metabarcoding(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST method required"}, status=405)

    user = request.user
    if not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    if 'file' not in request.FILES:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    pid = request.POST.get("pid")
    if not pid:
        return JsonResponse({"error": "Project ID required"}, status=400)

    try:
        project = Project.objects.get(id=pid)
    except Project.DoesNotExist:
        return JsonResponse({"error": "Project not found"}, status=404)

    demux_job = (
        Job.objects.filter(
            project=project,
            appId=settings.QIIME2_DEMUX_APP_ID,
        )
        .order_by("-id")
        .first()
    )
    demux_run_or_success = False
    if demux_job:
        demux_run_or_success = demux_job.status not in ["CANCELLED", "FAILED", "STOPPED"]

    if demux_run_or_success:
        return JsonResponse({"error": "You cannot upload metabarcoding files while demuliplexing is running or after it succeeds"}, status=400)

    uploaded_file = request.FILES['file']

    if not uploaded_file.name.endswith(".fastq.gz"):
        return JsonResponse({"error": "File must have .fastq.gz extension"}, status=400)

    filename = uploaded_file.name
    FILENAME_REGEX = re.compile(rf'^[A-Za-z0-9\.-]+_[^_]+_L[0-9]{{3}}_R{"[12]" if project.read_type == "paired" else "1"}_001\.fastq\.gz$')
    if not FILENAME_REGEX.match(filename):
        return JsonResponse({"error": "Invalid filename format."}, status=400)

    # Validate contents
    valid, error_msg = validate_fastq_gz(uploaded_file)
    if not valid:
        return JsonResponse({"error": error_msg}, status=400)

    # Check if a file with the same name already exists for this project
    if ProjectMetabarcodingFile.objects.filter(
        project=project, metabarcoding_file__name=uploaded_file.name
    ).exists():
        return JsonResponse({"error": f"A file named '{uploaded_file.name}' already exists for this project."}, status=400)

    try:
        # Create file record
        metabarcoding_file = MetabarcodingFile.objects.create(
            user=user,
            name=uploaded_file.name,
        )
        metabarcoding_file.file.save(f"{metabarcoding_file.id}.fastq.gz", uploaded_file, save=True)

        # Link to project
        ProjectMetabarcodingFile.objects.create(
            project=project,
            metabarcoding_file=metabarcoding_file
        )
        metadata_files = MetadataFile.objects.filter(
            project_links__project=project
        )

        metadata_files.update(validated=False)

        return JsonResponse({"id": metabarcoding_file.id})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

def delete_metabarcoding_file(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_id = data.get('metabarcoding_file_id')

    demux_job = (
        Job.objects.filter(
            project=project,
            appId=settings.QIIME2_DEMUX_APP_ID,
        )
        .order_by("-id")
        .first()
    )
    demux_run_or_success = False
    if demux_job:
        demux_run_or_success = demux_job.status not in ["CANCELLED", "FAILED", "STOPPED"]

    if demux_run_or_success:
        return JsonResponse({"error": "You cannot delete metabarcoding files while demuliplexing is running or after it succeeds"}, status=400)

    if not file_id:
        return JsonResponse({'error': 'metabarcoing_file_id is required'}, status=400)

    # Get the project and data file
    try:
        metabarcoding_file = MetabarcodingFile.objects.get(id=file_id)
    except MetabarcodingFile.DoesNotExist:
        return JsonResponse({'error': 'Metabarcoding file not found'}, status=404)

    project_metabarcoding_file = ProjectMetabarcodingFile.objects.filter(project=project, metabarcoding_file=metabarcoding_file).first()

    # Verify the file belongs to the project
    if not project_metabarcoding_file:
        return JsonResponse({'error': 'Metabarcoding file does not belong to this project'}, status=403)

    if ProjectMetabarcodingFile.objects.filter(metabarcoding_file=metabarcoding_file).count() < 2:
        if metabarcoding_file.file:
            os.remove(metabarcoding_file.file.name)
        metabarcoding_file.delete()
    project_metabarcoding_file.delete()
    metadata_files = MetadataFile.objects.filter(
        project_links__project=project
    )

    metadata_files.update(validated=False)
    return JsonResponse({'success': 'Metabarcoding file removed successfully'})


def upload_metadata(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST method required"}, status=405)

    user = request.user
    if not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    if 'file' not in request.FILES:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    pid = request.POST.get("pid")
    if not pid:
        return JsonResponse({"error": "Project ID required"}, status=400)

    try:
        project = Project.objects.get(id=pid)
    except Project.DoesNotExist:
        return JsonResponse({"error": "Project not found"}, status=404)

    uploaded_file = request.FILES['file']

    if not uploaded_file.name.endswith(".tsv") and not uploaded_file.name.endswith(".txt"):
        return JsonResponse({"error": "File must have .tsv or .txt extension"}, status=400)

    # Validate contents
    valid, errors = validate_qiime2_metadata_format(uploaded_file)
    if not valid:
        return JsonResponse({"error": "Invalid metadata file", "warnings": errors}, status=400)

    # Check if a file with the same name already exists for this project
    if ProjectMetadataFile.objects.filter(
        project=project, metadata_file__name=uploaded_file.name
    ).exists():
        return JsonResponse({"error": f"A file named '{uploaded_file.name}' already exists for this project."}, status=400)

    try:
        # Create file record
        metadata_file = MetadataFile.objects.create(
            user=user,
            name=uploaded_file.name,
        )
        uploaded_file.seek(0)
        metadata_file.file.save(f"{metadata_file.id}.tsv", uploaded_file, save=True)

        # Link to project
        ProjectMetadataFile.objects.create(
            project=project,
            metadata_file=metadata_file
        )

        return JsonResponse({"id": metadata_file.id})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

def save_metadata(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    metadata_id = data.get('metadata_id')
    file_contents = data.get('file_contents')
    if not metadata_id or not file_contents:
        return JsonResponse({"error": "Metadata id and file contents required"}, status=400)

    try:
        project_metadata_file = ProjectMetadataFile.objects.get(
            project=project, metadata_file__id=metadata_id
        )
    except ProjectMetadataFile.DoesNotExist:
        return JsonResponse({"error": "This metadata file does not belong to this project."}, status=400)

    metadata_file = project_metadata_file.metadata_file
    if not metadata_file:
        return JsonResponse({"error": "No such metadata file."}, status=404)

    errors = validate_qiime2_tsv(file_contents)
    if errors:
        return JsonResponse({"error": "Invalid metadata file", "warnings": errors}, status=400)

    used_in_active_dada2 = Dada2JobDetail.objects.filter(
        metadata_file=metadata_file
    ).exclude(
        job__status__in=["STOPPED", "CANCELLED", "FAILED"]
    ).exists()

    user = request.user

    if used_in_active_dada2:
        existing_names = ProjectMetadataFile.objects.filter(project=project).values_list(
            'metadata_file__name', flat=True
        )
        base_filename = "metadata.tsv"
        filename = base_filename
        counter = 1
        while filename in existing_names:
            filename = f"metadata_{counter}.tsv"
            counter += 1

        new_metadata_file = MetadataFile.objects.create(
            name=filename,
            user=user,
            validated=False
        )
        new_metadata_file.file.save(f"{new_metadata_file.id}.tsv", ContentFile(file_contents), save=True)

        ProjectMetadataFile.objects.create(
            project=project,
            metadata_file=new_metadata_file
        )
    else:
        storage = metadata_file.file.storage
        path = metadata_file.file.name  # e.g. "metadata_files/myfile.tsv"
        storage.delete(path)
        storage.save(path, ContentFile(file_contents))
        metadata_file.validated = False
        metadata_file.save()

    return JsonResponse({'status': 'success'})

def get_metadata_content(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    metadata_id = request.GET.get('id')
    if not metadata_id:
        return JsonResponse({'error': 'id parameter is required'}, status=400)

    try:
        metadata_file = MetadataFile.objects.get(id=metadata_id)
    except MetadataFile.DoesNotExist:
        return JsonResponse({'error': 'Invalid MetadataFile'}, status=404)

    file_obj = metadata_file.file
    try:
        if not file_obj:
            return JsonResponse({'error': 'No MetadataFile found'}, status=404)
        if hasattr(file_obj, 'closed') and not file_obj.closed:
            file_obj.close()

        file_obj.open('r')
    except (FileNotFoundError, SuspiciousFileOperation, IOError, OSError) as e:
        return JsonResponse({'error': "Unable to open file"}, status=400)

    reader = csv.reader(file_obj, delimiter='\t')

    header = None
    q2_types = []
    rows = []

    for row in reader:
        if not row:  # skip empty lines
            continue

        first_cell = row[0].strip()

        if first_cell.startswith("#q2:types"):
            q2_types = [cell.strip() for cell in row[1:]]  # skip the "#q2:types" cell
            continue

        # Detect header line
        if header is None:
            if first_cell.startswith("#SampleID") or first_cell.startswith("#Sample ID"):
                header = row
                continue
            elif first_cell.startswith("#"):
                # Comment line before header — skip it
                continue
            else:
                header = row
                continue

        # Skip comments after header
        if first_cell.startswith("#"):
            continue

        rows.append(row)

    file_obj.close()

    q2_types = q2_types or []

    return JsonResponse({
        'header': header or [],
        'q2_types': q2_types,
        'rows': rows,
    })

def create_datafolder(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    user = request.user
    name = parsed_data['data'].get('name')

    folder, created = DataFolder.objects.get_or_create(user=user, name=name)
    if not created:
        return JsonResponse({'error': 'DataFolder with this name already exists.'}, status=400)

    return JsonResponse({'id': folder.id, 'name': folder.name})

def rename_datafolder(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    folder_id = parsed_data['data'].get('id')
    new_name = parsed_data['data'].get('name')
    try:
        folder = DataFolder.objects.get(id=folder_id, user=request.user)
    except DataFolder.DoesNotExist:
        return JsonResponse({'error': 'Invalid DataFolder for user'}, status=404)

    if DataFolder.objects.filter(user=request.user, name=new_name).exclude(id=folder_id).exists():
        return JsonResponse({'error': 'Another folder with this name already exists.'}, status=400)

    folder.name = new_name
    folder.save()
    return JsonResponse({'id': folder.id, 'name': folder.name})

def delete_datafolder(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    folder_id = parsed_data['data'].get('id')
    try:
        folder = DataFolder.objects.get(id=folder_id, user=request.user)
    except DataFolder.DoesNotExist:
        return JsonResponse({'error': 'Invalid DataFolder for user'}, status=404)
    folder.delete()
    return JsonResponse({'status': 'success'})

def add_sanger_to_folder(request):
    parsed_data = parse_user_data(request)
    data = parsed_data.get('data', {})
    datafile_id = data.get('datafile_id')
    folder_id = data.get('datafolder_id')

    try:
        datafile = DataFile.objects.get(id=datafile_id, user=request.user)
    except DataFile.DoesNotExist:
        return JsonResponse({'error': 'Invalid DataFile for user'}, status=404)
    try:
        folder = DataFolder.objects.get(id=folder_id, user=request.user)
    except DataFolder.DoesNotExist:
        return JsonResponse({'error': 'Invalid DataFolder for user'}, status=404)

    sanger_folder, created = SangerSequenceFolder.objects.get_or_create(
        datafile=datafile,
        datafolder=folder
    )
    return JsonResponse({'id': sanger_folder.id})

def remove_sanger_from_folder(request):
    parsed_data = parse_user_data(request)
    folder_item_id = parsed_data['data'].get('id')

    try:
        sanger_folder = SangerSequenceFolder.objects.get(
            id=folder_item_id,
            datafolder__user=request.user  # ensures the folder belongs to the user
        )
    except SangerSequenceFolder.DoesNotExist:
        return JsonResponse({'error': 'Invalid SangerSequenceFolder for user'}, status=404)

    sanger_folder.delete()
    return JsonResponse({'status': 'success'})

def add_nanopore_to_folder(request):
    parsed_data = parse_user_data(request)
    data = parsed_data.get('data', {})
    nanopore_id = data.get('nanopore_sequence_id')
    folder_id = data.get('datafolder_id')

    try:
        nanopore = NanoporeSequence.objects.get(id=nanopore_id)
    except NanoporeSequence.DoesNotExist:
        return JsonResponse({'error': 'Invalid NanoporeSequence'}, status=404)

    try:
        folder = DataFolder.objects.get(id=folder_id, user=request.user)
    except DataFolder.DoesNotExist:
        return JsonResponse({'error': 'Invalid DataFolder for user'}, status=404)

    try:
        user_nanopore_seq = UserNanoporeSequence.objects.get(user=request.user, nanopore_sequence=nanopore)
    except UserNanoporeSequence.DoesNotExist:
        return JsonResponse({'error': 'Invalid UserNanoporeSequence'}, status=404)

    nanopore_folder, _ = NanoporeSequenceFolder.objects.get_or_create(
        usernanoporesequence=user_nanopore_seq,
        datafolder=folder
    )
    return JsonResponse({'id': nanopore_folder.id})

def remove_nanopore_from_folder(request):
    parsed_data = parse_user_data(request)
    folder_item_id = parsed_data['data'].get('id')

    try:
        nanopore_folder = NanoporeSequenceFolder.objects.get(
            id=folder_item_id,
            datafolder__user=request.user
        )
    except NanoporeSequenceFolder.DoesNotExist:
        return JsonResponse({'error': 'Invalid NanoporeSequenceFolder for user'}, status=404)

    nanopore_folder.delete()
    return JsonResponse({'status': 'success'})

def list_folders_with_connections(request):
    if request.method != "GET":
        return JsonResponse({'error': 'GET method required'}, status=405)

    if not request.user or not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    user_elevated_access = hasattr(request.user, 'userprofile') and request.user.userprofile.elevated_access
    submission = GenbankSubmission() if user_elevated_access else None

    user_id = request.GET.get('user_id')
    if user_id:
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
    else:
        user = request.user

    is_self = user == request.user

    data = {}

    # 1. Add repository items first
    data[""] = []

    # Sanger sequences in repository not in any folder
    linked_sanger_ids = set(
        SangerSequenceFolder.objects.filter(datafolder__user=user)
        .values_list('datafile_id', flat=True)
    )
    sanger_in_repo = DataFile.objects.filter(in_sequence_repository=True, user=user).order_by('name')
    if not is_self:
        sanger_in_repo.filter(is_public=True)
    for s in sanger_in_repo:
        if s.id not in linked_sanger_ids:
            data[""].append({
                **datafile_info(s, user_elevated_access, submission),
                "id": s.id,
                "type": "sanger"
            })

    # Nanopore sequences not in any folder
    linked_nano_ids = set(
        NanoporeSequenceFolder.objects.filter(datafolder__user=user)
        .values_list('usernanoporesequence__id', flat=True)
    )
    all_user_nanopores = UserNanoporeSequence.objects.filter(user=user).order_by('nanopore_sequence__name')
    if not is_self:
        all_user_nanopores = all_user_nanopores.filter(is_public=True)
    for un in all_user_nanopores:
        if un.id not in linked_nano_ids:
            data[""].append({
                "name": un.nanopore_sequence.name,
                "id": un.nanopore_sequence.id,
                "is_public": un.is_public,
                "username": un.user.username,
                "type": "nanopore"
            })

    # 2. Add actual folders and their items
    folders = DataFolder.objects.filter(user=user).order_by('name')
    for folder in folders:
        folder_name = folder.name
        data[folder_name] = [{'folder_id': folder.id}]

        # Sanger connections
        sanger_connections = SangerSequenceFolder.objects.filter(datafolder=folder).order_by('datafile__name')
        if not is_self:
            sanger_connections = sanger_connections.filter(datafile__is_public=True)
        for sf in sanger_connections:
            data[folder_name].append({
                **datafile_info(sf.datafile, user_elevated_access, submission),
                "id": sf.datafile.id,
                "type": "sanger"
            })

        # Nanopore connections
        nano_connections = NanoporeSequenceFolder.objects.filter(datafolder=folder).order_by('usernanoporesequence__nanopore_sequence__name')
        if not is_self:
            nano_connections = nano_connections.filter(
                usernanoporesequence__is_public=True
            )
        for nf in nano_connections:
            data[folder_name].append({
                "name": nf.usernanoporesequence.nanopore_sequence.name,
                "id": nf.usernanoporesequence.nanopore_sequence.id,
                "is_public": nf.usernanoporesequence.is_public,
                "username": nf.usernanoporesequence.user.username,
                "type": "nanopore"
            })
    return JsonResponse(data)

def export_to_genbank(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])
    user = request.user

    if not user or not hasattr(user, 'userprofile'):
        return JsonResponse({'error': 'User profile not found'}, status=400)
    if not user.userprofile.verified:
        return JsonResponse({'error': 'Email address is not verified'}, status=403)
    if not user.userprofile.elevated_access:
        return JsonResponse({'error': 'User is not allowed to export to GenBank'}, status=403)

    data = parsed_data['data']
    sequence_id = data.get('file_id')
    if not sequence_id:
        return JsonResponse({'error': 'Invalid sequence id'}, status=400)

    type_of_sample = data.get('typeOfSample')
    seq_type = type_of_sample.split(' ', 1)[0]
    if seq_type.upper() not in ["RBCL", "COI", "CO1"]:
        return JsonResponse({'error': 'Unsupported sample type'}, status=400)
    trans_codes = {
        "rbcL": 1,
        "COI Invertebrates": 5,
        "COI Vertebrates": 2,
        "COI Echinoderm": 9,
    }
    trans_table = trans_codes.get(type_of_sample)
    if not trans_table:
        return JsonResponse({'error': 'No such translation table'}, status=400)

    primers_available = ["rbcL", "COI - fish", "COI - mammals and insects", "COI - metazoans"]
    primer_used = data.get('primerUsed')
    if primer_used not in primers_available:
        return JsonResponse({'error': 'No such primer available'}, status=400)

    f_primer = primer_used
    r_primer = primer_used

    genus = data.get('genus')
    if not genus:
        return JsonResponse({'error': 'Genus is required'}, status=400)
    species = data.get('species')
    if not species:
        return JsonResponse({'error': 'Species is required'}, status=400)
    available_projects = ["Urban Barcode Project", "Barcode Long Island", "DNA Subway General Projects", "US Ants", "Barcode Suzhou", "Barcode Puerto Rico"]
    project = data.get('project')
    if not project or project not in available_projects:
        return JsonResponse({'error': 'Not a valid project'}, status=400)
    isolation_source = data.get('isolationSource')
    if not isolation_source:
        return JsonResponse({'error': 'Isolation source is required'}, status=400)
    host = data.get('hostOrganism')
    tax = data.get('identifiedBy')
    if not tax:
        return JsonResponse({'error': 'Identifier is required'}, status=400)
    tax_email = data.get('identifierEmail')
    if not tax_email:
        return JsonResponse({'error': 'Identifier email is required'}, status=400)
    date_collected = data.get('dateCollected')
    collected_date = None
    if date_collected:
        try:
            parsed_date = datetime.strptime(date_collected, "%Y-%m-%d")
            date_collected = parsed_date.strftime("%d/%m/%Y")
        except ValueError:
            date_collected = None
    else:
        date_collected = None
    if not date_collected:
        return JsonResponse({'error': 'Invalid date'}, status=400)
    country_code = data.get('country')
    if not country_code:
        return JsonResponse({'error': 'Country is required'}, status=400)

    country = INSDC_COUNTRY_MAP.get(country_code, "")
    if not country:
        return JsonResponse({'error': 'Country is not in the approved list'}, status=400)

    state = data.get('state')
    if not state:
        return JsonResponse({'error': 'State/Province is required'}, status=400)
    city = data.get('city')
    if not city:
        return JsonResponse({'error': 'City is required'}, status=400)
    site_desc = data.get('exactSite')
    if not site_desc:
        return JsonResponse({'error': 'Exact site is required'}, status=400)
    latitude = data.get('latitude')
    if not latitude:
        return JsonResponse({'error': 'Latitude is required'}, status=400)
    longitude = data.get('longitude')
    if not longitude:
        return JsonResponse({'error': 'Longitude is required'}, status=400)
    sex = data.get('sexOfSpecimen')
    institution_storing = data.get('institutionStoring')
    notes = data.get('notes')
    stage = data.get('lifeStageOfSpecimen')
    if sex:
        sex = sex.lower()
    if stage:
        stage = stage.lower()

    if latitude:
        match = re.match(r"^([-+]?\d+(?:\.\d+)?)\s*([NSns])$", latitude.strip())
        if not match:
            return JsonResponse({'error': 'Latitude must be a number followed by N or S'}, status=400)

        num, direction = match.groups()
        num = float(num)
        if not (0 <= num <= 90):
            return JsonResponse({'error': 'Latitude must be between 0 and 90'}, status=400)

        latitude = f"{num}{direction.upper()}"

    if longitude:
        match = re.match(r"^([-+]?\d+(?:\.\d+)?)\s*([EWew])$", longitude.strip())
        if not match:
            return JsonResponse({'error': 'Longitude must be a number followed by E or W'}, status=400)

        num, direction = match.groups()
        num = float(num)
        if not (0 <= num <= 180):
            return JsonResponse({'error': 'Longitude must be between 0 and 180'}, status=400)

        longitude = f"{num}{direction.upper()}"
    authors = data.get('authors', [])

    try:
        data_file = DataFile.objects.get(id=sequence_id)
    except DataFile.DoesNotExist:
        return JsonResponse({'error': 'No such sequence'}, status=400)

    if data_file.user != user:
        return JsonResponse({'error': 'You can only export a sequence you own'}, status=400)

    error = validate_consensus_export(data_file)
    if error:
        return JsonResponse({'error': error}, status=400)

    consensus = data_file.reads
    if not consensus:
        return JsonResponse({'error': 'The consensus must have reads'}, status=400)

    formed_data = {
        "genus": genus,
        "species": species,
        "trans_table": trans_table,
        "project": project,
        "isolation_source": isolation_source,
        "host": host,
        "tax": tax,
        "date_collected": date_collected,
        "country": country, "state": state, "city": city, "site_desc": site_desc,
        "latitude": latitude, "longitude": longitude,
        "sex": sex, "stage": stage,
        "f_primer": primer_used, "r_primer": primer_used,
    }

    valid_authors = [a for a in authors if a.get("firstName") and a.get("lastName")]

    if not valid_authors:
        return JsonResponse({'error': 'At least one author with both first and last name is required'}, status=400)

    for i, a in enumerate(valid_authors, start=1):
        formed_data[f"author_first{i}"] = a["firstName"]
        formed_data[f"author_last{i}"] = a["lastName"]

    formed_data_json = json.dumps(formed_data)

    specimen, created = Specimen.objects.update_or_create(
        datafile=data_file,
        defaults={
            "codon": str(trans_table),
            "institution_storing": institution_storing,
            "notes": notes,
            "primer_used": primer_used,
            "genus": genus,
            "species": species,
            "isolation_source": isolation_source,
            "sample_collected_from_host": True if host else False,
            "host_organism_name": host,
            "identifier_name": tax,
            "identifier_email": tax_email,
            "date_collected": parsed_date.date(),
            "country": country,
            "state_province": state,
            "city": city,
            "exact_site": site_desc,
            "latitude": latitude,
            "longitude": longitude,
            "sex": sex,
            "life_stage": stage,
        }
    )

    Author.objects.filter(datafile=data_file).delete()

    for a in valid_authors:
        Author.objects.create(
            datafile=data_file,
            specimen=specimen,
            first_name=a["firstName"],
            last_name=a["lastName"],
            affiliation=a.get("affiliation", "")
        )

    specimen_id = f"DNAS2-{specimen.id:X}-{base10_to_base36(int(sequence_id))}"

    rec = GenbankRecord(
        email=user.email,
        sequence_id=sequence_id,
        specimen_id=specimen_id,
        seq_type=seq_type,
        consensus=consensus,
        data=formed_data_json,
    )

    submission = GenbankSubmission()
    result = submission.run(rec)
    if result["status"] != "success":
        return JsonResponse({'error': result.get("message", "GenBank submission failed")}, status=400)
    data_file.exported = True
    data_file.save()
    return JsonResponse({'success': f"GenBank submission succeeded with specimen ID {specimen_id}"})

def user_id(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    username = request.GET.get('username')
    email = request.GET.get('email')
    if username and email:
        user = User.objects.filter(username=username, email=email).first()
    elif username:
        user = User.objects.filter(username=username).first()
    elif email:
        user = User.objects.filter(email=email).first()
    else:
        return JsonResponse({'error': 'username or email parameter is required'}, status=400)

    if not user:
        return JsonResponse({'error': 'No such user'}, status=404)

    return JsonResponse({'uid': user.id})

def demux(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    project = parsed_data['project']
    data = parsed_data['data']
    randSamples = data.get("randSamples", "1000")
    user = request.user

    active_demux_job_exists = Job.objects.filter(
        user=user,
        appId=settings.QIIME2_DEMUX_APP_ID,
    ).exclude(status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED', 'FAILED_BOOT']).exists()

    if active_demux_job_exists:
        return JsonResponse({
            'error': 'You already have an active or queued demultiplexing job. Please wait for it to finish before starting another.'
        }, status=400)

    metabarcoding_files = ProjectMetabarcodingFile.objects.filter(project=project).select_related('metabarcoding_file')
    if not metabarcoding_files.exists():
        return JsonResponse({'error': 'No metabarcoding files found for this project.'}, status=400)
    try:
        validate_metabarcoding_pairs(metabarcoding_files, project.read_type)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    job = placeholder_tapis_job(user, settings.QIIME2_DEMUX_APP_ID)
    job.project = project
    job.save()
    DemuxJobDetail.objects.create(
        job=job,
        rand_samples=randSamples,
    )
    submit_demux_job_task.delay(job.id, randSamples)
    return JsonResponse({'job_uuid': job.uuid, 'status': job.status})

def dada2(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    project = parsed_data['project']
    data = parsed_data['data']
    user = request.user

    # Get parameters (default to 0)
    try:
        trimLeft = int(data.get("trimLeft", 0))
        truncLen = int(data.get("truncLen", 0))
        trimLeftF = int(data.get("trimLeftF", 0))
        trimLeftR = int(data.get("trimLeftR", 0))
        truncLenF = int(data.get("truncLenF", 0))
        truncLenR = int(data.get("truncLenR", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "Trim Left and truncation length must be integers."}, status=400)
    file_id = data.get('metadata_file_id')

    if not file_id:
        return JsonResponse({'error': 'metadata_file_id is required'}, status=400)

    try:
        metadata_file = MetadataFile.objects.get(id=file_id)
    except MetadataFile.DoesNotExist:
        return JsonResponse({'error': 'Metadata file not found'}, status=404)

    if not metadata_file.validated:
        return JsonResponse({'error': 'Metadata file not validated'}, status=400)

    paired_flag = '1' if getattr(project, 'read_type', 'single') == 'paired' else '0'

    # Ensure there's a finished DemuxResult for this project
    finished_demux_jobs = Job.objects.filter(
        project=project,
        appId=settings.QIIME2_DEMUX_APP_ID,
        status='FINISHED'
    )

    if not finished_demux_jobs.exists():
        return JsonResponse({'error': 'No completed demux job found for this project.'}, status=400)

    demux_result = DemuxResult.objects.filter(job__in=finished_demux_jobs).first()
    if not demux_result or not demux_result.demux_qza:
        return JsonResponse({'error': 'Demux QZA file not found.'}, status=400)

    # Prevent duplicate active/queued DADA2 jobs
    active_dada2_exists = Job.objects.filter(
        user=user,
        appId=settings.QIIME2_DADA2_APP_ID
    ).exclude(status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED', 'FAILED_BOOT']).exists()

    if active_dada2_exists:
        return JsonResponse({
            'error': 'You already have an active or queued DADA2 job. Please wait for it to finish.'
        }, status=400)
    existing_detail = (
        Dada2JobDetail.objects.filter(
            job__status='FINISHED',
            job__project=project,
            job__appId=settings.QIIME2_DADA2_APP_ID,
            metadata_file=metadata_file,
            paired=(paired_flag == '1'),
            trimLeft=trimLeft,
            truncLen=truncLen,
            trimLeftF=trimLeftF,
            trimLeftR=trimLeftR,
            truncLenF=truncLenF,
            truncLenR=truncLenR
        )
        .select_related('job')
        .order_by('-job__id')
        .first()
    )
    if existing_detail:
        return JsonResponse({
            'error': f"A successful DADA2 job with these parameters already exists for this project: trim{existing_detail.job.id}",
        }, status=400)

    # Create Job placeholder
    job = placeholder_tapis_job(user, settings.QIIME2_DADA2_APP_ID)
    job.project = project
    job.save()

    # Save job details
    Dada2JobDetail.objects.create(
        job=job,
        metadata_file=metadata_file,
        paired=(paired_flag == '1'),
        trimLeft=trimLeft,
        truncLen=truncLen,
        trimLeftF=trimLeftF,
        trimLeftR=trimLeftR,
        truncLenF=truncLenF,
        truncLenR=truncLenR
    )

    # Submit async task
    submit_dada2_job_task.delay(
        job.id,
        demux_result.demux_qza.name,
        metadata_file.file.name,
        paired_flag,
        trimLeft, truncLen, trimLeftF, trimLeftR, truncLenF, truncLenR
    )

    return JsonResponse({'job_uuid': job.uuid, 'status': job.status})

def rarefaction(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    project = parsed_data['project']
    data = parsed_data['data']
    user = request.user

    # Get parameters
    try:
        minDepth = int(data.get("minDepth", 1))
        maxDepth = int(data.get("maxDepth", 3000))
    except (TypeError, ValueError):
        return JSONResponse({"error": "Min and max depth must be integers."}, status=400)

    # -----------------------------
    # Get finished jobs
    # -----------------------------
    finished_dada2_jobs = Job.objects.filter(
        project=project,
        appId=settings.QIIME2_DADA2_APP_ID,
        status='FINISHED'
    ).order_by('-id')

    finished_proname_refine_jobs = Job.objects.filter(
        project=project,
        appId=settings.QIIME2_PRONAME_REFINE_APP_ID,
        status='FINISHED'
    ).order_by('-id')

    if not finished_dada2_jobs.exists() and not finished_proname_refine_jobs.exists():
        return JsonResponse(
            {'error': 'No completed dada2 or proname refine job found for this project.'},
            status=400
        )

    # -----------------------------
    # Select job (prefer DADA2)
    # -----------------------------
    source_job = None
    source_result = None
    source_job_detail = None

    if finished_dada2_jobs.exists():
        primary_jobs = finished_dada2_jobs.filter(primary=True)
        source_job = primary_jobs.first() if primary_jobs.exists() else finished_dada2_jobs.first()

        if source_job:
            source_result = Dada2Result.objects.filter(job=source_job).first()
            source_job_detail = Dada2JobDetail.objects.filter(job=source_job).first()

    elif finished_proname_refine_jobs.exists():
        primary_jobs = finished_proname_refine_jobs.filter(primary=True)
        source_job = primary_jobs.first() if primary_jobs.exists() else finished_proname_refine_jobs.first()

        if source_job:
            source_result = PronameRefineResult.objects.filter(job=source_job).first()
            source_job_detail = PronameRefineJobDetail.objects.filter(job=source_job).first()

    # -----------------------------
    # Validate required inputs
    # -----------------------------
    if not source_result or not getattr(source_result, 'rooted_tree_qza', None) or not getattr(source_result, 'trim_table_qza', None):
        return JsonResponse(
            {'error': 'Rooted Tree or Trim Table QZA file not found.'},
            status=400
        )

    if not source_job_detail or not getattr(source_job_detail, 'metadata_file', None):
        return JsonResponse(
            {'error': 'Could not get metadata file.'},
            status=400
        )

    # -----------------------------
    # Prevent duplicate / active jobs
    # -----------------------------
    active_rarefaction_exists = Job.objects.filter(
        user=user,
        appId=settings.QIIME2_RAREFACTION_APP_ID
    ).exclude(status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED', 'FAILED_BOOT']).exists()

    if active_rarefaction_exists:
        return JsonResponse({
            'error': 'You already have an active or queued Rarefaction job. Please wait for it to finish.'
        }, status=400)

    existing_detail = (
        RarefactionJobDetail.objects.filter(
            job__status='FINISHED',
            job__project=project,
            job__appId=settings.QIIME2_RAREFACTION_APP_ID,
            dada2_job=source_job,  # keeping field name unchanged
            minDepth=minDepth,
            maxDepth=maxDepth,
        )
        .select_related('job')
        .order_by('-job__id')
        .first()
    )

    if existing_detail:
        return JsonResponse({
            'error': f"A successful Rarefaction job with these parameters already exists for this project: ar{existing_detail.job.id}",
        }, status=400)

    # -----------------------------
    # Create Job
    # -----------------------------
    job = placeholder_tapis_job(user, settings.QIIME2_RAREFACTION_APP_ID)
    job.project = project
    job.save()

    # -----------------------------
    # Save job details
    # -----------------------------
    RarefactionJobDetail.objects.create(
        job=job,
        dada2_job=source_job,  # keeping DB schema unchanged
        minDepth=minDepth,
        maxDepth=maxDepth,
    )

    # -----------------------------
    # Submit async task
    # -----------------------------
    submit_rarefaction_job_task.delay(
        job.id,
        source_result.rooted_tree_qza.name,
        source_result.trim_table_qza.name,
        source_job_detail.metadata_file.file.name,
        minDepth,
        maxDepth
    )

    return JsonResponse({'job_uuid': job.uuid, 'status': job.status})

def coremetrics(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    project = parsed_data['project']
    data = parsed_data['data']
    user = request.user

    classifier = data.get("classifier", "")
    try:
        sdepth = int(data.get("sdepth", 10))
    except (TypeError, ValueError):
        return JSONResponse({"error": "Sampling depth must be an integer."}, status=400)

    if classifier not in settings.CLASSIFIERS:
        return JSONResponse({"error": f"Invalid classifier '{classifier}'"}, status=400)

    if sdepth < 10 or sdepth > 20000:
        return JSONResponse({"error": "Sampling depth must be between 10 and 20000"}, status=400)


    # -----------------------------
    # Get finished jobs
    # -----------------------------
    finished_dada2_jobs = Job.objects.filter(
        project=project,
        appId=settings.QIIME2_DADA2_APP_ID,
        status='FINISHED'
    ).order_by('-id')

    finished_proname_refine_jobs = Job.objects.filter(
        project=project,
        appId=settings.QIIME2_PRONAME_REFINE_APP_ID,
        status='FINISHED'
    ).order_by('-id')

    if not finished_dada2_jobs.exists() and not finished_proname_refine_jobs.exists():
        return JsonResponse(
            {'error': 'No completed dada2 or proname refine job found for this project.'},
            status=400
        )

    # -----------------------------
    # Select job (prefer DADA2)
    # -----------------------------
    source_job = None
    source_result = None
    source_job_detail = None

    if finished_dada2_jobs.exists():
        primary_jobs = finished_dada2_jobs.filter(primary=True)
        source_job = primary_jobs.first() if primary_jobs.exists() else finished_dada2_jobs.first()

        if source_job:
            source_result = Dada2Result.objects.filter(job=source_job).first()
            source_job_detail = Dada2JobDetail.objects.filter(job=source_job).first()

    elif finished_proname_refine_jobs.exists():
        primary_jobs = finished_proname_refine_jobs.filter(primary=True)
        source_job = primary_jobs.first() if primary_jobs.exists() else finished_proname_refine_jobs.first()

        if source_job:
            source_result = PronameRefineResult.objects.filter(job=source_job).first()
            source_job_detail = PronameRefineJobDetail.objects.filter(job=source_job).first()

    # -----------------------------
    # Validate required inputs
    # -----------------------------
    if (
        not source_result
        or not getattr(source_result, 'rooted_tree_qza', None)
        or not getattr(source_result, 'trim_table_qza', None)
        or not getattr(source_result, 'rep_seqs_qza', None)
    ):
        return JsonResponse(
            {'error': 'Rooted Tree or Trim Table or Rep Seqs QZA file not found.'},
            status=400
        )

    if not source_job_detail or not getattr(source_job_detail, 'metadata_file', None):
        return JsonResponse({'error': 'Could not get metadata file.'}, status=400)

    # -----------------------------
    # Prevent duplicate / active jobs
    # -----------------------------
    active_coremetrics_exists = Job.objects.filter(
        user=user,
        appId=settings.QIIME2_COREMETRICS_APP_ID
    ).exclude(status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED', 'FAILED_BOOT']).exists()

    if active_coremetrics_exists:
        return JsonResponse({
            'error': 'You already have an active or queued Core metrics job. Please wait for it to finish.'
        }, status=400)
    existing_detail = (
        CoreMetricsJobDetail.objects.filter(
            job__status='FINISHED',
            job__project=project,
            job__appId=settings.QIIME2_COREMETRICS_APP_ID,
            dada2_job=source_job,  # DB field unchanged
            sdepth=sdepth,
            classifier=classifier,
        )
        .select_related('job')
        .order_by('-job__id')
        .first()
    )
    if existing_detail:
        return JsonResponse({
            'error': f"A successful Core metrics job with these parameters already exists for this project: cm{existing_detail.job.id}",
        }, status=400)

    # Create Job placeholder
    job = placeholder_tapis_job(user, settings.QIIME2_COREMETRICS_APP_ID)
    job.project = project
    job.save()

    # Save job details
    CoreMetricsJobDetail.objects.create(
        job=job,
        dada2_job=source_job,  # DB schema unchanged
        sdepth=sdepth,
        classifier=classifier,
    )

    classifier_file = settings.CLASSIFIERS[classifier]

    # Submit async task
    submit_coremetrics_job_task.delay(
        job.id,
        source_result.rooted_tree_qza.name,
        source_result.trim_table_qza.name,
        source_result.rep_seqs_qza.name,
        source_job_detail.metadata_file.file.name,
        classifier_file,
        sdepth
    )

    return JsonResponse({'job_uuid': job.uuid, 'status': job.status})

def gneiss(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    project = parsed_data['project']
    data = parsed_data['data']
    user = request.user

    category = data.get("category", "")
    formula = data.get("formula", "")
    try:
        taxalevel = int(data.get("taxalevel", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "Level of taxonomy to summarize must be an integer."}, status=400)

    if taxalevel < 0 or taxalevel > 7:
        return JSONResponse({"error": "Level of taxonomy to summarize must be between 0 and 7"}, status=400)

    if len(category) > 36:
        return JSONResponse({"error": "Category should be limited to 36 characters"}, status=400)

    if len(formula) > 512:
        return JSONResponse({"error": "Too many columns selected for the grouping"}, status=400)

    # Ensure there's a finished CoreMetricsResult for this project
    finished_coremetrics_jobs = Job.objects.filter(
        project=project,
        appId=settings.QIIME2_COREMETRICS_APP_ID,
        status='FINISHED'
    ).order_by('-id')

    if not finished_coremetrics_jobs.exists():
        return JsonResponse({'error': 'No completed core metrics job found for this project.'}, status=400)

    primary_jobs = finished_coremetrics_jobs.filter(primary=True)

    if primary_jobs.exists():
        coremetrics_job = primary_jobs.first()
    else:
        coremetrics_job = finished_coremetrics_jobs.first()

    if not coremetrics_job:
        return JsonResponse({'error': 'No complete core metrics job found for this project.'}, status=400)

    coremetrics_detail = getattr(coremetrics_job, "coremetrics_detail", None)
    if not coremetrics_detail:
        return JsonResponse({'error': 'No coremetrics detail found for this job.'}, status=400)

    dada2_job = getattr(coremetrics_detail, "dada2_job", None)
    if not dada2_job:
        return JsonResponse({'error': 'No dada2 job found for this core metrics job for this project.'}, status=400)

    coremetrics_result = CoreMetricsResult.objects.filter(job=coremetrics_job).first()
    dada2_result = Dada2Result.objects.filter(job=dada2_job).first()

    if not dada2_result or not dada2_result.trim_table_qza or not coremetrics_result or not coremetrics_result.taxonomy_qza:
        return JsonResponse({'error': 'Trim Table or Taxonomy QZA file not found.'}, status=400)
    dada2_job_detail = Dada2JobDetail.objects.filter(job=dada2_job).first()
    if not dada2_job_detail or not dada2_job_detail.metadata_file:
        return JsonResponse({'error': 'Could not get dada2 metadata file.'}, status=400)

    # Prevent duplicate active/queued Gneiss jobs
    active_gneiss_exists = Job.objects.filter(
        user=user,
        appId=settings.QIIME2_GNEISS_APP_ID
    ).exclude(status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED', 'FAILED_BOOT']).exists()

    if active_gneiss_exists:
        return JsonResponse({
            'error': 'You already have an active or queued Gneiss job. Please wait for it to finish.'
        }, status=400)
    existing_detail = (
        GneissJobDetail.objects.filter(
            job__status='FINISHED',
            job__project=project,
            job__appId=settings.QIIME2_GNEISS_APP_ID,
            coremetrics_job=coremetrics_job,
            category=category,
            formula=formula,
            taxalevel=taxalevel,
        )
        .select_related('job')
        .order_by('-job__id')
        .first()
    )
    if existing_detail:
        return JsonResponse({
            'error': f"A successful Gneiss job with these parameters already exists for this project: gn{existing_detail.job.id}",
        }, status=400)

    # Create Job placeholder
    job = placeholder_tapis_job(user, settings.QIIME2_GNEISS_APP_ID)
    job.project = project
    job.save()

    # Save job details
    GneissJobDetail.objects.create(
        job=job,
        coremetrics_job=coremetrics_job,
        category=category,
        formula=formula,
        taxalevel=taxalevel,
    )

    # Submit async task
    submit_gneiss_job_task.delay(
        job.id,
        dada2_result.trim_table_qza.name,
        coremetrics_result.taxonomy_qza.name,
        dada2_job_detail.metadata_file.file.name,
        category,
        formula,
        taxalevel
    )

    return JsonResponse({'job_uuid': job.uuid, 'status': job.status})

def ancom(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    project = parsed_data['project']
    data = parsed_data['data']
    user = request.user

    category = data.get("category", "")
    formula = data.get("formula", "")
    try:
        taxalevel = int(data.get("taxalevel", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "Level of taxonomy to summarize must be an integer."}, status=400)

    if taxalevel < 0 or taxalevel > 7:
        return JSONResponse({"error": "Level of taxonomy to summarize must be between 0 and 7"}, status=400)

    if len(category) > 36:
        return JSONResponse({"error": "Category should be limited to 36 characters"}, status=400)

    if len(formula) > 512:
        return JSONResponse({"error": "Too many columns selected for the grouping"}, status=400)

    # Ensure there's a finished CoreMetricsResult for this project
    finished_coremetrics_jobs = Job.objects.filter(
        project=project,
        appId=settings.QIIME2_COREMETRICS_APP_ID,
        status='FINISHED'
    ).order_by('-id')

    if not finished_coremetrics_jobs.exists():
        return JsonResponse({'error': 'No completed core metrics job found for this project.'}, status=400)

    primary_jobs = finished_coremetrics_jobs.filter(primary=True)

    if primary_jobs.exists():
        coremetrics_job = primary_jobs.first()
    else:
        coremetrics_job = finished_coremetrics_jobs.first()

    if not coremetrics_job:
        return JsonResponse({'error': 'No complete core metrics job found for this project.'}, status=400)

    coremetrics_detail = getattr(coremetrics_job, "coremetrics_detail", None)
    if not coremetrics_detail:
        return JsonResponse({'error': 'No coremetrics detail found for this job.'}, status=400)

    dada2_job = getattr(coremetrics_detail, "dada2_job", None)
    if not dada2_job:
        return JsonResponse({'error': 'No dada2 job found for this core metrics job for this project.'}, status=400)

    coremetrics_result = CoreMetricsResult.objects.filter(job=coremetrics_job).first()
    dada2_result = Dada2Result.objects.filter(job=dada2_job).first()
    if not dada2_result:
        dada2_result = PronameRefineResult.objects.filter(job=dada2_job).first()

    if not dada2_result or not dada2_result.trim_table_qza or not coremetrics_result or not coremetrics_result.taxonomy_qza:
        return JsonResponse({'error': 'Trim Table or Taxonomy QZA file not found.'}, status=400)
    dada2_job_detail = Dada2JobDetail.objects.filter(job=dada2_job).first()
    if not dada2_job_detail:
        dada2_job_detail = PronameRefineJobDetail.objects.filter(job=dada2_job).first()
    if not dada2_job_detail or not dada2_job_detail.metadata_file:
        return JsonResponse({'error': 'Could not get dada2 or proname refine metadata file.'}, status=400)

    # Prevent duplicate active/queued Ancom jobs
    active_ancom_exists = Job.objects.filter(
        user=user,
        appId=settings.QIIME2_ANCOM_APP_ID
    ).exclude(status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED', 'FAILED_BOOT']).exists()

    if active_ancom_exists:
        return JsonResponse({
            'error': 'You already have an active or queued Ancom job. Please wait for it to finish.'
        }, status=400)
    existing_detail = (
        AncomJobDetail.objects.filter(
            job__status='FINISHED',
            job__project=project,
            job__appId=settings.QIIME2_ANCOM_APP_ID,
            coremetrics_job=coremetrics_job,
            category=category,
            formula=formula,
            taxalevel=taxalevel,
        )
        .select_related('job')
        .order_by('-job__id')
        .first()
    )
    if existing_detail:
        return JsonResponse({
            'error': f"A successful Ancom job with these parameters already exists for this project: an{existing_detail.job.id}",
        }, status=400)

    # Create Job placeholder
    job = placeholder_tapis_job(user, settings.QIIME2_ANCOM_APP_ID)
    job.project = project
    job.save()

    # Save job details
    AncomJobDetail.objects.create(
        job=job,
        coremetrics_job=coremetrics_job,
        category=category,
        formula=formula,
        taxalevel=taxalevel,
    )

    # Submit async task
    submit_ancom_job_task.delay(
        job.id,
        dada2_result.trim_table_qza.name,
        coremetrics_result.taxonomy_qza.name,
        dada2_job_detail.metadata_file.file.name,
        category,
        formula,
        taxalevel
    )

    return JsonResponse({'job_uuid': job.uuid, 'status': job.status})

def set_job_primary(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])
    data = parsed_data['data']
    job_id = data.get('job_id', 0)

    try:
        job = Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=400)

    if job.user != request.user:
        return JsonResponse({'error': 'You do not have permission for this job'}, status=400)

    if job.status != "FINISHED":
        return JsonResponse({'error': 'Job is not finished'}, status=400)

    if not job.project:
        return JsonResponse({'error': 'Job not tied to project'}, status=400)

    Job.objects.filter(
        appId=job.appId,
        project=job.project,
        status="FINISHED",
        primary=True
    ).update(primary=False)

    job.primary = True
    job.save()

    return JsonResponse({'success': f'Job {job_id} is now set as primary'})

def set_job_name(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])
    data = parsed_data['data']
    job_id = data.get('job_id', 0)
    chosen_name = str(data.get('chosen_name') or '')[:255]

    try:
        job = Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=400)

    if job.user != request.user:
        return JsonResponse({'error': 'You do not have permission for this job'}, status=400)

    if job.status != "FINISHED":
        return JsonResponse({'error': 'Job is not finished'}, status=400)

    if not job.project:
        return JsonResponse({'error': 'Job not tied to project'}, status=400)

    job.chosen_name = chosen_name
    job.save()

    return JsonResponse({'success': f'Job {job_id} is now set as primary'})

def job_info(request):
    if not request.user or not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    job_id = request.GET.get('jid')

    if not job_id:
        return JsonResponse({'error': 'Job not found'}, status=404)

    try:
        job = Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=404)

    job_details = get_user_job_status(job)
    if not job_details:
        return JsonResponse({'error': 'No data found for job'}, status=400)

    return JsonResponse({'job_details': job_details}, status=200)

def cancel_job(request):
    parsed_data = parse_user_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    job_id = data.get('jid')

    if not job_id:
        return JsonResponse({'error': 'Job not found'}, status=404)

    try:
        job = Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=404)

    if job.user != request.user and not request.user.is_superuser:
        return JsonResponse({'error': 'You do not have permission for this job'}, status=400)

    job_stopped = stop_job(job)
    if not job_stopped:
        return JsonResponse({'error': 'Could not stop job'}, status=400)

    return JsonResponse({'success': "Job stopped successfully"}, status=200)

def upload_cyverse_metabarcoding(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    username = data.get('username')
    jwt = data.get('jwt')
    selectedFiles = data.get('selectedFiles', [])

    if not selectedFiles:
        return JsonResponse({'error': 'No files selected'}, status=400)

    if not jwt:
        return JsonResponse({'error': 'JWT is required for CyVerse access'}, status=401)

    demux_job = (
        Job.objects.filter(
            project=project,
            appId=settings.QIIME2_DEMUX_APP_ID,
        )
        .order_by("-id")
        .first()
    )
    demux_run_or_success = False
    if demux_job:
        demux_run_or_success = demux_job.status not in ["CANCELLED", "FAILED", "STOPPED"]

    if demux_run_or_success:
        return JsonResponse({"error": "You cannot upload metabarcoding files while demuliplexing is running or after it succeeds"}, status=400)

    created_files = []
    validated_list = []

    FILENAME_REGEX = re.compile(
        rf'^[A-Za-z0-9\.-]+_[^_]+_L[0-9]{{3}}_R{"[12]" if project.read_type == "paired" else "1"}_001\.fastq\.gz$'
    )

    for file_uri in selectedFiles:
        # Expected format: tapis://data.cyverse.org/home/shared/.../filename.fastq.gz
        filename = file_uri.split("/")[-1]
        if not filename.endswith(".fastq.gz"):
            return JsonResponse({'error': f"Invalid file extension for {filename}"}, status=400)

        # Validate filename pattern (like upload_metabarcoding)
        if not FILENAME_REGEX.match(filename):
            return JsonResponse({'error': f"Invalid filename format: {filename}"}, status=400)

        # Skip duplicates
        if ProjectMetabarcodingFile.objects.filter(
            project=project, metabarcoding_file__name=filename
        ).exists():
            continue

        validated_list.append((file_uri, filename))

    if not validated_list:
        return JsonResponse({"error": "No new files created (all duplicates skipped)"}, status=400)

    download_results = {}
    max_workers = min(12, len(validated_list))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(download_cyverse_file, jwt, file_uri): (file_uri, filename)
            for file_uri, filename in validated_list
        }

        for future in as_completed(future_map):
            file_uri, filename = future_map[future]

            try:
                content = future.result()
            except Exception as e:
                return JsonResponse({"error": f"Error downloading {file_uri}: {str(e)}"}, status=500)

            if not content:
                return JsonResponse({"error": f"Failed to fetch {file_uri} from CyVerse"}, status=400)

            download_results[filename] = content

    for filename, content in download_results.items():
        metabarcoding_file = MetabarcodingFile.objects.create(
            user=request.user,
            name=filename,
        )

        # Save file locally using Django storage
        metabarcoding_file.file.save(
            f"{metabarcoding_file.id}.fastq.gz",
            ContentFile(content),
            save=True
        )

        # Link to project
        ProjectMetabarcodingFile.objects.create(
            project=project,
            metabarcoding_file=metabarcoding_file
        )

        created_files.append(metabarcoding_file.id)

    metadata_files = MetadataFile.objects.filter(
        project_links__project=project
    )

    metadata_files.update(validated=False)

    return JsonResponse({"created_files": created_files}, status=200)

def upload_cyverse_metadata(request):
    """
    Upload metadata files from CyVerse (tapis:// URIs), download them via JWT,
    validate as QIIME2 metadata, and create MetadataFile and ProjectMetadataFile entries.
    """
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    username = data.get('username')
    jwt = data.get('jwt')
    selectedFiles = data.get('selectedFiles', [])

    if not selectedFiles:
        return JsonResponse({'error': 'No files selected'}, status=400)

    if not jwt:
        return JsonResponse({'error': 'JWT is required for CyVerse access'}, status=401)

    created_files = []
    errors = []

    for file_uri in selectedFiles:
        # Expected format: tapis://data.cyverse.org/home/shared/.../filename.tsv
        filename = file_uri.split("/")[-1]
        if not (filename.endswith(".tsv") or filename.endswith(".txt")):
            errors.append(f"Invalid file extension for {filename}")
            continue

        # Skip duplicates
        if ProjectMetadataFile.objects.filter(
            project=project, metadata_file__name=filename
        ).exists():
            continue

        try:
            file_content = download_cyverse_file(jwt, file_uri)
            if not file_content:
                errors.append(f"Failed to fetch {file_uri} from CyVerse")
                continue

            # Validate the metadata format before saving
            file_like = io.BytesIO(file_content)
            valid, warnings = validate_qiime2_metadata_format(file_like)
            if not valid:
                errors.append(f"Invalid metadata file: {filename}")
                continue

            # Create MetadataFile and link to project
            metadata_file = MetadataFile.objects.create(
                user=request.user,
                name=filename,
            )

            metadata_file.file.save(
                f"{metadata_file.id}.tsv",
                ContentFile(file_content),
                save=True
            )

            ProjectMetadataFile.objects.create(
                project=project,
                metadata_file=metadata_file
            )

            created_files.append(metadata_file.id)

        except Exception as e:
            errors.append(f"Error processing {filename}: {str(e)}")

    if not created_files and errors:
        return JsonResponse({"error": "No valid metadata files uploaded", "details": errors}, status=400)

    if not created_files:
        return JsonResponse({"error": "No new files created (all duplicates skipped)"}, status=400)

    return JsonResponse({
        "created_files": created_files,
        "errors": errors if errors else None
    }, status=200)

def rename_metadata_file(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_id = data.get('metadata_file_id')
    new_name = data.get('new_name')

    if not file_id or not new_name:
        return JsonResponse({'error': 'metadata_file_id and new_name are required'}, status=400)

    try:
        metadata_file = MetadataFile.objects.get(id=file_id)
    except MetadataFile.DoesNotExist:
        return JsonResponse({'error': 'Metadata file not found'}, status=404)

    # Verify the file belongs to the project
    if not ProjectMetadataFile.objects.filter(project=project, metadata_file=metadata_file).exists():
        return JsonResponse({'error': 'Metadata file does not belong to this project'}, status=403)

    # Prevent duplicate names in the same project
    if ProjectMetadataFile.objects.filter(
        project=project, metadata_file__name=new_name
    ).exists():
        return JsonResponse({'error': f"A file named '{new_name}' already exists in this project."}, status=400)

    # Rename
    metadata_file.name = new_name
    metadata_file.save()

    return JsonResponse({'success': f'Metadata file renamed to {new_name}'})

def rename_metabarcoding_file(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_id = data.get('metabarcoding_file_id')
    new_name = data.get('new_name')

    demux_job = (
        Job.objects.filter(
            project=project,
            appId=settings.QIIME2_DEMUX_APP_ID,
        )
        .order_by("-id")
        .first()
    )
    demux_run_or_success = False
    if demux_job:
        demux_run_or_success = demux_job.status not in ["CANCELLED", "FAILED", "STOPPED"]

    if demux_run_or_success:
        return JsonResponse({"error": "You cannot rename metabarcoding files while demuliplexing is running or after it succeeds"}, status=400)

    if not file_id or not new_name:
        return JsonResponse({'error': 'metabarcoding_file_id and new_name are required'}, status=400)

    try:
        metabarcoding_file = MetabarcodingFile.objects.get(id=file_id)
    except MetabarcodingFile.DoesNotExist:
        return JsonResponse({'error': 'Metabarcoding file not found'}, status=404)

    # Verify it belongs to the current project
    if not ProjectMetabarcodingFile.objects.filter(project=project, metabarcoding_file=metabarcoding_file).exists():
        return JsonResponse({'error': 'Metabarcoding file does not belong to this project'}, status=403)

    # Validate filename format
    FILENAME_REGEX = re.compile(
        rf'^[A-Za-z0-9\.-]+_[^_]+_L[0-9]{{3}}_R{"[12]" if project.read_type == "paired" else "1"}_001\.fastq\.gz$'
    )
    if not FILENAME_REGEX.match(new_name):
        return JsonResponse({"error": "Invalid filename format."}, status=400)

    # Prevent duplicate names within the project
    if ProjectMetabarcodingFile.objects.filter(
        project=project, metabarcoding_file__name=new_name
    ).exists():
        return JsonResponse({'error': f"A file named '{new_name}' already exists in this project."}, status=400)

    # Rename
    metabarcoding_file.name = new_name
    metabarcoding_file.save()
    metadata_files = MetadataFile.objects.filter(
        project_links__project=project
    )

    metadata_files.update(validated=False)

    return JsonResponse({'success': f'Metabarcoding file renamed to {new_name}'})

def delete_metadata_file(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    data = parsed_data['data']
    project = parsed_data['project']
    file_id = data.get('metadata_file_id')

    if not file_id:
        return JsonResponse({'error': 'metadata_file_id is required'}, status=400)

    try:
        metadata_file = MetadataFile.objects.get(id=file_id)
    except MetadataFile.DoesNotExist:
        return JsonResponse({'error': 'Metadata file not found'}, status=404)

    used_in_active_dada2 = Dada2JobDetail.objects.filter(
        metadata_file=metadata_file
    ).exclude(
        job__status__in=["STOPPED", "CANCELLED", "FAILED"]
    ).exists()

    if used_in_active_dada2:
        return JsonResponse({'error': 'Cannot remove metadata file used in an active or completed DADA2 job'}, status=403)

    project_metadata_file = ProjectMetadataFile.objects.filter(project=project, metadata_file=metadata_file).first()

    # Verify it belongs to this project
    if not project_metadata_file:
        return JsonResponse({'error': 'Metadata file does not belong to this project'}, status=403)

    # If shared by multiple projects, remove only the link
    if ProjectMetadataFile.objects.filter(metadata_file=metadata_file).count() < 2:
        if metadata_file.file:
            os.remove(metadata_file.file.name)
        metadata_file.delete()

    project_metadata_file.delete()
    return JsonResponse({'success': 'Metadata file removed successfully'})

def validate_metadata(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    project = parsed_data['project']
    data = parsed_data['data']
    file_id = data.get('metadata_file_id')

    if not file_id:
        return JsonResponse({'error': 'metadata_file_id is required'}, status=400)

    try:
        metadata_file = MetadataFile.objects.get(id=file_id)
    except MetadataFile.DoesNotExist:
        return JsonResponse({'error': 'Metadata file not found'}, status=404)

    valid, errors = validate_qiime2_metadata_format(metadata_file.file)
    if not valid:
        return JsonResponse({"error": "Invalid metadata file", "warnings": errors}, status=400)

    if project.sequencing_type == "nanopore":
        # Get all ProjectNanoporeSequences for the project and retrieve their related NanoporeSequences
        nanopore_sequences = (
            ProjectNanoporeSequence.objects
            .filter(project=project)
            .exclude(nanopore_sequence__source='proname_import')
            .select_related('nanopore_sequence')
            .order_by('nanopore_sequence__name')
        )
        if not nanopore_sequences.exists():
            return JsonResponse({'error': 'No nanopore files found for this project.'}, status=400)
        try:
            sample_ids = extract_qiime2_metadata_sample_ids(metadata_file)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        file_sample_names = {
            pns.nanopore_sequence.name
            for pns in nanopore_sequences
        }

        metadata_sample_ids = set(sample_ids)

        missing_files = [
            f"{sample_id}: no FASTQ files found"
            for sample_id in metadata_sample_ids
            if sample_id not in file_sample_names
        ]

        extraneous_files = [
            f"File {sample_id} not found in metadata"
            for sample_id in file_sample_names
            if sample_id not in metadata_sample_ids
        ]

        expected_count = len(metadata_sample_ids)
        actual_count = len(file_sample_names)

        if missing_files or extraneous_files or actual_count != expected_count:
            return JsonResponse({
                "error": "Metadata and metabarcoding file mismatch detected.",
                "missing_files": missing_files or None,
                "extraneous_files": extraneous_files or None,
                "expected_file_count": expected_count,
                "actual_file_count": actual_count,
            }, status=400)
        metadata_file.validated = True
        metadata_file.save()
        return JsonResponse({"success": "Metadata validation passed."})

    metabarcoding_files = ProjectMetabarcodingFile.objects.filter(project=project).select_related('metabarcoding_file')
    if not metabarcoding_files.exists():
        return JsonResponse({'error': 'No metabarcoding files found for this project.'}, status=400)

    try:
        sample_ids = extract_qiime2_metadata_sample_ids(metadata_file)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    FILENAME_REGEX = re.compile(
        rf'^[A-Za-z0-9\.-]+_[^_]+_L[0-9]{{3}}_R{"[12]" if project.read_type == "paired" else "1"}_001\.fastq\.gz$'
    )
    invalid_filenames = []
    file_sample_map = {}

    for pmf in metabarcoding_files:
        fname = pmf.metabarcoding_file.name.split('/')[-1]

        if not FILENAME_REGEX.match(fname):
            invalid_filenames.append(fname)
            continue

        sample_id = fname.split('_')[0]
        file_sample_map.setdefault(sample_id, []).append(fname)

    if invalid_filenames:
        return JsonResponse({
            "error": "Invalid metabarcoding filenames detected.",
            "invalid_filenames": invalid_filenames,
        }, status=400)

    missing_files = []
    extraneous_files = []

    for sample_id in sample_ids:
        files_for_sample = file_sample_map.get(sample_id, [])
        if not files_for_sample:
            missing_files.append(f"{sample_id}: no FASTQ files found")
            continue

        if project.read_type == "paired":
            has_r1 = any("_R1_" in f for f in files_for_sample)
            has_r2 = any("_R2_" in f for f in files_for_sample)
            if not (has_r1 and has_r2):
                missing = []
                if not has_r1:
                    missing.append("R1")
                if not has_r2:
                    missing.append("R2")
                missing_files.append(f"{sample_id}: missing {', '.join(missing)} file(s)")
        else:
            if not any("_R1_" in f for f in files_for_sample):
                missing_files.append(f"{sample_id}: missing R1 file")

    for sample_id in file_sample_map.keys():
        if sample_id not in sample_ids:
            files_str = ", ".join(file_sample_map[sample_id])
            if len(files_for_sample) > 1:
                extraneous_files.append(f"Files {files_str} not found in metadata")
            else:
                extraneous_files.append(f"File {files_str} not found in metadata")

    expected_count = len(sample_ids) * (2 if project.read_type == "paired" else 1)
    actual_count = sum(len(v) for v in file_sample_map.values())

    if missing_files or extraneous_files or actual_count != expected_count:
        return JsonResponse({
            "error": "Metadata and metabarcoding file mismatch detected.",
            "missing_files": missing_files or None,
            "extraneous_files": extraneous_files or None,
            "expected_file_count": expected_count,
            "actual_file_count": actual_count,
        }, status=400)

    metadata_file.validated = True
    metadata_file.save()
    return JsonResponse({"success": "Metadata validation passed."})

def create_metadata_from_scratch(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    user = request.user
    project = parsed_data['project']

    # Collect unique sample names
    sample_ids = set()

    if project.sequencing_type == "nanopore":
        nanopore_sequences = ProjectNanoporeSequence.objects.filter(project=project) \
            .select_related('nanopore_sequence') \
            .order_by('nanopore_sequence__name')
        if not nanopore_sequences.exists():
            return JsonResponse({'error': 'No nanopore files found for this project.'}, status=400)
        sample_ids = {
            pns.nanopore_sequence.name
            for pns in nanopore_sequences
        }
    else:
        metabarcoding_files = (
            ProjectMetabarcodingFile.objects
            .filter(project=project)
            .select_related('metabarcoding_file')
        )

        if not metabarcoding_files.exists():
            return JsonResponse({'error': 'No metabarcoding files found for this project.'}, status=400)

        FILENAME_REGEX = re.compile(
            rf'^([A-Za-z0-9\.-]+)_[^_]+_L[0-9]{{3}}_R{"[12]" if project.read_type == "paired" else "1"}_001\.fastq\.gz$'
        )

        for pmf in metabarcoding_files:
            fname = pmf.metabarcoding_file.name.split('/')[-1]
            match = FILENAME_REGEX.match(fname)
            if match:
                # Extract the part before the first underscore
                prefix = match.group(1)
                sample_ids.add(prefix)

    # Choose a unique filename for the metadata
    existing_names = ProjectMetadataFile.objects.filter(project=project).values_list('metadata_file__name', flat=True)
    base_filename = "metadata.tsv"
    filename = base_filename
    counter = 1
    while filename in existing_names:
        filename = f"metadata_{counter}.tsv"
        counter += 1

    # Build metadata content
    output = io.StringIO()
    output.write("#SampleID\tDescription\n")
    for sid in sorted(sample_ids):
        output.write(f"{sid}\t{sid}\n")

    # Create and save metadata file
    metadata_file = MetadataFile.objects.create(
        user=user,
        validated = True,
        name=filename,
    )
    metadata_file.file.save(
        f"{metadata_file.id}.tsv",
        ContentFile(output.getvalue()),
        save=True
    )

    # Link to project
    ProjectMetadataFile.objects.create(
        project=project,
        metadata_file=metadata_file
    )

    return JsonResponse({"id": metadata_file.id})

def ub_classifiers(request):
    classifiers = settings.CLASSIFIERS.keys()
    return JsonResponse({
        'classifiers': list(classifiers)
    })

def medaka_references(request):
    references = settings.MEDAKA_REFERENCES.keys()
    return JsonResponse({
        'references': list(references) + ["custom"]
    })

def proname_import(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    project = parsed_data['project']
    data = parsed_data['data']
    kit = data.get("kit", "")
    forwardPrimer = data.get("forwardPrimer", "")
    reversePrimer = data.get("reversePrimer", "")
    trimPrimers = data.get("trimPrimers", False)
    trimAdapters = data.get("trimAdapters", False)
    hasDuplex = data.get("hasDuplex", False)

    if not kit or len(kit) > 64:
        return JsonResponse({'error': 'Kit name is required and must be no more than 64 characters.'}, status=400)

    if not all(isinstance(x, bool) for x in [trimPrimers, trimAdapters, hasDuplex]):
        return JsonResponse({'error': 'Invalid boolean fields.'}, status=400)

    if trimPrimers:
        if not isinstance(forwardPrimer, str) or not isinstance(reversePrimer, str):
            return JsonResponse({'error': 'Primers must be strings.'}, status=400)
        PRIMER_REGEX = re.compile(r'^[ACGTRYSWKMBDHVN]+$', re.IGNORECASE)
        invalid = (
            not forwardPrimer
            or not reversePrimer
            or not (15 <= len(forwardPrimer) <= 50)
            or not (15 <= len(reversePrimer) <= 50)
            or not PRIMER_REGEX.fullmatch(forwardPrimer)
            or not PRIMER_REGEX.fullmatch(reversePrimer)
        )
        if invalid:
            return JsonResponse({'error': 'Invalid primers.'}, status=400)

    user = request.user

    active_proname_import_job_exists = Job.objects.filter(
        user=user,
        appId=settings.QIIME2_PRONAME_IMPORT_APP_ID,
    ).exclude(status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED', 'FAILED_BOOT']).exists()

    if active_proname_import_job_exists:
        return JsonResponse({
            'error': 'You already have an active or queued proname import job. Please wait for it to finish before starting another.'
        }, status=400)

    nanopore_sequences = (
        ProjectNanoporeSequence.objects
        .filter(project=project)
        .exclude(nanopore_sequence__source='proname_import')
        .select_related('nanopore_sequence')
    )
    if not nanopore_sequences.exists():
        return JsonResponse({'error': 'No nanopore sequences found for this project.'}, status=400)
    job = placeholder_tapis_job(user, settings.QIIME2_PRONAME_IMPORT_APP_ID)
    job.project = project
    job.save()
    PronameImportJobDetail.objects.create(
        job=job,
        forward_primer=forwardPrimer,
        reverse_primer=reversePrimer,
        kit=kit,
        has_duplex=hasDuplex,
        trim_adapters=trimAdapters,
        trim_primers=trimPrimers
    )
    submit_proname_import_job_task.delay(job.id, forwardPrimer, reversePrimer, kit, hasDuplex, trimAdapters, trimPrimers)
    return JsonResponse({'job_uuid': job.uuid, 'status': job.status})

def proname_filter(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    project = parsed_data['project']
    data = parsed_data['data']
    dataType = data.get("dataType", "simplex")
    try:
        filtMaxLength = int(data.get("filtmaxlength", 5000))
        filtMinLength = int(data.get("filtminlength", 1))
        filtMinQual = int(data.get("filtminqual", 15))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Filter parameters must be integers.'}, status=400)

    if not (1 <= filtMaxLength <= 10000):
        return JsonResponse({'error': 'filtMaxLength must be between 1 and 10000.'}, status=400)

    if not (1 <= filtMinLength <= 5000):
        return JsonResponse({'error': 'filtMaxLength must be between 1 and 5000.'}, status=400)

    if not (0 <= filtMinQual <= 60):
        return JsonResponse({'error': 'filtMinQual must be between 0 and 60.'}, status=400)

    if filtMinLength > filtMaxLength:
        return JsonResponse({'error': 'filtMinLength cannot be greater than filtMaxLength.'}, status=400)

    if dataType not in ["simplex", "duplex", "both"]:
        return JsonResponse({'error': 'Invalid data type.'}, status=400)

    user = request.user

    active_proname_filter_job_exists = Job.objects.filter(
        user=user,
        appId=settings.QIIME2_PRONAME_FILTER_APP_ID,
    ).exclude(status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED', 'FAILED_BOOT']).exists()

    if active_proname_filter_job_exists:
        return JsonResponse({
            'error': 'You already have an active or queued proname filter job. Please wait for it to finish before starting another.'
        }, status=400)

    nanopore_sequences = (
        ProjectNanoporeSequence.objects
        .filter(project=project, nanopore_sequence__source='proname_import')
        .select_related('nanopore_sequence')
    )
    if not nanopore_sequences.exists():
        return JsonResponse({'error': 'No proname import metabarcoding output files found for this project.'}, status=400)
    proname_import_result = None
    finished_proname_import_jobs = Job.objects.filter(
        project=project,
        appId=settings.QIIME2_PRONAME_IMPORT_APP_ID,
        status='FINISHED'
    ).order_by('-id')
    if not finished_proname_import_jobs.exists():
        return JsonResponse({'error': 'No completed proname import job found for this project.'}, status=400)

    primary_jobs = finished_proname_import_jobs.filter(primary=True)
    proname_import_job = primary_jobs.first() if primary_jobs.exists() else finished_proname_import_jobs.first()
    if proname_import_job:
        proname_import_result = PronameImportResult.objects.filter(job=proname_import_job).first()
    required_read_fields = ['simplex_reads', 'duplex_reads', 'dual_reads']
    if not proname_import_result or not any(getattr(proname_import_result, f, None) for f in required_read_fields):
        return JsonResponse({'error': 'Simplex, duplex, and dual reads not found.'}, status=400)

    simplex_reads = None
    duplex_reads = None
    dual_reads = None
    if proname_import_result and proname_import_result.simplex_reads:
        simplex_reads = proname_import_result.simplex_reads.name
    if proname_import_result and proname_import_result.duplex_reads:
        duplex_reads = proname_import_result.duplex_reads.name
    if proname_import_result and proname_import_result.dual_reads:
        dual_reads = proname_import_result.dual_reads.name

    job = placeholder_tapis_job(user, settings.QIIME2_PRONAME_FILTER_APP_ID)
    job.project = project
    job.save()
    PronameFilterJobDetail.objects.create(
        job=job,
        data_type=dataType,
        filt_min_length=filtMinLength,
        filt_max_length=filtMaxLength,
        filt_min_qual=filtMinQual
    )
    submit_proname_filter_job_task.delay(job.id, dataType, filtMinLength, filtMaxLength, filtMinQual, simplex_reads, duplex_reads, dual_reads)
    return JsonResponse({'job_uuid': job.uuid, 'status': job.status})

def proname_refine(request):
    parsed_data = parse_user_project_data(request)
    if 'error' in parsed_data:
        return JsonResponse({'error': parsed_data['error']}, status=parsed_data['status'])

    project = parsed_data['project']
    data = parsed_data['data']
    chimeraDb = data.get("chimeradb")
    clusterMethod = data.get("clusteringmethod")
    medakaModel = data.get("medakamodel")
    try:
        minReadsPerCluster = int(data.get("minreadspercluster", 2))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Minimum reads per cluster must be an integer.'}, status=400)
    if minReadsPerCluster < 1:
        return JsonResponse({'error': 'minReadsPerCluster must be greater than 0.'}, status=400)
    file_id = data.get('metadata_file_id')
    nanopore_sequences = (
        ProjectNanoporeSequence.objects
        .filter(project=project)
        .exclude(nanopore_sequence__source='proname_import')
        .select_related('nanopore_sequence')
    )
    first_project_seq = nanopore_sequences.first()

    if not first_project_seq:
        return JsonResponse({'error': 'No nanopore sequences found for this project.'}, status=400)
    fastq_file_path = first_project_seq.nanopore_sequence.file.path
    medakaModel = find_best_medaka_model(fastq_file_path)
    try:
        raw_cluster_id = data.get("clusterid", 0.99)
        clusterId = round(float(raw_cluster_id), 2)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'clusterId must be a numeric decimal.'}, status=400)

    if not file_id:
        return JsonResponse({'error': 'metadata_file_id is required'}, status=400)

    try:
        metadata_file = MetadataFile.objects.get(id=file_id)
    except MetadataFile.DoesNotExist:
        return JsonResponse({'error': 'Metadata file not found'}, status=404)

    if not metadata_file.validated:
        return JsonResponse({'error': 'Metadata file not validated'}, status=400)

    valid_chimera_dbs = [db[0] for db in PronameRefineJobDetail.CHIMERA_DBS]
    valid_cluster_methods = [method[0] for method in PronameRefineJobDetail.CLUSTERING_METHODS]

    if chimeraDb not in valid_chimera_dbs:
        return JsonResponse({'error': f'Invalid Chimera DB. Must be one of: {", ".join(valid_chimera_dbs)}'}, status=400)

    if clusterMethod not in valid_cluster_methods:
        return JsonResponse({'error': f'Invalid Clustering Method. Must be one of: {", ".join(valid_cluster_methods)}'}, status=400)

    if medakaModel not in PronameRefineJobDetail.MEDAKA_MODEL_LIST:
        return JsonResponse({'error': 'Invalid Medaka Model selected.'}, status=400)

    if not (0.0 <= clusterId <= 1.0):
        return JsonResponse({'error': 'clusterId must be between 0.0 and 1.0.'}, status=400)

    user = request.user

    active_proname_refine_job_exists = Job.objects.filter(
        user=user,
        appId=settings.QIIME2_PRONAME_REFINE_APP_ID,
    ).exclude(status__in=['FINISHED', 'FAILED', 'STOPPED', 'CANCELLED', 'FAILED_BOOT']).exists()

    if active_proname_refine_job_exists:
        return JsonResponse({
            'error': 'You already have an active or queued proname refine job. Please wait for it to finish before starting another.'
        }, status=400)

    nanopore_sequences = (
        ProjectNanoporeSequence.objects
        .filter(project=project, nanopore_sequence__source='proname_import')
        .select_related('nanopore_sequence')
    )
    if not nanopore_sequences.exists():
        return JsonResponse({'error': 'No proname import metabarcoding output files found for this project.'}, status=400)
    proname_filter_result = None
    finished_proname_filter_jobs = Job.objects.filter(
        project=project,
        appId=settings.QIIME2_PRONAME_FILTER_APP_ID,
        status='FINISHED'
    ).order_by('-id')
    if not finished_proname_filter_jobs.exists():
        return JsonResponse({'error': 'No completed proname filter job found for this project.'}, status=400)

    primary_jobs = finished_proname_filter_jobs.filter(primary=True)
    proname_filter_job = primary_jobs.first() if primary_jobs.exists() else finished_proname_filter_jobs.first()
    if proname_filter_job:
        proname_filter_result = PronameFilterResult.objects.filter(job=proname_filter_job).first()
    required_read_fields = ['simplex_reads', 'duplex_reads', 'dual_reads']
    if not proname_filter_result or not any(getattr(proname_filter_result, f, None) for f in required_read_fields):
        return JsonResponse({'error': 'Simplex, duplex, and dual reads not found.'}, status=400)

    simplex_reads = None
    duplex_reads = None
    dual_reads = None
    if proname_filter_result and proname_filter_result.simplex_reads:
        simplex_reads = proname_filter_result.simplex_reads.name
    if proname_filter_result and proname_filter_result.duplex_reads:
        duplex_reads = proname_filter_result.duplex_reads.name
    if proname_filter_result and proname_filter_result.dual_reads:
        dual_reads = proname_filter_result.dual_reads.name

    job = placeholder_tapis_job(user, settings.QIIME2_PRONAME_REFINE_APP_ID)
    job.project = project
    job.save()
    PronameRefineJobDetail.objects.create(
        job=job,
        chimera_db=chimeraDb,
        cluster_id=clusterId,
        min_reads_per_cluster=minReadsPerCluster,
        clustering_method=clusterMethod,
        medaka_model=medakaModel,
        metadata_file=metadata_file
    )
    submit_proname_refine_job_task.delay(job.id, clusterId, minReadsPerCluster, clusterMethod, medakaModel, chimeraDb, simplex_reads, duplex_reads, dual_reads)
    return JsonResponse({'job_uuid': job.uuid, 'status': job.status})
