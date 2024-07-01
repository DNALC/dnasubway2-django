from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
import json
import os
import random
import re
import requests
import string
#from django.shortcuts import render
from .models import UserProfile, Ethnicity, PasswordResetToken, Project, DataFile, ProjectDataFile
from .utils import parse_reads, cleanSequenceName

def register(request):
    if request.method == 'POST':
        try:
            # Parse JSON data from request body
            data = json.loads(request.body)

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

            if User.objects.filter(username=username).exists():
                return JsonResponse({'error': 'User with this username already exists'}, status=400)

            # Check if zip_code is no longer than 12 characters
            if len(zip_code) > 12:
                return JsonResponse({'error': 'Postal code must be no longer than 12 characters'}, status=400)

            # Check if country is no longer than 2 characters
            if len(country) > 2:
                return JsonResponse({'error': 'Country must be no longer than 2 characters'}, status=400)

            # Check if username is no longer than 150 characters
            if len(username) > 150:
                return JsonResponse({'error': 'Username must be no longer than 150 characters'}, status=400)

            # Check if first_name is no longer than 150 characters
            if len(first_name) > 150:
                return JsonResponse({'error': 'First name must be no longer than 150 characters'}, status=400)

            # Check if last_name is no longer than 150 characters
            if len(last_name) > 150:
                return JsonResponse({'error': 'Last name must be no longer than 150 characters'}, status=400)

            # Check if email is no longer than 254 characters
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

            return JsonResponse({'success': 'User registered successfully', 'redirect': '/login'}, status=201)

        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)

    return JsonResponse({'error': 'Invalid request method'}, status=405)

def login_view(request):
    if request.method == 'POST':
        try:
            # Parse JSON data from request body
            data = json.loads(request.body)

            # Extract required fields from JSON data
            username = data.get('username')
            password = data.get('password')
            if username and password:
                user = authenticate(request, username=username, password=password)
                if user is not None:
                    login(request, user)
                    # Generate and return session token
                    session_token = request.session.session_key
                    return JsonResponse({'session_token': session_token, 'redirect': '/profile'}, status=200)
                else:
                    return JsonResponse({'error': 'Invalid username or password'}, status=401)
            else:
                return JsonResponse({'error': 'Username and password are both required'}, status=400)

        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)

    else:
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)

def get_csrf_token(request):
    csrf_token = get_token(request)
    return JsonResponse({'csrfToken': csrf_token})

@csrf_exempt
def request_password_reset(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            username = data.get('username')
            email = data.get('email')

            try:
                user = User.objects.get(username=username, email=email)
            except User.DoesNotExist:
                return JsonResponse({'error': 'User not found'}, status=404)

            # Create password reset token
            reset_token = PasswordResetToken.create_token(user)

            # Send email with reset token
            if not send_password_reset_email(email, reset_token.token):
                return JsonResponse({'error': 'Failed to send password reset email'}, status=500)

            return JsonResponse({'success': 'Password reset email sent'}, status=200)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    else:
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)

def send_password_reset_email(to_email, token):
    try:
        # Mailgun API endpoint
        url = f"https://api.mailgun.net/v3/{getattr(settings, 'MAILGUN_DOMAIN')}/messages"

        # Mailgun API credentials
        api_key = getattr(settings, 'MAILGUN_API_KEY')
        from_email = getattr(settings, 'MAILGUN_FROM_EMAIL')

        # Email data
        subject = 'Password Reset'
        text = f"Use this link to reset your password: {getattr(settings, 'REACT_URL')}/reset?code={token}"
        body = f"Use this link to reset your password: <a href=\"{getattr(settings, 'REACT_URL')}/reset?code={token}\">{getattr(settings, 'REACT_URL')}/reset?code={token}</a>"
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

@csrf_exempt
def confirm_password_reset(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
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
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    else:
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)

# Mapping between ethnicity fields and their corresponding strings
ETHNICITY_MAPPING = {
    'white': 'wh',
    'native': 'na',
    'asian': 'as',
    'black': 'aa',
    'hispanic': 'hs',
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
            }

            # Check if UserProfile exists for the user
            if hasattr(user, 'userprofile'):
                # Include fields from UserProfile model
                user_profile = user.userprofile
                user_fields.update({
                    'country': user_profile.country,
                    'zip': user_profile.postal_code,
                    'gender': user_profile.gender,
                    'occupation': user_profile.occupation,
                    'source': user_profile.source,
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
                user.email = data.get('email', user.email)
                user.save()

                # Update UserProfile fields if available
                if hasattr(user, 'userprofile'):
                    user_profile = user.userprofile
                    user_profile.country = data.get('country', user_profile.country)
                    user_profile.postal_code = data.get('zip', user_profile.postal_code)
                    user_profile.gender = data.get('gender', user_profile.gender)
                    user_profile.occupation = data.get('occupation', user_profile.occupation)
                    user_profile.source = data.get('source', user_profile.source)
                    user_profile.save()

                    # Update ethnicity record associated with the UserProfile
                    ethnicity_list = data.get('ethnicity', [])
                    ethnicity_record, _ = Ethnicity.objects.get_or_create(userprofile=user_profile)
                    ethnicity_record.white = 'wh' in ethnicity_list
                    ethnicity_record.native = 'na' in ethnicity_list
                    ethnicity_record.asian = 'as' in ethnicity_list
                    ethnicity_record.black = 'aa' in ethnicity_list
                    ethnicity_record.hispanic = 'hs' in ethnicity_list
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
        return JsonResponse({'success': 'Logged out successfully'})
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
                return JsonResponse({'username': username, 'success': 'Guest user created successfully', 'redirect': '/profile'})
        # If all retries fail, return an error
        return JsonResponse({'error': 'Failed to create guest user'}, status=500)

    return JsonResponse({'error': 'Method not allowed'}, status=405)

def generate_guest_username():
    # Generate a random username starting with 'guest_' followed by random characters
    random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f'guest_{random_chars}'

def create_project(request):
    # Retrieve the user associated with the session ID
    user = request.user

    if user.is_authenticated:
        if request.method == 'POST':
            # Parse JSON data from the request
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'error': 'Invalid JSON data'}, status=400)

            # Extract fields from the JSON data
            title = data.get('title', '').strip()
            description = data.get('description', '').strip()
            project_type = data.get('project_type', 'PHY')
            sequencing_type = data.get('sequencing_type', 'sanger')

            # Check if project_type is valid
            if project_type not in dict(Project.PROJECT_TYPES).keys():
                return JsonResponse({'error': 'Invalid project_type'}, status=400)

            # Check if sequencing_type is valid
            if sequencing_type not in dict(Project.SEQUENCING_TYPES).keys():
                return JsonResponse({'error': 'Invalid sequencing_type'}, status=400)

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
                              project_type=project_type, sequencing_type=sequencing_type)
            new_project.save()

            return JsonResponse({'success': 'Project created successfully', 'redirect': '/view_project?pid=' + str(new_project.id)})

        return JsonResponse({'error': 'Method not allowed'}, status=405)
    else:
        return JsonResponse({'error': 'User not authenticated'}, status=401)

def user_projects(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    # Retrieve projects for the current authenticated user
    projects = Project.objects.filter(user=request.user, deleted=False).order_by('-id')

    # Paginate the projects with 10 projects per page
    paginator = Paginator(projects, 10)
    page_number = request.GET.get('page')
    projects_page = paginator.get_page(page_number)

    # Serialize project data
    serialized_projects = []
    for project in projects_page:
        serialized_project = {
            'id': project.id,
            'title': project.title,
            'description': project.description,
            'sequencing_type': project.sequencing_type,
            'project_type': project.project_type,
            'created_date': project.created.strftime('%Y-%m-%d'),  # Format date as YYYY-MM-DD
            'public': project.public
        }
        serialized_projects.append(serialized_project)

    return JsonResponse({'success': 'Projects retrieved', 'projects': serialized_projects, 'total_pages': paginator.num_pages}, safe=False)

def project_info(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User is not authenticated'}, status=401)

    pid = request.GET.get('pid')
    # Retrieve project info for the current authenticated user
    project = Project.objects.filter(id=pid, user=request.user)
    if not project:
        return JsonResponse({'error': 'Project not found'}, status=404)
    else:
        project = project.first()
    # Retrieve project data files for the current authenticated user
    project_data_files = ProjectDataFile.objects.filter(project=project).order_by('id')
    serialized_sequences = []
    for project_data_file in project_data_files:
        serialized_sequence = {
            'file_id': project_data_file.data_file.id,
            'display_name': project_data_file.data_file.name,
            'trace_file_path': project_data_file.data_file.associated_abi.name if project_data_file.data_file.associated_abi else None,
            'sequence': project_data_file.data_file.reads,
        }
        serialized_sequences.append(serialized_sequence)
    project_data = {
        'id': project.id,
        'title': project.title,
        'description': project.description,
        'sequencing_type': project.sequencing_type,
        'project_type': project.project_type,
        'created_date': project.created.strftime('%Y-%m-%d'),  # Format date as YYYY-MM-DD
        'username': project.user.username,
        'sequences': serialized_sequences,
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
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    # Check if the user is authenticated
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=401)

    # Parse JSON data from the request
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)

    pid = data.get('pid', None)

    # Get the project associated with the given pid
    project = Project.objects.get(id=pid)

    if not project:
        return JsonResponse({'error': 'Project not found.'}, status=404)

    # Ensure the current user owns the project
    if project.user != request.user:
        return JsonResponse({'error': 'You do not have permission to access this project.'}, status=403)
    
    project.deleted = True
    project.save()
    return JsonResponse({'success': 'Project deleted successfully'}, status=200)

def download_and_create_datafiles(request):

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    # Check if the user is authenticated
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=401)

    # Parse JSON data from the request
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)

    pid = data.get('pid', None)
    # Get the 'o' and 'f' parameters from the request
    o = data.get('o', None)
    f = data.get('f', None)

    # Get the project associated with the given pid
    project = Project.objects.get(id=pid)

    if not project:
        return JsonResponse({'error': 'Project not found.'}, status=404)

    # Ensure the current user owns the project
    if project.user != request.user:
        return JsonResponse({'error': 'You do not have permission to access this project.'}, status=403)

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

        message, sequence, trace_exists, record = parse_reads(file_url)
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
        )
        response = requests.get(file_url)

        abi_content = ContentFile(response.content)
        abi_file_name = f"{data_file.id}.abi"
        abi_file_path = default_storage.save(f"abi_files/{abi_file_name}", abi_content)
        data_file.associated_abi.name = abi_file_path
        data_file.save()

        fasta_content = ContentFile(f">{data_file.name}\n{data_file.reads}\n")
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

def process_abi_file(request):
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
    message, sequence, trace_exists, record = parse_reads(os.path.join("http://" + request.get_host(), abi_file.name))
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
