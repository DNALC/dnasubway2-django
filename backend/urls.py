from django.urls import path
from .views import register, get_csrf_token, login_view, request_password_reset, confirm_password_reset, get_user_fields, logout_view, create_guest_user, create_project, user_projects, public_projects, download_and_create_datafiles, process_abi_file, project_info, delete_project

urlpatterns = [
    path('csrf_token', get_csrf_token, name='csrf_token'),
    path('register', register, name='register'),
    path('login', login_view, name='login'),
    path('logout', logout_view, name='logout'),
    path('forgot', request_password_reset, name='forgot'),
    path('reset', confirm_password_reset, name='reset'),
    path('profile', get_user_fields, name='profile'),
    path('guest', create_guest_user, name='guest'),
    path('project', create_project, name='project'),
    path('user_projects', user_projects, name='user_projects'),
    path('public_projects', public_projects, name='public_projects'),
    path('dnalc_import', download_and_create_datafiles, name='dnalc_import'),
    path('abi_data', process_abi_file, name='abi_data'),
    path('project_info', project_info, name='project_info'),
    path('remove_project', delete_project, name='remove_project'),
]
