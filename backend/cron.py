from django.conf import settings
import os
import re
from .models import BasecallingJob, PodFile, NanoporeSequence, UserNanoporeSequence, JobPodFile, DataFolder, NanoporeSequenceFolder, DemuxResult, Dada2Result, RarefactionResult, Job
from .utils import (
    shelve_instance,
    ensure_instance_ready,
    get_service_token,
    generate_user_token,
    connect_to_tapis,
    list_all_files,
    get_file_content,
    download_tapis_file,
)
from django.core.files.base import ContentFile
from datetime import datetime

def tprint(*args, **kwargs):
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sep = kwargs.get('sep', ' ')
    end = kwargs.get('end', '\n')
    print(stamp + ' :: ', *args, sep=sep, end=end)

def get_job_status(tapis, job_uuid):
    try:
        job_details = tapis.jobs.getJob(jobUuid=job_uuid)
        return job_details
    except Exception as e:
        tprint("Error retrieving job details:", e)
        return None

def check_demux_job(tapis, user_token, job_uuid, job_obj, admin_tapis):
    tprint("Checking demux job " + job_uuid)
    status = get_job_status(tapis, job_uuid)
    if not status:
        return
    current_status = status.get("status")
    tprint("Current status: " + current_status)
    user = job_obj.user

    if current_status == "FINISHED":
        job_obj.status = "FINISHING"
        job_obj.save(update_fields=["status"])
        # List files from Tapis job archive
        all_files = list_all_files(tapis, job_uuid)

        qza_file = None
        qzv_file = None
        qza_archived = False
        qzv_archived = False

        for file_path in all_files:
            if file_path.endswith("imported-demux.qza"):
                qza_file = file_path
            elif file_path.endswith("imported-demux.qzv"):
                qzv_file = file_path
        if qza_file or qzv_file:
            demux_result, _ = DemuxResult.objects.get_or_create(job=job_obj)

            if qza_file:
                tprint("GET " + qza_file)
                file_content = download_tapis_file("js2_dnasubway2", user_token, f"scratch/{user.username}/job-{job_uuid}/imported-demux.qza")
                if file_content:
                    demux_result.demux_qza.save(
                        f"{demux_result.id}-imported-demux.qza",
                        ContentFile(file_content),
                        save=False,
                    )
                    qza_archived = True
            if qzv_file:
                tprint("GET " + qzv_file)
                file_content = download_tapis_file("js2_dnasubway2", user_token, f"scratch/{user.username}/job-{job_uuid}/imported-demux.qzv")
                if file_content:
                    demux_result.demux_summary_qzv.save(
                        f"{demux_result.id}-imported-demux.qzv",
                        ContentFile(file_content),
                        save=False,
                    )
                    qzv_archived = True
            demux_result.save()

            # cleanup remote job dir
            try:
                admin_tapis.files.delete(
                    systemId="js2_dnasubway2",
                    path=f"scratch/{user.username}/job-{job_uuid}/",
                )
                tprint(f"Deleted /scratch/{user.username}/job-{job_uuid}/ from Tapis")
            except Exception as e:
                tprint(f"Failed to delete job files for {job_uuid}: {e}")
        if not qza_archived or not qzv_archived:
            job_obj.status = "FAILED"
            job_obj.save(update_fields=["status"])
            return
    job_obj.status = current_status
    job_obj.save(update_fields=["status"])
    tprint("Demux job status:", current_status)

def check_dada2_job(tapis, user_token, job_uuid, job_obj, admin_tapis):
    tprint("Checking dada2 job " + job_uuid)
    status = get_job_status(tapis, job_uuid)
    if not status:
        return
    current_status = status.get("status")
    tprint("Current status: " + current_status)
    user = job_obj.user

    if current_status == "FINISHED":
        job_obj.status = "FINISHING"
        job_obj.save(update_fields=["status"])
        # List files from Tapis job archive
        all_files = list_all_files(tapis, job_uuid)

        rooted_tree_qza_file = None
        trim_table_qza_file = None
        rep_seqs_qza_file = None
        stats_qzv_file = None
        rep_seqs_qzv_file = None
        trim_table_qzv_file = None
        rooted_tree_qza_archived = False
        trim_table_qza_archived = False
        rep_seqs_qza_archived = False
        stats_qzv_archived = False
        rep_seqs_qzv_archived = False
        trim_table_qzv_archived = False

        for file_path in all_files:
            if file_path.endswith("rooted-tree.qza"):
                rooted_tree_qza_file = file_path
            elif file_path.endswith("table-trimming.qza"):
                trim_table_qza_file = file_path
            elif file_path.endswith("rep-seqs.qza"):
                rep_seqs_qza_file = file_path
            elif file_path.endswith("stats.qzv"):
                stats_qzv_file = file_path
            elif file_path.endswith("rep-seqs.qzv"):
                rep_seqs_qzv_file = file_path
            elif file_path.endswith("table-trimming.qzv"):
                trim_table_qzv_file = file_path
        if any([rooted_tree_qza_file, trim_table_qza_file, rep_seqs_qza_file, stats_qzv_file, rep_seqs_qzv_file, trim_table_qzv_file]):
            dada2_result, _ = Dada2Result.objects.get_or_create(job=job_obj)
            def download_and_save(remote_path, field_name, filename):
                content = download_tapis_file("js2_dnasubway2", user_token, remote_path)
                if content:
                    getattr(dada2_result, field_name).save(
                        filename,
                        ContentFile(content),
                        save=False
                    )
                    return True
                return False

            if rooted_tree_qza_file:
                tprint(f"GET {rooted_tree_qza_file}")
                rooted_tree_qza_archived = download_and_save(
                    f"scratch/{user.username}/job-{job_uuid}/output/rooted-tree.qza",
                    "rooted_tree_qza",
                    f"{dada2_result.id}-rooted-tree.qza"
                )

            if trim_table_qza_file:
                tprint(f"GET {trim_table_qza_file}")
                trim_table_qza_archived = download_and_save(
                    f"scratch/{user.username}/job-{job_uuid}/output/table-trimming.qza",
                    "trim_table_qza",
                    f"{dada2_result.id}-table-trimming.qza"
                )

            if rep_seqs_qza_file:
                tprint(f"GET {rep_seqs_qza_file}")
                rep_seqs_qza_archived = download_and_save(
                    f"scratch/{user.username}/job-{job_uuid}/output/rep-seqs.qza",
                    "rep_seqs_qza",
                    f"{dada2_result.id}-rep-seqs.qza"
                )

            if stats_qzv_file:
                tprint(f"GET {stats_qzv_file}")
                stats_qzv_archived = download_and_save(
                    f"scratch/{user.username}/job-{job_uuid}/output/stats.qzv",
                    "stats_qzv",
                    f"{dada2_result.id}-stats.qzv"
                )

            if rep_seqs_qzv_file:
                tprint(f"GET {rep_seqs_qzv_file}")
                rep_seqs_qzv_archived = download_and_save(
                    f"scratch/{user.username}/job-{job_uuid}/output/rep-seqs.qzv",
                    "rep_seqs_qzv",
                    f"{dada2_result.id}-rep-seqs.qzv"
                )

            if trim_table_qzv_file:
                tprint(f"GET {trim_table_qzv_file}")
                trim_table_qzv_archived = download_and_save(
                    f"scratch/{user.username}/job-{job_uuid}/output/table-trimming.qzv",
                    "trim_table_qzv",
                    f"{dada2_result.id}-table-trimming.qzv"
                )

            dada2_result.save()

            # cleanup remote job dir
            try:
                admin_tapis.files.delete(
                    systemId="js2_dnasubway2",
                    path=f"scratch/{user.username}/job-{job_uuid}/",
                )
                tprint(f"Deleted /scratch/{user.username}/job-{job_uuid}/ from Tapis")
            except Exception as e:
                tprint(f"Failed to delete job files for {job_uuid}: {e}")
        if not all([
            rooted_tree_qza_archived,
            trim_table_qza_archived,
            rep_seqs_qza_archived,
            stats_qzv_archived,
            rep_seqs_qzv_archived,
            trim_table_qzv_archived
        ]):
            job_obj.status = "FAILED"
            job_obj.save(update_fields=["status"])
            return
    job_obj.status = current_status
    job_obj.save(update_fields=["status"])
    tprint("Dada2 job status:", current_status)

def check_rarefaction_job(tapis, user_token, job_uuid, job_obj, admin_tapis):
    tprint("Checking rarefaction job " + job_uuid)
    status = get_job_status(tapis, job_uuid)
    if not status:
        return
    current_status = status.get("status")
    tprint("Current status: " + current_status)
    user = job_obj.user

    if current_status == "FINISHED":
        job_obj.status = "FINISHING"
        job_obj.save(update_fields=["status"])
        # List files from Tapis job archive
        all_files = list_all_files(tapis, job_uuid)

        alpha_rarefaction_qzv_file = None
        alpha_rarefaction_qzv_archived = False

        for file_path in all_files:
            if file_path.endswith("alpha-rarefaction-trimming.qzv"):
                alpha_rarefaction_qzv_file = file_path
        if alpha_rarefaction_qzv_file:
            rarefaction_result, _ = RarefactionResult.objects.get_or_create(job=job_obj)
            def download_and_save(remote_path, field_name, filename):
                content = download_tapis_file("js2_dnasubway2", user_token, remote_path)
                if content:
                    getattr(rarefaction_result, field_name).save(
                        filename,
                        ContentFile(content),
                        save=False
                    )
                    return True
                return False

            if alpha_rarefaction_qzv_file:
                tprint(f"GET {alpha_rarefaction_qzv_file}")
                alpha_rarefaction_qzv_archived = download_and_save(
                    f"scratch/{user.username}/job-{job_uuid}/alpha-rarefaction-trimming.qzv",
                    "alpha_rarefaction_qzv",
                    f"{rarefaction_result.id}-alpha-rarefaction.qzv"
                )

            rarefaction_result.save()

            # cleanup remote job dir
            try:
                admin_tapis.files.delete(
                    systemId="js2_dnasubway2",
                    path=f"scratch/{user.username}/job-{job_uuid}/",
                )
                tprint(f"Deleted /scratch/{user.username}/job-{job_uuid}/ from Tapis")
            except Exception as e:
                tprint(f"Failed to delete job files for {job_uuid}: {e}")
        if not alpha_rarefaction_qzv_archived:
            job_obj.status = "FAILED"
            job_obj.save(update_fields=["status"])
            return
    job_obj.status = current_status
    job_obj.save(update_fields=["status"])
    tprint("Rarefaction job status:", current_status)

def check_job(tapis, job_uuid, job_obj, admin_tapis):
    tprint("Checking job " + job_uuid)
    status = get_job_status(tapis, job_uuid)
    if status:
        current_status = status.get("status")
        tprint("Current status: " + current_status)
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
                tprint("GET " + file_path)
                file_content = get_file_content(tapis, job_uuid, file_path)
                tprint("GOT " + file_path)

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
                tprint(f"Deleted /home/exouser/{user.username}/job-{job_uuid}/ from Tapis")
            except Exception as e:
                tprint(f"Failed to delete /home/exouser/{user.username}/job-{job_uuid}/ from Tapis: {e}")

        # Update status in DB
        job_obj.job.status = current_status
        job_obj.job.save(update_fields=["status"])
        tprint("Job status:", current_status)

def poll_active_jobs():
    get_service_token()
    admin_token = generate_user_token("jacobs")
    admin_tapis = connect_to_tapis("jacobs", admin_token)
    demux_jobs = (
        Job.objects
        .filter(appId=settings.QIIME2_DEMUX_APP_ID)
        .exclude(status__in=['FINISHED', 'CANCELLED', 'FAILED', 'STOPPED', 'STARTING', 'FAILED_BOOT', 'FINISHING'])
    )
    dada2_jobs = (
        Job.objects
        .filter(appId=settings.QIIME2_DADA2_APP_ID)
        .exclude(status__in=['FINISHED', 'CANCELLED', 'FAILED', 'STOPPED', 'STARTING', 'FAILED_BOOT', 'FINISHING'])
    )
    rarefaction_jobs = (
        Job.objects
        .filter(appId=settings.QIIME2_RAREFACTION_APP_ID)
        .exclude(status__in=['FINISHED', 'CANCELLED', 'FAILED', 'STOPPED', 'STARTING', 'FAILED_BOOT', 'FINISHING'])
    )
    if demux_jobs.exists():
        usernames = set(j.user.username for j in demux_jobs)

        for username in usernames:
            tprint("Checking demux jobs for user " + username)
            user_demux_jobs = [j for j in demux_jobs if j.user.username == username]

            user_token = generate_user_token(username)
            tapis = connect_to_tapis(username, user_token)

            for job in user_demux_jobs:
                check_demux_job(tapis, user_token, job.uuid, job, admin_tapis)
    if dada2_jobs.exists():
        usernames = set(j.user.username for j in dada2_jobs)

        for username in usernames:
            tprint("Checking dada2 jobs for user " + username)
            user_dada2_jobs = [j for j in dada2_jobs if j.user.username == username]

            user_token = generate_user_token(username)
            tapis = connect_to_tapis(username, user_token)

            for job in user_dada2_jobs:
                check_dada2_job(tapis, user_token, job.uuid, job, admin_tapis)
    if rarefaction_jobs.exists():
        usernames = set(j.user.username for j in rarefaction_jobs)

        for username in usernames:
            tprint("Checking rarefaction jobs for user " + username)
            user_rarefaction_jobs = [j for j in rarefaction_jobs if j.user.username == username]

            user_token = generate_user_token(username)
            tapis = connect_to_tapis(username, user_token)

            for job in user_rarefaction_jobs:
                check_rarefaction_job(tapis, user_token, job.uuid, job, admin_tapis)
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
        tprint(err)
        return

    # 3. Get service token
    tprint("Got service token")
    tprint("Got jobs")
    tprint(jobs)

    # 4. Group by user
    usernames = set(j.job.user.username for j in jobs)
    tprint("Got users")
    tprint(usernames)


    for username in usernames:
        tprint("Checking user " + username)
        user_jobs = [j for j in jobs if j.job.user.username == username]
        
        user_token = generate_user_token(username)
        tprint("User token: " + user_token)
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
        tprint(f"No active jobs or pod files remaining, shelving instance {settings.INSTANCE_NAME}")
        instance_shelved, err = shelve_instance(settings.INSTANCE_NAME)
        if err:
            tprint(err)
    elif remaining_jobs.exists():
        tprint(f"{remaining_jobs.count()} active jobs remain, instance stays active")
    else:
        tprint(f"{PodFile.objects.count()} PodFile(s) remain, instance stays active")
