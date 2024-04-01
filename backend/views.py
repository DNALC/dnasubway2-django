from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.http import JsonResponse
from django.middleware.csrf import get_token
import json
import re
#from django.shortcuts import render
from .models import UserProfile, Ethnicity

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
                    return JsonResponse({'session_token': session_token}, status=200)
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
