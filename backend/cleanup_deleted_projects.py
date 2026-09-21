import os
import shutil
from collections import defaultdict
from datetime import timedelta
from django.utils import timezone
from django.core.files.storage import default_storage
from django.db.models import Q
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
    Dada2Result,
    PodFile,
    JobPodFile,
    PronameImportResult,
    PronameFilterResult,
    PronameRefineResult,
    PronameTaxonomyResult,
    RarefactionResult,
    CoreMetricsResult,
    GneissResult,
    AncomResult,
)

User = get_user_model()
BATCH_SIZE = 500

def has_file_q(fields):
    """Returns a Q object that filters for records where at least one field is NOT empty."""
    return Q(*[~Q(**{f: ''}) for f in fields], _connector=Q.OR)

def chunked_filter(queryset, lookup_field, id_list, batch_size=BATCH_SIZE):
    """
    Filters a queryset in batches to prevent SQLite 'too many SQL variables' errors.
    Yields model instances across all batches.
    """
    for i in range(0, len(id_list), batch_size):
        batch = id_list[i : i + batch_size]
        yield from queryset.filter(**{f"{lookup_field}__in": batch})

def cleanup_deleted_project_files(dry_run=True, stdout=None, stderr=None):
    updates_to_perform = defaultdict(lambda: defaultdict(list))
    action = "Listing" if dry_run else "Deleting"
    if stdout:
        stdout.write(f"{action} files associated with deleted projects...\n")

    total_bytes = 0
    total_files = 0

    def maybe_delete(file_field):
        nonlocal total_bytes, total_files

        if not file_field:
            return False

        if hasattr(file_field, 'path'):
            file_path = file_field.path
        # If it's a string, it's already the path
        elif isinstance(file_field, str):
            file_path = file_field
        # If it has a .name attribute (standard FileField), use it to resolve path
        elif hasattr(file_field, 'name') and file_field.name:
            file_path = default_storage.path(file_field.name)
        else:
            return False

        if not file_path:
            return False

        # Use os.path to check if file is in 'sample' or 'reference' directories
        parts = file_path.split(os.sep)
        if "sample" in parts or "reference" in parts:
            return False

        exists = default_storage.exists(file_path)

        if exists:
            is_dir = os.path.isdir(file_path)
            try:
                if is_dir:
                    file_size = sum(os.path.getsize(os.path.join(dirpath, f))
                                    for dirpath, _, filenames in os.walk(file_path)
                                    for f in filenames)
                else:
                    file_size = default_storage.size(file_path)
            except Exception:
                file_size = 0

            total_bytes += file_size
            total_files += 1

            size_kb = file_size / 1_000
            if dry_run:
                if stdout:
                    stdout.write(f"[DRY RUN] Would delete: {file_path} ({size_kb:.2f} KB)")
            else:
                if is_dir:
                    shutil.rmtree(file_path)
                else:
                    default_storage.delete(file_path)
                if stdout:
                    stdout.write(f"Deleted: {file_path}")
            return True

        return False

    def maybe_delete_and_collect(model_instance, field_name, file_field):
        """
        Calls existing maybe_delete. If it returns True (file deleted),
        collects the record ID for a bulk update later.
        """
        if maybe_delete(file_field):
            if not dry_run:
                updates_to_perform[type(model_instance)][field_name].append(model_instance.id)

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

    # Snapshot projects needing file cleanup
    target_project_ids = list(
        Project.objects.filter(deleted=True, files_deleted=False).values_list("id", flat=True)
    )

    if not target_project_ids:
        if stdout:
            stdout.write("No projects pending file cleanup.\n")
        return

    # --------------------------------------------------
    # Cache NanoporeSampleSet directories once
    # --------------------------------------------------

    sample_dirs = list(
        NanoporeSampleSet.objects.values_list("directory", flat=True)
    )

    # ---- STEP 2: MetabarcodingFile ----
    base_qs = MetabarcodingFile.objects.filter(has_file_q(['file'])).prefetch_related("project_links__project").distinct()
    for mfile in chunked_filter(base_qs, 'project_links__project__id', target_project_ids):

        links = list(mfile.project_links.all())

        deleted_links = any(l.project.deleted for l in links)
        active_links = any(not l.project.deleted for l in links)

        if deleted_links and not active_links:
            maybe_delete_and_collect(mfile, 'file', mfile.file)

    # ---- STEP 3: DemuxResult ----
    base_qs = DemuxResult.objects.select_related("job__project").filter(has_file_q(['demux_qza', 'demux_summary_qzv', 'log_file']))
    for d in chunked_filter(base_qs, 'job__project__id', target_project_ids):
        maybe_delete_and_collect(d, 'demux_qza', d.demux_qza)
        maybe_delete_and_collect(d, 'demux_summary_qzv', d.demux_summary_qzv)
        maybe_delete_and_collect(d, 'log_file', d.log_file)

    # ---- STEP 4: MetadataFile ----
    base_qs = MetadataFile.objects.filter(has_file_q(['file'])).prefetch_related("project_links__project").distinct()
    for mfile in chunked_filter(base_qs, 'project_links__project__id', target_project_ids):

        links = list(mfile.project_links.all())

        deleted_links = any(l.project.deleted for l in links)
        active_links = any(not l.project.deleted for l in links)

        if deleted_links and not active_links:
            maybe_delete_and_collect(mfile, 'file', mfile.file)

    # ---- STEP 5: NanoporeSequence ----
    base_qs = NanoporeSequence.objects.filter(has_file_q(['file'])).distinct()
    for seq in chunked_filter(base_qs, 'projectnanoporesequence__project__id', target_project_ids):
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
                maybe_delete_and_collect(seq, 'file', seq.file)

    # ---- STEP 6: DataFile ----
    base_qs = DataFile.objects.filter(has_file_q(['associated_abi', 'associated_fasta'])).distinct()

    for df in chunked_filter(base_qs, 'projectdatafile__project__id', target_project_ids):

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
                    maybe_delete_and_collect(df, 'associated_abi', df.associated_abi)
                    maybe_delete_and_collect(df, 'associated_fasta', df.associated_fasta)

    # ---- STEP 7: MuscleJob ----
    base_qs = MuscleJob.objects.select_related("job__project").filter(has_file_q(['associated_input']))
    for mj in chunked_filter(base_qs, 'job__project__id', target_project_ids):

        if hasattr(mj, "associated_input"):
            maybe_delete_and_collect(mj, 'associated_input', mj.associated_input)

    # ---- STEP 8: MuscleData ----
    base_qs = MuscleData.objects.select_related("muscle_job__job__project").filter(has_file_q(['associated_alignment', 'original_associated_alignment']))
    for md in chunked_filter(base_qs, 'muscle_job__job__project__id', target_project_ids):

        maybe_delete_and_collect(md, 'associated_alignment', md.associated_alignment)
        maybe_delete_and_collect(md, 'original_associated_alignment', md.original_associated_alignment)

    # ---- STEP 9: FastpResult ----
    base_qs = FastpResult.objects.select_related("project_nanopore_sequence__project").filter(has_file_q(['filtered_file', 'json_file', 'html_file']))
    for fr in chunked_filter(base_qs, 'project_nanopore_sequence__project__id', target_project_ids):

        maybe_delete_and_collect(fr, 'filtered_file', fr.filtered_file)
        maybe_delete_and_collect(fr, 'json_file', fr.json_file)
        maybe_delete_and_collect(fr, 'html_file', fr.html_file)

    # ---- STEP 10: PorechopResult ----
    base_qs = PorechopResult.objects.select_related("project_nanopore_sequence__project").filter(has_file_q(['chopped_file', 'html_log_file']))
    for pr in chunked_filter(base_qs, 'project_nanopore_sequence__project__id', target_project_ids):

        maybe_delete_and_collect(pr, 'chopped_file', pr.chopped_file)
        maybe_delete_and_collect(pr, 'html_log_file', pr.html_log_file)

    # ---- STEP 11: MedakaResult ----
    base_qs = MedakaResult.objects.select_related("project_nanopore_sequence__project").filter(has_file_q(['fasta_file', 'fasta_file_medaka_headers', 'medaka_output_dir']))
    for mr in chunked_filter(base_qs, 'project_nanopore_sequence__project__id', target_project_ids):
        maybe_delete_and_collect(mr, 'fasta_file', mr.fasta_file)
        # Don't delete fasta_file_medaka_headers if its a medaka_reference_file
        headers_path = getattr(mr.fasta_file_medaka_headers, 'name', str(mr.fasta_file_medaka_headers))
        if not headers_path.startswith('medaka_reference_files'):
            maybe_delete_and_collect(mr, 'fasta_file_medaka_headers', mr.fasta_file_medaka_headers)
        maybe_delete_and_collect(mr, 'medaka_output_dir', mr.medaka_output_dir)

    # ---- STEP 12: PodFile (Shared user repository file via Job links) ----
    base_qs = PodFile.objects.filter(has_file_q(['file'])).distinct()
    for pf in chunked_filter(base_qs, 'jobpodfile__job__project__id', target_project_ids):
        # Safe boundary check: make sure it isn't linked to any active project jobs
        has_active_links = JobPodFile.objects.filter(podfile=pf, job__project__deleted=False).exists()
        if not has_active_links:
            maybe_delete_and_collect(pf, 'file', pf.file)

    # ---- STEP 13: Dada2Result ----
    fields = ['rooted_tree_qza', 'trim_table_qza', 'rep_seqs_qza', 'stats_qzv', 'rep_seqs_qzv', 'trim_table_qzv', 'log_file']
    base_qs = Dada2Result.objects.select_related("job__project").filter(has_file_q(fields))
    for dr in chunked_filter(base_qs, 'job__project__id', target_project_ids):
        for field in fields:
            maybe_delete_and_collect(dr, field, getattr(dr, field))

    # ---- STEP 14: PronameImportResult ----
    fields = ['duplex_plot', 'simplex_plot', 'dual_plot', 'simplex_distribution',
              'duplex_distribution', 'dual_distribution', 'simplex_reads',
              'duplex_reads', 'dual_reads']
    base_qs = PronameImportResult.objects.select_related("job__project").filter(has_file_q(fields))
    for pir in chunked_filter(base_qs, 'job__project__id', target_project_ids):
        for field in fields:
            maybe_delete_and_collect(pir, field, getattr(pir, field))

    # ---- STEP 15: PronameFilterResult ----
    fields = ['duplex_plot', 'simplex_plot', 'dual_plot', 'simplex_distribution',
              'duplex_distribution', 'dual_distribution', 'simplex_reads',
              'duplex_reads', 'dual_reads']
    base_qs = PronameFilterResult.objects.select_related("job__project").filter(has_file_q(fields))
    for pfr in chunked_filter(base_qs, 'job__project__id', target_project_ids):
        for field in fields:
            maybe_delete_and_collect(pfr, field, getattr(pfr, field))

    # ---- STEP 16: PronameRefineResult ----
    fields = ['rep_seqs_qza', 'trim_table_qza', 'rep_seqs_fasta', 'rep_table_tsv',
              'rooted_tree_qza', 'rep_seqs_qzv', 'trim_table_qzv']
    base_qs = PronameRefineResult.objects.select_related("job__project").filter(has_file_q(fields))
    for prr in chunked_filter(base_qs, 'job__project__id', target_project_ids):
        for field in fields:
            maybe_delete_and_collect(prr, field, getattr(prr, field))

    # ---- STEP 17: PronameTaxonomyResult ----
    fields = ['rooted_tree_qza', 'taxonomy_qza', 'taxa_bar_plots']
    base_qs = PronameTaxonomyResult.objects.select_related("job__project").filter(has_file_q(fields))
    for ptr in chunked_filter(base_qs, 'job__project__id', target_project_ids):
        for field in fields:
            maybe_delete_and_collect(ptr, field, getattr(ptr, field))

    # ---- STEP 18: RarefactionResult ----
    fields = ['alpha_rarefaction_qzv', 'log_file']
    base_qs = RarefactionResult.objects.select_related("job__project").filter(has_file_q(fields))
    for rr in chunked_filter(base_qs, 'job__project__id', target_project_ids):
        for field in fields:
            maybe_delete_and_collect(rr, field, getattr(rr, field))

    # ---- STEP 19: CoreMetricsResult ----
    fields = ['taxonomy_qza', 'bray_curtis_bioenv', 'bray_curtis_emperor', 'evenness_correlation',
              'evenness_group_significance', 'evenness_raincloud', 'faith_pd_correlation',
              'faith_pd_group_significance', 'faith_pd_raincloud', 'observed_features_correlation',
              'observed_features_group_significance', 'observed_features_raincloud', 'shannon_correlation',
              'shannon_group_significance', 'shannon_raincloud', 'jaccard_emperor', 'taxa_bar_plots',
              'taxonomy_qzv', 'unweighted_unifrac_bioenv', 'unweighted_unifrac_emperor', 'weighted_unifrac_emperor']
    base_qs = CoreMetricsResult.objects.select_related("job__project").filter(has_file_q(fields))
    for cmr in chunked_filter(base_qs, 'job__project__id', target_project_ids):
        for field in fields:
            maybe_delete_and_collect(cmr, field, getattr(cmr, field))

    # ---- STEP 20: GneissResult ----
    base_qs = GneissResult.objects.select_related("job__project").filter(has_file_q(['heatmap']))
    for gr in chunked_filter(base_qs, 'job__project__id', target_project_ids):
        maybe_delete_and_collect(gr, 'heatmap', gr.heatmap)

    # ---- STEP 21: AncomResult ----
    fields = ['heatmap', 'abundance_barplot', 'ancom', 'differentials']
    base_qs = AncomResult.objects.select_related("job__project").filter(has_file_q(fields))
    for ar in chunked_filter(base_qs, 'job__project__id', target_project_ids):
        for field in fields:
            maybe_delete_and_collect(ar, field, getattr(ar, field))

    if not dry_run:
        for model_class, fields in updates_to_perform.items():
            for field_name, ids in fields.items():
                if not ids:
                    continue
                for i in range(0, len(ids), BATCH_SIZE):
                    batch_ids = ids[i : i + BATCH_SIZE]
                    model_class.objects.filter(id__in=batch_ids).update(**{field_name: ''})
                    if stdout:
                        stdout.write(
                            f"Cleared {len(batch_ids)} references for "
                            f"{model_class.__name__}.{field_name} (Batch {i//BATCH_SIZE + 1})\n"
                        )
        # Mark targeted projects as cleaned up
        for i in range(0, len(target_project_ids), BATCH_SIZE):
            batch_ids = target_project_ids[i : i + BATCH_SIZE]
            Project.objects.filter(id__in=batch_ids).update(files_deleted=True)
        if stdout:
            stdout.write(f"Marked {len(target_project_ids)} projects as files_deleted=True.\n")

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
