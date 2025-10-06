import os
from datetime import date
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

    def maybe_delete(file_field, dry_run):
        nonlocal total_bytes, total_files

        if not file_field:
            return
        file_path = getattr(file_field, "path", None)
        if not file_path or not default_storage.exists(file_path):
            return

        # Use os.path to check if file is in 'sample' or 'reference' directories
        parts = file_path.split(os.sep)
        if "sample" in parts or "reference" in parts:
            return

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

    # ---- STEP 1: Mark guest projects as deleted (except created today) ----
    today = timezone.localdate()
    guest_users = User.objects.filter(username__startswith="guest_")
    guest_projects_qs = Project.objects.filter(user__in=guest_users)
    guest_projects_to_mark = guest_projects_qs.exclude(created__date=today)

    count = guest_projects_to_mark.update(deleted=True)
    if stdout:
        stdout.write(
            f"Marked {count} guest projects as deleted "
            f"(excluding those created today: {guest_projects_qs.count() - count}).\n"
        )

    # ---- STEP 2: MetabarcodingFile ----
    for mfile in MetabarcodingFile.objects.all():
        p_links = ProjectMetabarcodingFile.objects.filter(metabarcoding_file=mfile)
        if not p_links.exists():
            continue
        deleted_links = p_links.filter(project__deleted=True)
        active_links = p_links.filter(project__deleted=False)
        if deleted_links.exists() and not active_links.exists():
            maybe_delete(mfile.file, dry_run)

    # ---- STEP 3: DemuxResult ----
    for d in DemuxResult.objects.filter(job__project__deleted=True):
        for field_name in ["demux_qza", "demux_summary_qzv", "log_file"]:
            maybe_delete(getattr(d, field_name), dry_run)

    # ---- STEP 4: MetadataFile ----
    for mfile in MetadataFile.objects.all():
        p_links = ProjectMetadataFile.objects.filter(metadata_file=mfile)
        deleted_links = p_links.filter(project__deleted=True)
        active_links = p_links.filter(project__deleted=False)
        if deleted_links.exists() and not active_links.exists():
            maybe_delete(mfile.file, dry_run)

    # ---- STEP 5: NanoporeSequence ----
    for seq in NanoporeSequence.objects.all():
        proj_links = ProjectNanoporeSequence.objects.filter(nanopore_sequence=seq)
        deleted_links = proj_links.filter(project__deleted=True)
        active_links = proj_links.filter(project__deleted=False)
        user_links = UserNanoporeSequence.objects.filter(nanopore_sequence=seq)

        # guest user owns this sequence?
        guest_user_link = user_links.filter(user__username__startswith="guest_").exists()
        guest_owned = guest_user_link

        sample_set_exists = NanoporeSampleSet.objects.filter(
            directory__in=[
                sample_set.directory
                for sample_set in NanoporeSampleSet.objects.all()
                if seq.file.name.startswith(sample_set.directory)
            ]
        ).exists()

        if deleted_links.exists() and not active_links.exists() and not sample_set_exists:
            # For guest-owned, skip the user exclusion
            if guest_owned or not user_links.exists():
                maybe_delete(seq.file, dry_run)

    # ---- STEP 6: DataFile ----
    for df in DataFile.objects.all():
        proj_links = ProjectDataFile.objects.filter(data_file=df)
        deleted_links = proj_links.filter(project__deleted=True)
        active_links = proj_links.filter(project__deleted=False)
        guest_owned = (
            hasattr(df, "user")
            and df.user
            and df.user.username.startswith("guest_")
        )

        if deleted_links.exists() and not active_links.exists():
            # Normally skip in_sequence_repository=True, but not for guest-owned
            if guest_owned or not df.in_sequence_repository:
                if df.source not in ('sample', 'reference'):
                    for field_name in ["associated_abi", "associated_fasta"]:
                        maybe_delete(getattr(df, field_name), dry_run)

    # ---- STEP 7: MuscleJob ----
    for mj in MuscleJob.objects.filter(job__project__deleted=True):
        if hasattr(mj, "associated_input"):
            maybe_delete(mj.associated_input, dry_run)

    # ---- STEP 8: MuscleData ----
    for md in MuscleData.objects.filter(muscle_job__job__project__deleted=True):
        for field_name in ["associated_alignment", "original_associated_alignment"]:
            maybe_delete(getattr(md, field_name), dry_run)

    # ---- STEP 9: FastpResult ----
    for fr in FastpResult.objects.filter(project_nanopore_sequence__project__deleted=True):
        for field_name in ["filtered_file", "json_file", "html_file"]:
            maybe_delete(getattr(fr, field_name), dry_run)

    # ---- STEP 10: PorechopResult ----
    for pr in PorechopResult.objects.filter(project_nanopore_sequence__project__deleted=True):
        for field_name in ["chopped_file", "html_log_file"]:
            maybe_delete(getattr(pr, field_name), dry_run)

    # ---- STEP 11: MedakaResult ----
    for mr in MedakaResult.objects.filter(project_nanopore_sequence__project__deleted=True):
        for field_name in ["fasta_file", "fasta_file_medaka_headers", "medaka_output_dir"]:
            maybe_delete(getattr(mr, field_name), dry_run)

    total_mb = total_bytes / 1_000_000
    total_gb = total_bytes / 1_000_000_000
    summary_text = (
        f"\nTotal files to be deleted: {total_files}\n"
        f"Total size: {total_mb:.2f} MB ({total_gb:.2f} GB)\n"
    )

    if dry_run:
        if stdout:
            stdout.write(f"[DRY RUN] {summary_text}")
    else:
        if stdout:
            stdout.write(summary_text)

    if stdout:
        stdout.write("\nCleanup complete.\n")
