import os
import shutil
from collections import defaultdict
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
            maybe_delete_and_collect(mfile, 'file', mfile.file)

    # ---- STEP 3: DemuxResult ----
    for d in DemuxResult.objects.select_related("job__project")\
            .filter(job__project__deleted=True)\
            .iterator(chunk_size=500):
        maybe_delete_and_collect(d, 'demux_qza', d.demux_qza)
        maybe_delete_and_collect(d, 'demux_summary_qzv', d.demux_summary_qzv)
        maybe_delete_and_collect(d, 'log_file', d.log_file)

    # ---- STEP 4: MetadataFile ----
    qs = MetadataFile.objects.filter(
        project_links__project__deleted=True
    ).prefetch_related("project_links__project").distinct()

    for mfile in qs.iterator(chunk_size=500):

        links = list(mfile.project_links.all())

        deleted_links = any(l.project.deleted for l in links)
        active_links = any(not l.project.deleted for l in links)

        if deleted_links and not active_links:
            maybe_delete_and_collect(mfile, 'file', mfile.file)

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
                maybe_delete_and_collect(seq, 'file', seq.file)

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
                    maybe_delete_and_collect(df, 'associated_abi', df.associated_abi)
                    maybe_delete_and_collect(df, 'associated_fasta', df.associated_fasta)

    # ---- STEP 7: MuscleJob ----
    for mj in MuscleJob.objects.select_related("job__project")\
            .filter(job__project__deleted=True)\
            .iterator(chunk_size=500):

        if hasattr(mj, "associated_input"):
            maybe_delete_and_collect(mj, 'associated_input', mj.associated_input)

    # ---- STEP 8: MuscleData ----
    for md in MuscleData.objects.select_related(
        "muscle_job__job__project"
    ).filter(
        muscle_job__job__project__deleted=True
    ).iterator(chunk_size=500):

        maybe_delete_and_collect(md, 'associated_alignment', md.associated_alignment)
        maybe_delete_and_collect(md, 'original_associated_alignment', md.original_associated_alignment)

    # ---- STEP 9: FastpResult ----
    for fr in FastpResult.objects.select_related(
        "project_nanopore_sequence__project"
    ).filter(
        project_nanopore_sequence__project__deleted=True
    ).iterator(chunk_size=500):

        maybe_delete_and_collect(fr, 'filtered_file', fr.filtered_file)
        maybe_delete_and_collect(fr, 'json_file', fr.json_file)
        maybe_delete_and_collect(fr, 'html_file', fr.html_file)

    # ---- STEP 10: PorechopResult ----
    for pr in PorechopResult.objects.select_related(
        "project_nanopore_sequence__project"
    ).filter(
        project_nanopore_sequence__project__deleted=True
    ).iterator(chunk_size=500):

        maybe_delete_and_collect(pr, 'chopped_file', pr.chopped_file)
        maybe_delete_and_collect(pr, 'html_log_file', pr.html_log_file)

    # ---- STEP 11: MedakaResult ----
    for mr in MedakaResult.objects.select_related(
        "project_nanopore_sequence__project"
    ).filter(
        project_nanopore_sequence__project__deleted=True
    ).iterator(chunk_size=500):

        maybe_delete_and_collect(mr, 'fasta_file', mr.fasta_file)
        maybe_delete_and_collect(mr, 'fasta_file_medaka_headers', mr.fasta_file_medaka_headers)
        maybe_delete_and_collect(mr, 'medaka_output_dir', mr.medaka_output_dir)

    # ---- STEP 12: PodFile (Shared user repository file via Job links) ----
    pod_qs = PodFile.objects.filter(jobpodfile__job__project__deleted=True).distinct()
    for pf in pod_qs.iterator(chunk_size=500):
        # Safe boundary check: make sure it isn't linked to any active project jobs
        has_active_links = JobPodFile.objects.filter(podfile=pf, job__project__deleted=False).exists()
        if not has_active_links:
            maybe_delete_and_collect(pf, 'file', pf.file)

    # ---- STEP 13: Dada2Result ----
    for dr in Dada2Result.objects.select_related(
        "job__project"
    ).filter(
        job__project__deleted=True
    ).iterator(chunk_size=500):
        maybe_delete_and_collect(dr, 'rooted_tree_qza', dr.rooted_tree_qza)
        maybe_delete_and_collect(dr, 'trim_table_qza', dr.trim_table_qza)
        maybe_delete_and_collect(dr, 'rep_seqs_qza', dr.rep_seqs_qza)
        maybe_delete_and_collect(dr, 'stats_qzv', dr.stats_qzv)
        maybe_delete_and_collect(dr, 'rep_seqs_qzv', dr.rep_seqs_qzv)
        maybe_delete_and_collect(dr, 'trim_table_qzv', dr.trim_table_qzv)
        maybe_delete_and_collect(dr, 'log_file', dr.log_file)

    # ---- STEP 14: PronameImportResult ----
    for pir in PronameImportResult.objects.select_related(
        "job__project"
    ).filter(
        job__project__deleted=True
    ).iterator(chunk_size=500):
        for field in ['duplex_plot', 'simplex_plot', 'dual_plot', 'simplex_distribution',
                      'duplex_distribution', 'dual_distribution', 'simplex_reads',
                      'duplex_reads', 'dual_reads']:
            maybe_delete_and_collect(pir, field, getattr(pir, field))

    # ---- STEP 15: PronameFilterResult ----
    for pfr in PronameFilterResult.objects.select_related(
        "job__project"
    ).filter(
        job__project__deleted=True
    ).iterator(chunk_size=500):
        for field in ['duplex_plot', 'simplex_plot', 'dual_plot', 'simplex_distribution',
                      'duplex_distribution', 'dual_distribution', 'simplex_reads',
                      'duplex_reads', 'dual_reads']:
            maybe_delete_and_collect(pfr, field, getattr(pfr, field))

    # ---- STEP 16: PronameRefineResult ----
    for prr in PronameRefineResult.objects.select_related(
        "job__project"
    ).filter(
        job__project__deleted=True
    ).iterator(chunk_size=500):
        for field in ['rep_seqs_qza', 'trim_table_qza', 'rep_seqs_fasta', 'rep_table_tsv',
                      'rooted_tree_qza', 'rep_seqs_qzv', 'trim_table_qzv']:
            maybe_delete_and_collect(prr, field, getattr(prr, field))

    # ---- STEP 17: PronameTaxonomyResult ----
    for ptr in PronameTaxonomyResult.objects.select_related(
        "job__project"
    ).filter(
        job__project__deleted=True
    ).iterator(chunk_size=500):
        maybe_delete_and_collect(ptr, 'rooted_tree_qza', ptr.rooted_tree_qza)
        maybe_delete_and_collect(ptr, 'taxonomy_qza', ptr.taxonomy_qza)
        maybe_delete_and_collect(ptr, 'taxa_bar_plots', ptr.taxa_bar_plots)

    # ---- STEP 18: RarefactionResult ----
    for rr in RarefactionResult.objects.select_related(
        "job__project"
    ).filter(
        job__project__deleted=True
    ).iterator(chunk_size=500):
        maybe_delete_and_collect(rr, 'alpha_rarefaction_qzv', rr.alpha_rarefaction_qzv)
        maybe_delete_and_collect(rr, 'log_file', rr.log_file)

    # ---- STEP 19: CoreMetricsResult ----
    for cmr in CoreMetricsResult.objects.select_related(
        "job__project"
    ).filter(
        job__project__deleted=True
    ).iterator(chunk_size=500):
        fields = ['taxonomy_qza', 'bray_curtis_bioenv', 'bray_curtis_emperor', 'evenness_correlation',
                  'evenness_group_significance', 'evenness_raincloud', 'faith_pd_correlation',
                  'faith_pd_group_significance', 'faith_pd_raincloud', 'observed_features_correlation',
                  'observed_features_group_significance', 'observed_features_raincloud', 'shannon_correlation',
                  'shannon_group_significance', 'shannon_raincloud', 'jaccard_emperor', 'taxa_bar_plots',
                  'taxonomy_qzv', 'unweighted_unifrac_bioenv', 'unweighted_unifrac_emperor', 'weighted_unifrac_emperor']
        for field in fields:
            maybe_delete_and_collect(cmr, field, getattr(cmr, field))

    # ---- STEP 20: GneissResult ----
    for gr in GneissResult.objects.select_related(
        "job__project"
    ).filter(
        job__project__deleted=True
    ).iterator(chunk_size=500):
        maybe_delete_and_collect(gr, 'heatmap', gr.heatmap)

    # ---- STEP 21: AncomResult ----
    for ar in AncomResult.objects.select_related(
        "job__project"
    ).filter(
        job__project__deleted=True
    ).iterator(chunk_size=500):
        for field in ['heatmap', 'abundance_barplot', 'ancom', 'differentials']:
            maybe_delete_and_collect(ar, field, getattr(ar, field))

    if not dry_run:
        BATCH_SIZE = 1000
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
