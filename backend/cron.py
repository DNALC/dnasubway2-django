from django.conf import settings
import os
from .models import BasecallingJob, PodFile, NanoporeSequence, UserNanoporeSequence
from .utils import (
    ensure_instance_ready,
    get_service_token,
    generate_user_token,
    connect_to_tapis,
    list_all_files,
    get_file_content,
)
from django.core.files.base import ContentFile

def get_job_status(tapis, job_uuid):
    try:
        job_details = tapis.jobs.getJob(jobUuid=job_uuid)
        return job_details
    except Exception as e:
        print("Error retrieving job details:", e)
        return None

def check_job(tapis, job_uuid, job_obj):
    print("Checking job " + job_uuid)
    status = get_job_status(tapis, job_uuid)
    if status:
        current_status = status.get("status")
        print("Current status: " + current_status)
        user = job_obj.job.user

        # If job is in a terminal state, remove PodFiles
        if current_status in ["FAILED", "STOPPED", "FINISHED", "CANCELLED"]:
            # Delete PodFiles (both DB record + actual file)
            for pod_file in PodFile.objects.filter(user=user):
                pod_file.file.delete(save=False)  # remove file from disk
                pod_file.delete()

        # Update status in DB
        job_obj.job.status = current_status
        job_obj.job.save(update_fields=["status"])
        print("Job status:", current_status)

        # If FINISHED, save .fastq.gz files as NanoporeSequence
        if current_status == "FINISHED":
            all_files = list_all_files(tapis, job_uuid)

            for file_path in all_files:
                if not file_path.endswith(".fastq.gz"):
                    continue

                # Retrieve file content from Tapis
                file_content = get_file_content(tapis, job_uuid, file_path)

                # Derive sequence filename and display name
                seq_filename = f"{job_obj.id}.fastq.gz"  # sequence record ID placeholder (we’ll adjust)
                last_part = os.path.basename(file_path)  # e.g. "96c4c27b..._unclassified.fastq.gz"
                display_name = f"{job_obj.output_name}-{last_part.split('_')[-1]}"  # unclassified.fastq.gz

                # Save NanoporeSequence
                nanopore_sequence = NanoporeSequence.objects.create(
                    name=display_name,
                )
                # Now that we have the ID, rename file accordingly
                nanopore_sequence.file.save(
                    f"{nanopore_sequence.id}.fastq.gz",
                    ContentFile(file_content)
                )

                # Link sequence to user
                UserNanoporeSequence.objects.create(
                    user=user,
                    nanopore_sequence=nanopore_sequence
                )

def poll_active_jobs():
    # 1. Ensure instance is ready
    active, err = ensure_instance_ready(settings.INSTANCE_NAME)
    if err:
        print(err)
        return

    # 2. Get service token
    get_service_token()
    print("Got service token") 

    # 3. Query active jobs
    jobs = (
        BasecallingJob.objects
        .select_related('job', 'job__user')
        .exclude(job__status__in=['FINISHED', 'CANCELLED', 'FAILED'])
    )
    print("Got jobs") 
    print(jobs) 

    # 4. Group by user
    usernames = set(j.job.user.username for j in jobs)
    print("Got users") 
    print(usernames) 

    for username in usernames:
        print("Checking user " + username)
        user_jobs = [j for j in jobs if j.job.user.username == username]
        
        user_token = generate_user_token(username)
        print("User token: " + user_token) 
        tapis = connect_to_tapis(username, user_token)

        for job in user_jobs:
            check_job(tapis, job.job.uuid, job)
