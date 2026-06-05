import os
from datetime import timedelta
from django.utils import timezone
from django.core.files.storage import default_storage
from django.contrib.auth import get_user_model

from .models import (
    Project,
    ProjectMetabarcodingFile,
    MetabarcodingFile,
    DemuxResult,
    ProjectMetadataFile,
    MetadataFile,
    ProjectNanoporeSequence,
    NanoporeSequence,
    NanoporeSampleSet,
    UserNanoporeSequence,
    ProjectDataFile,
    DataFile,
    MuscleJob,
    MuscleData,
    FastpResult,
    PorechopResult,
    MedakaResult,
)

User = get_user_model()

def cleanup_deleted_project_files(dry_run=True, stdout=None, stderr=None):
    action = "Listing" if dry_run else "Deleting"
    if stdout:
        stdout.write(f"{action} files associated with deleted projects...\n")

    total_bytes = 0
    total_files = 0

    def maybe_delete(file_field):
        nonlocal total_bytes, total_files

        if not file_field or not file_field.name:
            return

        file_path = getattr(file_field, "path", None)
        if not file_path:
            return

        # Use os.path to check if file is in 'sample' or 'reference' directories
        parts = file_path.split(os.sep)
        if "sample" in parts or "reference" in parts:
            return

        exists = default_storage.exists(file_path)

        if exists:
            try:
                file_size = default_storage.size(file_path)
            except Exception:
                file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

            total_bytes += file_size
            total_files += 1

            size_kb = file_size / 1_000
            if dry_run:
                if stdout:
                    stdout.write(f"[DRY RUN] Would delete: {file_path} ({size_kb:.2f} KB)")
            else:
                default_storage.delete(file_path)
                if stdout:
                    stdout.write(f"Deleted: {file_path}")

        # 2. Clear the database field so we NEVER check this file again
        if not dry_run:
            file_field.name = ''
            try:
                # Update only this specific field to avoid race conditions
                file_field.instance.save(update_fields=[file_field.field.name])
            except ValueError:
                file_field.instance.save()

    # ---- STEP 1: Mark guest projects as deleted (except created today) ----
    today = timezone.localdate()
    yesterday = timezone.now() - timedelta(days=1)

    # 3. Optimize the update: Only target guests who are inactive AND not already deleted
    guest_projects_to_mark = Project.objects.filter(
        user__username__startswith="guest_",
        deleted=False
    ).exclude(
        created__date=today
    ).filter(
        user__last_login__lt=yesterday
    )

    count = guest_projects_to_mark.update(deleted=True)

    if stdout:
        stdout.write(
            f"Marked {count} guest projects as deleted.\n"
        )

    # --------------------------------------------------
    # Cache NanoporeSampleSet directories once
    # --------------------------------------------------

    sample_dirs = list(
        NanoporeSampleSet.objects.values_list("directory", flat=True)
    )

    # ---- STEP 2: MetabarcodingFile ----
    qs = MetabarcodingFile.objects.filter(
        project_links__project__deleted=True
    ).prefetch_related("project_links__project").distinct()

    for mfile in qs.iterator(chunk_size=500):

        links = list(mfile.project_links.all())

        deleted_links = any(l.project.deleted for l in links)
        active_links = any(not l.project.deleted for l in links)

        if deleted_links and not active_links:
            maybe_delete(mfile.file)

    # ---- STEP 3: DemuxResult ----
    for d in DemuxResult.objects.select_related("job__project")\
            .filter(job__project__deleted=True)\
            .iterator(chunk_size=500):
        maybe_delete(d.demux_qza)
        maybe_delete(d.demux_summary_qzv)
        maybe_delete(d.log_file)

    # ---- STEP 4: MetadataFile ----
    qs = MetadataFile.objects.filter(
        project_links__project__deleted=True
    ).prefetch_related("project_links__project").distinct()

    for mfile in qs.iterator(chunk_size=500):

        links = list(mfile.project_links.all())

        deleted_links = any(l.project.deleted for l in links)
        active_links = any(not l.project.deleted for l in links)

        if deleted_links and not active_links:
            maybe_delete(mfile.file)

    # ---- STEP 5: NanoporeSequence ----
    seq_qs = NanoporeSequence.objects.filter(
        projectnanoporesequence__project__deleted=True
    ).distinct()

    for seq in seq_qs.iterator(chunk_size=500):

        proj_links = list(
            ProjectNanoporeSequence.objects
            .select_related("project")
            .filter(nanopore_sequence=seq)
        )

        deleted_links = any(l.project.deleted for l in proj_links)
        active_links = any(not l.project.deleted for l in proj_links)

        user_links = list(
            UserNanoporeSequence.objects
            .select_related("user")
            .filter(nanopore_sequence=seq)
        )

        guest_owned = any(
            ul.user.username.startswith("guest_")
            for ul in user_links
        )

        sample_set_exists = any(
            seq.file.name.startswith(d)
            for d in sample_dirs
        )

        if deleted_links and not active_links and not sample_set_exists:
            # For guest-owned, skip the user exclusion
            if guest_owned or not user_links:
                maybe_delete(seq.file)

    # ---- STEP 6: DataFile ----
    qs = DataFile.objects.filter(
        projectdatafile__project__deleted=True
    ).select_related("user").prefetch_related(
        "projectdatafile_set__project"
    ).distinct()

    for df in qs.iterator(chunk_size=500):

        links = list(df.projectdatafile_set.all())

        deleted_links = any(l.project.deleted for l in links)
        active_links = any(not l.project.deleted for l in links)

        guest_owned = (
            df.user and df.user.username.startswith("guest_")
        )

        if deleted_links and not active_links:
            # Normally skip in_sequence_repository=True, but not for guest-owned
            if guest_owned or not df.in_sequence_repository:
                if df.source not in ('sample', 'reference'):
                    maybe_delete(df.associated_abi)
                    maybe_delete(df.associated_fasta)

    # ---- STEP 7: MuscleJob ----
    for mj in MuscleJob.objects.select_related("job__project")\
            .filter(job__project__deleted=True)\
            .iterator(chunk_size=500):

        if hasattr(mj, "associated_input"):
            maybe_delete(mj.associated_input)

    # ---- STEP 8: MuscleData ----
    for md in MuscleData.objects.select_related(
        "muscle_job__job__project"
    ).filter(
        muscle_job__job__project__deleted=True
    ).iterator(chunk_size=500):

        maybe_delete(md.associated_alignment)
        maybe_delete(md.original_associated_alignment)

    # ---- STEP 9: FastpResult ----
    for fr in FastpResult.objects.select_related(
        "project_nanopore_sequence__project"
    ).filter(
        project_nanopore_sequence__project__deleted=True
    ).iterator(chunk_size=500):

        maybe_delete(fr.filtered_file)
        maybe_delete(fr.json_file)
        maybe_delete(fr.html_file)

    # ---- STEP 10: PorechopResult ----
    for pr in PorechopResult.objects.select_related(
        "project_nanopore_sequence__project"
    ).filter(
        project_nanopore_sequence__project__deleted=True
    ).iterator(chunk_size=500):

        maybe_delete(pr.chopped_file)
        maybe_delete(pr.html_log_file)

    # ---- STEP 11: MedakaResult ----
    for mr in MedakaResult.objects.select_related(
        "project_nanopore_sequence__project"
    ).filter(
        project_nanopore_sequence__project__deleted=True
    ).iterator(chunk_size=500):

        maybe_delete(mr.fasta_file)
        maybe_delete(mr.fasta_file_medaka_headers)
        maybe_delete(mr.medaka_output_dir)

    total_mb = total_bytes / 1_000_000
    total_gb = total_bytes / 1_000_000_000
    summary_text = (
        f"\nTotal files to be deleted: {total_files}\n"
        f"Total size: {total_mb:.2f} MB ({total_gb:.2f} GB)\n"
    )

    if stdout:
        if dry_run:
            stdout.write(f"[DRY RUN] {summary_text}")
        else:
            stdout.write(summary_text)

        stdout.write("\nCleanup complete.\n")
