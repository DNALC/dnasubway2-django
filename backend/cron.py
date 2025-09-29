from django.conf import settings
import os
import re
from .models import BasecallingJob, PodFile, NanoporeSequence, UserNanoporeSequence, JobPodFile, DataFolder, NanoporeSequenceFolder, DemuxResult, Job
from .utils import (
    shelve_instance,
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

def check_metabarcoding_job(tapis, job_uuid, job_obj, admin_tapis):
    print("Checking metabarcoding job " + job_uuid)
    status = get_job_status(tapis, job_uuid)
    if not status:
        return
    current_status = status.get("status")
    print("Current status: " + current_status)
    user = job_obj.user

    if current_status == "FINISHED":
        job_obj.status = "FINISHING"
        job_obj.save(update_fields=["status"])
        # List files from Tapis job archive
        all_files = list_all_files(tapis, job_uuid)

        qza_file = None
        qzv_file = None

        for file_path in all_files:
            if file_path.endswith("imported-demux.qza"):
                qza_file = file_path
            elif file_path.endswith("imported-demux.qzv"):
                qzv_file = file_path
        if qza_file or qzv_file:
            demux_result, _ = DemuxResult.objects.get_or_create(job=job_obj)

            if qza_file:
                print("GET " + qza_file)
                file_content = tapis.files.getContents(systemId="js2_dnasubway2", path=f"scratch/{user.username}/job-{job_uuid}/imported-demux.qza")
                demux_result.demux_qza.save(
                    f"{demux_result.id}-imported-demux.qza",
                    ContentFile(file_content),
                    save=False,
                )
            if qzv_file:
                print("GET " + qzv_file)
                file_content = get_file_content(tapis, job_uuid, qzv_file)
                demux_result.demux_summary_qzv.save(
                    f"{demux_result.id}-imported-demux.qzv",
                    ContentFile(file_content),
                    save=False,
                )
            demux_result.save()

            # cleanup remote job dir
            try:
                admin_tapis.files.delete(
                    systemId="js2_dnasubway2",
                    path=f"scratch/{user.username}/job-{job_uuid}/",
                )
                print(f"Deleted /scratch/{user.username}/job-{job_uuid}/ from Tapis")
            except Exception as e:
                print(f"Failed to delete job files for {job_uuid}: {e}")
    job_obj.status = current_status
    job_obj.save(update_fields=["status"])
    print("Metabarcoding job status:", current_status)

def check_job(tapis, job_uuid, job_obj, admin_tapis):
    print("Checking job " + job_uuid)
    status = get_job_status(tapis, job_uuid)
    if status:
        current_status = status.get("status")
        print("Current status: " + current_status)
        user = job_obj.job.user

        # If job is in a terminal state, remove PodFiles
        if current_status in ["FAILED", "STOPPED", "FINISHED", "CANCELLED"]:
            job_pod_files = JobPodFile.objects.filter(job = job_obj.job)
            # Delete PodFiles (both DB record + actual file)
            for job_pod_file in job_pod_files:
                pod_file = job_pod_file.podfile
                pod_file.file.delete(save=False)  # remove file from disk
                pod_file.delete()

        # If FINISHED, save .fastq.gz files as NanoporeSequence
        if current_status == "FINISHED":
            all_files = list_all_files(tapis, job_uuid)
            folder, _ = DataFolder.objects.get_or_create(user=user, name=job_obj.output_name)

            for file_path in all_files:
                if not file_path.endswith(".fastq.gz"):
                    continue

                # Retrieve file content from Tapis
                print("GET " + file_path)
                file_content = get_file_content(tapis, job_uuid, file_path)
                print("GOT " + file_path)

                # Derive sequence filename and display name
                seq_filename = f"{job_obj.id}.fastq.gz"  # sequence record ID placeholder (we’ll adjust)
                last_part = os.path.basename(file_path)  # e.g. "96c4c27b..._unclassified.fastq.gz"
                suffix_stripped = re.sub(r'\.fastq\.gz$', '', last_part)  # e.g. "96c4c27b..._unclassified"
                display_name = f"{job_obj.output_name}-{suffix_stripped.split('_')[-1]}"  # unclassified

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
                user_nanopore_seq = UserNanoporeSequence.objects.create(
                    user=user,
                    nanopore_sequence=nanopore_sequence
                )
                nanopore_folder, _ = NanoporeSequenceFolder.objects.get_or_create(
                    usernanoporesequence=user_nanopore_seq,
                    datafolder=folder
                )
            # Delete files from Tapis after retrieval
            try:
                admin_tapis.files.delete(
                    systemId="js2_dnasubway_full_gpu",
                    path=f"home/exouser/{user.username}/job-{job_uuid}/"
                )
                print(f"Deleted /home/exouser/{user.username}/job-{job_uuid}/ from Tapis")
            except Exception as e:
                print(f"Failed to delete /home/exouser/{user.username}/job-{job_uuid}/ from Tapis: {e}")

        # Update status in DB
        job_obj.job.status = current_status
        job_obj.job.save(update_fields=["status"])
        print("Job status:", current_status)

def poll_active_jobs():
    get_service_token()
    admin_token = generate_user_token("jacobs")
    admin_tapis = connect_to_tapis("jacobs", admin_token)
    demux_jobs = (
        Job.objects
        .filter(appId=settings.QIIME2_DEMUX_APP_ID)
        .exclude(status__in=['FINISHED', 'CANCELLED', 'FAILED', 'STOPPED', 'STARTING', 'FAILED_BOOT', 'FINISHING'])
    )
    if demux_jobs.exists():
        usernames = set(j.user.username for j in demux_jobs)

        for username in usernames:
            print("Checking demux jobs for user " + username)
            user_jobs = [j for j in demux_jobs if j.user.username == username]

            user_token = generate_user_token(username)
            tapis = connect_to_tapis(username, user_token)

            for job in user_jobs:
                check_metabarcoding_job(tapis, job.uuid, job, admin_tapis)
    # 1. Query active jobs
    jobs = (
        BasecallingJob.objects
        .select_related('job', 'job__user')
        .exclude(job__status__in=['FINISHED', 'CANCELLED', 'FAILED', 'STOPPED', 'STARTING', 'FAILED_BOOT'])
    )
    if not jobs.exists():
        return

    # 2. Ensure instance is ready
    active, err = ensure_instance_ready(settings.INSTANCE_NAME)
    if err:
        print(err)
        return

    # 3. Get service token
    print("Got service token") 
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
            check_job(tapis, job.job.uuid, job, admin_tapis)

    # --- After processing all jobs, re-check active jobs ---
    remaining_jobs = (
        BasecallingJob.objects
        .select_related('job')
        .exclude(job__status__in=['FINISHED', 'CANCELLED', 'FAILED', 'STOPPED', 'STARTING', 'FAILED_BOOT'])
    )

    if not remaining_jobs.exists() and not PodFile.objects.exists() and settings.SHELVE_INSTANCE:
        print(f"No active jobs or pod files remaining, shelving instance {settings.INSTANCE_NAME}")
        instance_shelved, err = shelve_instance(settings.INSTANCE_NAME)
        if err:
            print(err)
    elif remaining_jobs.exists():
        print(f"{remaining_jobs.count()} active jobs remain, instance stays active")
    else:
        print(f"{PodFile.objects.count()} PodFile(s) remain, instance stays active")
