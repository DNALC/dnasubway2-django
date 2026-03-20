from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
import secrets
from django.utils.timezone import now
from django.core.validators import MinValueValidator, MaxValueValidator

def reference_file_path(instance, filename):
    # This function will construct the path based on the category of the reference data
    # e.g., fasta_files/sample/mtDNA/filename.fasta
    return f"fasta_files/reference/{instance.category}/{filename}"

def sample_file_path(instance, filepath):
    # This function will construct the path based on the category of the reference data
    # e.g., fasta_files/reference/mtDNA/filepath
    return f"{instance.file_type}_files/sample/{instance.category}/{filepath}"

class ReferenceData(models.Model):
    category = models.CharField(max_length=255)  # e.g., 'COI', 'mtDNA'
    name = models.CharField(max_length=255)      # Name of the reference file or sequence
    file = models.FileField(upload_to=reference_file_path)  # Use dynamic upload path based on category

    def __str__(self):
        return f"{self.category} - {self.name}"

class SampleData(models.Model):
    category = models.CharField(max_length=255)  # e.g., 'COI', 'mtDNA'
    name = models.CharField(max_length=255)      # Name of the reference file or sequence
    FILE_TYPE_CHOICES = (
        ('abi', 'ABI File'),
        ('fasta', 'FASTA File'),
    )
    file_type = models.CharField(max_length=8, choices=FILE_TYPE_CHOICES)
    file = models.FileField(upload_to=sample_file_path)  # Use dynamic upload path based on category

    def __str__(self):
        return f"{self.category} - {self.file_type} - {self.name}"

class Ethnicity(models.Model):
    native = models.BooleanField(default=False)
    asian = models.BooleanField(default=False)
    black = models.BooleanField(default=False)
    hispanic = models.BooleanField(default=False)
    middle = models.BooleanField(default=False)
    pacific = models.BooleanField(default=False)
    white = models.BooleanField(default=False)
    other = models.BooleanField(default=False)
    unanswered = models.BooleanField(default=False)

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    country = models.CharField(max_length=2)
    postal_code = models.CharField(max_length=12)
    institution = models.CharField(max_length=64, default='')
    GENDER_CHOICES = (
        ('m', 'Male'),
        ('f', 'Female'),
        ('n', 'Non-Binary/Other'),
        ('-', 'I prefer not to share'),
    )
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    OCCUPATION_CHOICES = (
        ('ss', 'K-12 Student'),
        ('st', 'K-12 Teacher'),
        ('us', 'Undergraduate Student'),
        ('gs', 'Graduate Student'),
        ('pd', 'Postdoctorate'),
        ('cf', 'University/College Faculty'),
        ('cs', 'University/College Staff'),
        ('iu', 'Industrial User'),
        ('gu', 'Government User'),
        ('uu', 'Unaffiliated User'),
        ('nu', 'Nonprofit User'),
        ('oo', 'Other'),
        ('np', 'Not Provided'),
    )
    occupation = models.CharField(max_length=2, choices=OCCUPATION_CHOICES)
    SOURCE_CHOICES = (
        ('fr', 'Friend'),
        ('sd', 'Student'),
        ('is', 'Instructor'),
        ('cl', 'Colleague'),
        ('ws', 'Workshop'),
        ('cv', 'Convention'),
        ('de', 'Direct Email'),
        ('se', 'Search Engine'),
        ('in', 'Internet'),
        ('gb', 'UCSC Genome Browser'),
        ('sm', 'Social Media'),
        ('oo', 'Other'),
        ('np', 'Not Provided'),
    )
    source = models.CharField(max_length=2, choices=SOURCE_CHOICES)
    ethnicity = models.ForeignKey(Ethnicity, on_delete=models.CASCADE)
    elevated_access = models.BooleanField(default=False)
    verified = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username}'s profile"

class TutorialSettings(models.Model):
    user_profile = models.OneToOneField(UserProfile, on_delete=models.CASCADE)

    disable_phy_tutorial = models.BooleanField(default=False)
    disable_ngs_tutorial = models.BooleanField(default=False)
    disable_ub_tutorial = models.BooleanField(default=False)
    disable_nanopore_phy_tutorial = models.BooleanField(default=False)

    def is_disabled(self, project):
        key = self._get_key(project)
        return getattr(self, key, False)

    def disable(self, project):
        key = self._get_key(project)
        setattr(self, key, True)
        self.save()

    def _get_key(self, project):
        if project.project_type == 'PHY' and project.sequencing_type == 'nanopore':
            return 'disable_nanopore_phy_tutorial'
        return {
            'PHY': 'disable_phy_tutorial',
            'NGS': 'disable_ngs_tutorial',
            'UB': 'disable_ub_tutorial',
        }.get(project.project_type)

class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    @classmethod
    def create_token(cls, user):
        token = secrets.token_hex(16)
        expiration_time = timezone.now() + timezone.timedelta(hours=1)  # Token expires in 1 hour
        return cls.objects.create(user=user, token=token, expires_at=expiration_time)

class EmailVerifyToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    @classmethod
    def create_token(cls, user):
        token = secrets.token_hex(16)
        expiration_time = timezone.now() + timezone.timedelta(hours=1)  # Token expires in 1 hour
        return cls.objects.create(user=user, token=token, expires_at=expiration_time)

class EnhancedPermissionToken(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('denied', 'Denied'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    @classmethod
    def create_token(cls, user, reason):
        token = secrets.token_hex(16)
        return cls.objects.create(user=user, token=token, reason=reason, status='pending')

class Project(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=64, default='')
    description = models.CharField(max_length=160, default='')
    SEQUENCING_TYPES = [
        ('nanopore', 'Nanopore'),
        ('sanger', 'Sanger'),
        ('illumina', 'Illumina'),
        ('', 'Unknown')
    ]
    sequencing_type = models.CharField(max_length=8, choices=SEQUENCING_TYPES, default='sanger')
    PROJECT_TYPES = [
        ('PHY', 'Phylogenetics'),
        ('NGS', 'Next Generation Sequencing'),
        ('UB', 'uBiome')
    ]
    project_type = models.CharField(max_length=3, choices=PROJECT_TYPES, default='phylogenetics')
    BARCODE_TYPES = [
        ('DNA', 'DNA'),
        ('mtDNA', 'mtDNA'),
        ('Viral', 'Viral'),
        ('rbcL', 'rbcL'),
        ('COI', 'COI'),
        ('16S', '16S'),
        ('ITS', 'ITS'),
        ('Other', 'Other')
    ]
    barcode_type = models.CharField(max_length=8, choices=BARCODE_TYPES, default='Other')
    READ_TYPES = [
        ('single', 'Single-End Reads'),
        ('paired', 'Paired-End Reads'),
    ]
    read_type = models.CharField(max_length=8, choices=READ_TYPES, default='single')
    public = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Ensure title is unique for the same user with a non-deleted project
        constraints = [
            models.UniqueConstraint(fields=['user', 'title'], condition=~models.Q(deleted=True),
                                    name='unique_non_deleted_title_per_user')
        ]

    def __str__(self):
        return self.title

class MetabarcodingFile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to='metabarcoding_files/')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class MetadataFile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to="metadata_files/")
    validated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class ProjectMetadataFile(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="metadata_files")
    metadata_file = models.ForeignKey(MetadataFile, on_delete=models.CASCADE, related_name="project_links")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("project", "metadata_file")

    def __str__(self):
        return f"{self.project.title} → {self.metadata_file.name}"


class DemuxJobInputFile(models.Model):
    job = models.ForeignKey("Job", on_delete=models.CASCADE, related_name="input_files")
    file = models.ForeignKey(MetabarcodingFile, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.file.name} for {self.job.uuid}"

class DemuxJobDetail(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="demux_detail")
    rand_samples = models.PositiveIntegerField(default=1000)
    def __str__(self):
        return f"Demux details for Job {self.job.uuid}"

class DemuxResult(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="demux_result")
    demux_qza = models.FileField(upload_to="demux_files/", blank=True, null=True)
    demux_summary_qzv = models.FileField(upload_to="demux_files/", blank=True, null=True)
    log_file = models.FileField(upload_to="demux_files/", blank=True, null=True)

    def __str__(self):
        return f"Results for Job {self.job.uuid}"

class Dada2JobDetail(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="dada2_detail")
    metadata_file = models.ForeignKey(MetadataFile, on_delete=models.CASCADE, related_name="dada2_details")
    paired = models.BooleanField(default=False)
    trimLeft = models.PositiveIntegerField(default=0)
    truncLen = models.PositiveIntegerField(default=0)
    trimLeftF = models.PositiveIntegerField(default=0)
    trimLeftR = models.PositiveIntegerField(default=0)
    truncLenF = models.PositiveIntegerField(default=0)
    truncLenR = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"DADA2 details for Job {self.job.uuid}"


class Dada2Result(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="dada2_result")
    rooted_tree_qza = models.FileField(upload_to="dada2_files/", blank=True, null=True)
    trim_table_qza = models.FileField(upload_to="dada2_files/", blank=True, null=True)
    rep_seqs_qza = models.FileField(upload_to="dada2_files/", blank=True, null=True)
    stats_qzv = models.FileField(upload_to="dada2_files/", blank=True, null=True)
    rep_seqs_qzv = models.FileField(upload_to="dada2_files/", blank=True, null=True)
    trim_table_qzv = models.FileField(upload_to="dada2_files/", blank=True, null=True)
    log_file = models.FileField(upload_to="dada2_files/", blank=True, null=True)

    def __str__(self):
        return f"DADA2 results for Job {self.job.uuid}"

class ProjectMetabarcodingFile(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="metabarcoding_files")
    metabarcoding_file = models.ForeignKey(MetabarcodingFile, on_delete=models.CASCADE, related_name="project_links")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("project", "metabarcoding_file")

    def __str__(self):
        return f"{self.project.title} → {self.metabarcoding_file.name}"

class PodFile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to='pod5_files/')

    def __str__(self):
        return self.name

class PronameImportJobDetail(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="proname_import_detail")
    forward_primer = models.CharField(max_length=64, default='')
    reverse_primer = models.CharField(max_length=64, default='')
    kit = models.CharField(max_length=64, default='')
    has_duplex = models.BooleanField(default=False)
    trim_adapters = models.BooleanField(default=False)
    trim_primers = models.BooleanField(default=False)

class PronameImportResult(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="proname_import_result")
    duplex_plot = models.FileField(upload_to="proname_import_files/", blank=True, null=True)
    simplex_plot = models.FileField(upload_to="proname_import_files/", blank=True, null=True)
    dual_plot = models.FileField(upload_to="proname_import_files/", blank=True, null=True)
    simplex_distribution = models.FileField(upload_to="proname_import_files/", blank=True, null=True)
    duplex_distribution = models.FileField(upload_to="proname_import_files/", blank=True, null=True)
    dual_distribution = models.FileField(upload_to="proname_import_files/", blank=True, null=True)
    simplex_reads = models.FileField(upload_to="proname_import_files/", blank=True, null=True)
    duplex_reads = models.FileField(upload_to="proname_import_files/", blank=True, null=True)
    dual_reads = models.FileField(upload_to="proname_import_files/", blank=True, null=True)

class PronameFilterJobDetail(models.Model):
    DATA_TYPES = [
        ('simplex', 'simplex'),
        ('duplex', 'duplex'),
        ('both', 'both')
    ]
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="proname_filter_detail")
    data_type = models.CharField(max_length=8, choices=DATA_TYPES, default='simplex')
    filt_min_length = models.PositiveIntegerField(default=1)
    filt_max_length = models.PositiveIntegerField(default=5000)
    filt_min_qual = models.PositiveIntegerField(default=15)

class PronameFilterResult(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="proname_filter_result")
    duplex_plot = models.FileField(upload_to="proname_filter_files/", blank=True, null=True)
    simplex_plot = models.FileField(upload_to="proname_filter_files/", blank=True, null=True)
    dual_plot = models.FileField(upload_to="proname_filter_files/", blank=True, null=True)
    simplex_distribution = models.FileField(upload_to="proname_filter_files/", blank=True, null=True)
    duplex_distribution = models.FileField(upload_to="proname_filter_files/", blank=True, null=True)
    dual_distribution = models.FileField(upload_to="proname_filter_files/", blank=True, null=True)
    simplex_reads = models.FileField(upload_to="proname_import_files/", blank=True, null=True)
    duplex_reads = models.FileField(upload_to="proname_import_files/", blank=True, null=True)
    dual_reads = models.FileField(upload_to="proname_import_files/", blank=True, null=True)

class PronameRefineJobDetail(models.Model):
    CHIMERA_DBS = [
        ('refseq207', 'refseq207'),
        ('greengenes2', 'greengenes2'),
        ('silva138', 'silva138'),
        ('regenb_full', 'regenb_full'),
        ('regenb_unique', 'regenb_unique')
    ]
    CLUSTERING_METHODS = [
        ('vsearch', 'vsearch'),
        ('mmseqs2', 'mmseqs2')
    ]
    MEDAKA_MODEL_LIST = ['r103_fast_g507', 'r103_fast_snp_g507', 'r103_fast_variant_g507', 'r103_hac_g507', 'r103_hac_snp_g507', 'r103_hac_variant_g507', 'r103_sup_g507', 'r103_sup_snp_g507', 'r103_sup_variant_g507', 'r1041_e82_260bps_fast_g632', 'r1041_e82_260bps_fast_variant_g632', 'r1041_e82_260bps_hac_g632', 'r1041_e82_260bps_hac_v4.0.0', 'r1041_e82_260bps_hac_v4.1.0', 'r1041_e82_260bps_hac_variant_g632', 'r1041_e82_260bps_hac_variant_v4.1.0', 'r1041_e82_260bps_joint_apk_ulk_v5.0.0', 'r1041_e82_260bps_sup_g632', 'r1041_e82_260bps_sup_v4.0.0', 'r1041_e82_260bps_sup_v4.1.0', 'r1041_e82_260bps_sup_variant_g632', 'r1041_e82_260bps_sup_variant_v4.1.0', 'r1041_e82_400bps_bacterial_methylation', 'r1041_e82_400bps_fast_g615', 'r1041_e82_400bps_fast_g632', 'r1041_e82_400bps_fast_variant_g615', 'r1041_e82_400bps_fast_variant_g632', 'r1041_e82_400bps_hac_g615', 'r1041_e82_400bps_hac_g632', 'r1041_e82_400bps_hac_v4.0.0', 'r1041_e82_400bps_hac_v4.1.0', 'r1041_e82_400bps_hac_v4.2.0', 'r1041_e82_400bps_hac_v4.3.0', 'r1041_e82_400bps_hac_v5.0.0', 'r1041_e82_400bps_hac_v5.0.0_rl_lstm384_dwells', 'r1041_e82_400bps_hac_v5.0.0_rl_lstm384_no_dwells', 'r1041_e82_400bps_hac_v5.2.0', 'r1041_e82_400bps_hac_v5.2.0_rl_lstm384_dwells', 'r1041_e82_400bps_hac_v5.2.0_rl_lstm384_no_dwells', 'r1041_e82_400bps_hac_variant_g615', 'r1041_e82_400bps_hac_variant_g632', 'r1041_e82_400bps_hac_variant_v4.1.0', 'r1041_e82_400bps_hac_variant_v4.2.0', 'r1041_e82_400bps_hac_variant_v4.3.0', 'r1041_e82_400bps_hac_variant_v5.0.0', 'r1041_e82_400bps_sup_g615', 'r1041_e82_400bps_sup_v4.0.0', 'r1041_e82_400bps_sup_v4.1.0', 'r1041_e82_400bps_sup_v4.2.0', 'r1041_e82_400bps_sup_v4.3.0', 'r1041_e82_400bps_sup_v5.0.0', 'r1041_e82_400bps_sup_v5.0.0_rl_lstm384_dwells', 'r1041_e82_400bps_sup_v5.0.0_rl_lstm384_no_dwells', 'r1041_e82_400bps_sup_v5.2.0', 'r1041_e82_400bps_sup_v5.2.0_rl_lstm384_dwells', 'r1041_e82_400bps_sup_v5.2.0_rl_lstm384_no_dwells', 'r1041_e82_400bps_sup_variant_g615', 'r1041_e82_400bps_sup_variant_v4.1.0', 'r1041_e82_400bps_sup_variant_v4.2.0', 'r1041_e82_400bps_sup_variant_v4.3.0', 'r1041_e82_400bps_sup_variant_v5.0.0', 'r104_e81_fast_g5015', 'r104_e81_fast_variant_g5015', 'r104_e81_hac_g5015', 'r104_e81_hac_variant_g5015', 'r104_e81_sup_g5015', 'r104_e81_sup_g610', 'r104_e81_sup_variant_g610', 'r941_e81_fast_g514', 'r941_e81_fast_variant_g514', 'r941_e81_hac_g514', 'r941_e81_hac_variant_g514', 'r941_e81_sup_g514', 'r941_e81_sup_variant_g514', 'r941_min_fast_g507', 'r941_min_fast_snp_g507', 'r941_min_fast_variant_g507', 'r941_min_hac_g507', 'r941_min_hac_snp_g507', 'r941_min_hac_variant_g507', 'r941_min_sup_g507', 'r941_min_sup_snp_g507', 'r941_min_sup_variant_g507', 'r941_prom_fast_g507', 'r941_prom_fast_snp_g507', 'r941_prom_fast_variant_g507', 'r941_prom_hac_g507', 'r941_prom_hac_snp_g507', 'r941_prom_hac_variant_g507', 'r941_prom_sup_g507', 'r941_prom_sup_snp_g507', 'r941_prom_sup_variant_g507', 'r941_sup_plant_g610', 'r941_sup_plant_variant_g610']
    MEDAKA_MODELS = [(m, m) for m in MEDAKA_MODEL_LIST]
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="proname_refine_detail")
    chimera_db = models.CharField(max_length=32, choices=CHIMERA_DBS, default='greengenes2')
    cluster_id = models.DecimalField(max_digits=3, decimal_places=2, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)])
    clustering_method = models.CharField(max_length=16, choices=CLUSTERING_METHODS, default='vsearch')
    medaka_model = models.CharField(max_length=64, choices=MEDAKA_MODELS, default='r1041_e82_400bps_sup_v5.2.0')
    metadata_file = models.ForeignKey(MetadataFile, on_delete=models.CASCADE, related_name="proname_refine_details")

class PronameRefineResult(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="proname_refine_result")
    rep_seqs_qza = models.FileField(upload_to="proname_refine_files/", blank=True, null=True)
    trim_table_qza = models.FileField(upload_to="proname_refine_files/", blank=True, null=True)
    rep_seqs_fasta = models.FileField(upload_to="proname_refine_files/", blank=True, null=True)
    rep_table_tsv = models.FileField(upload_to="proname_refine_files/", blank=True, null=True)
    rooted_tree_qza = models.FileField(upload_to="proname_refine_files/", blank=True, null=True)

class PronameTaxonomyJobDetail(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="proname_taxonomy_detail")
    metadata_file = models.ForeignKey(MetadataFile, on_delete=models.SET_NULL, null=True, related_name="proname_taxonomy_details")
    assay = models.CharField(max_length=64, default='')

class PronameTaxonomyResult(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="proname_taxonomy_result")
    rooted_tree_qza = models.FileField(upload_to="proname_taxonomy_files/", blank=True, null=True)
    taxonomy_qza = models.FileField(upload_to="proname_taxonomy_files/", blank=True, null=True)
    taxa_bar_plots = models.FileField(upload_to="proname_taxonomy_files/", blank=True, null=True)

class NanoporeSequence(models.Model):
    SOURCE_CHOICES = [
        ('upload', 'Upload'),
        ('proname_import', 'Proname Import'),
    ]
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='upload')
    proname_import_result = models.ForeignKey(PronameImportResult, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to='fastq_files/')

    def __str__(self):
        return self.name

class UserNanoporeSequence(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    nanopore_sequence = models.ForeignKey(NanoporeSequence, on_delete=models.CASCADE)

    class Meta:
        unique_together = ('user', 'nanopore_sequence')

    def __str__(self):
        return f"User {self.user_id} - Sequence {self.nanopore_sequence.name}"

class DataFile(models.Model):
    # Foreign key for the user who uploaded the file
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    # Sequence name
    name = models.CharField(max_length=255)

    # Sequence reads
    reads = models.TextField()
    left_trim = models.TextField(default='')
    right_trim = models.TextField(default='')

    # Boolean indicating if a trace file exists
    trace_exists = models.BooleanField(default=False)

    # Boolean indicating if it's a consensus read
    is_consensus = models.BooleanField(default=False)

    # Foreign keys for forward and reverse reads if it's a consensus
    forward_read = models.ForeignKey('self', on_delete=models.CASCADE, related_name='consensus_forward', null=True, blank=True)
    reverse_read = models.ForeignKey('self', on_delete=models.CASCADE, related_name='consensus_reverse', null=True, blank=True)

    # Fields for storing trim information
    trim_start = models.IntegerField(null=True, blank=True)
    trim_end = models.IntegerField(null=True, blank=True)

    # Field indicating if it's a forward, reverse, or consensus read
    READ_CHOICES = [
        ('F', 'Forward'),
        ('R', 'Reverse'),
        ('C', 'Consensus'),
        ('E', 'Reference'),
        ('B', 'BLAST Hit'),
    ]
    read_type = models.CharField(max_length=1, choices=READ_CHOICES)

    SOURCE_CHOICES = [
        ('sample', 'Sample'),
        ('reference', 'Reference'),
        ('bold', 'BOLD'),
        ('genbank', 'GenBank'),
        ('hit', 'Hit'),
        ('upload', 'Upload'),
        ('import', 'Import'),
        ('paste', 'Paste'),
        ('saved', 'Saved'),
        ('nanopore', 'Nanopore'),
        ('consensus', 'Consensus'),
    ]

    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='upload')
    process_id = models.CharField(max_length=100, null=True, blank=True)
    accession_number = models.CharField(max_length=100, null=True, blank=True)
    order_id = models.IntegerField(null=True, blank=True)
    source_file_id = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    nanopore_seq_id = models.ForeignKey(NanoporeSequence, on_delete=models.SET_NULL, null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Ensure certain fields are set only if source is appropriate
        if self.source == 'reference' and not self.reference_data:
            raise ValueError("reference_data must be set if source is 'reference'")
        if self.source == 'sample' and not self.sample_data:
            raise ValueError("sample_data must be set if source is 'sample'")
        if self.source == 'bold' and not self.process_id:
            raise ValueError("process_id must be set if source is 'bold'")
        if self.source == 'genbank' and not self.accession_number:
            raise ValueError("accession_number must be set if source is 'genbank'")
        if self.source == 'hit' and not self.accession_number and not self.source_file_id:
            raise ValueError("accession_number and source_file_id must be set if source is 'hit'")
        if self.source == 'import' and not self.order_id:
            raise ValueError("order_id must be set if source is 'import'")
        if self.source == 'saved' and not self.source_file_id:
            raise ValueError("source_file_id must be set if source is 'saved'")

        # Call the parent class's save method
        super(DataFile, self).save(*args, **kwargs)

    # Associated ABI file (if uploaded)
    associated_abi = models.FileField(upload_to='abi_files/', null=True, blank=True)

    # Associated FASTA file
    associated_fasta = models.FileField(upload_to='fasta_files/', null=True, blank=True)

    # Boolean indicating if the file is public
    is_public = models.BooleanField(default=False)

    # Boolean indicating if the file is in the sequence repository
    in_sequence_repository = models.BooleanField(default=False)

    # Boolean indicating if the file was exported to GenBank
    exported = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    # Reference to the original multi-sequence FASTA file
    reference_data = models.ForeignKey(ReferenceData, on_delete=models.SET_NULL, null=True, blank=True)

    # Reference to the original multi-sequence FASTA or ABI file
    sample_data = models.ForeignKey(SampleData, on_delete=models.SET_NULL, null=True, blank=True)

    # Method to get the associated ABI file name
    def get_associated_abi_filename(self):
        if self.associated_abi:
            return self.associated_abi.name
        return None

class ProjectDataFile(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    data_file = models.ForeignKey(DataFile, on_delete=models.CASCADE)

    class Meta:
        unique_together = ('project', 'data_file')

    def __str__(self):
        return f"Project: {self.project}, DataFile: {self.data_file}"


class Job(models.Model):
    STATUS_CHOICES = [
        ('STARTING', 'System instance booting'),
        ('FAILED_BOOT', 'System instance booting failed'),
        ('PENDING', 'Job processing beginning'),
        ('PROCESSING_INPUTS', 'Identifying input files for staging'),
        ('STAGING_INPUTS', 'Transferring job input data to execution system'),
        ('STAGING_JOB', 'Staging runtime assets to execution system'),
        ('SUBMITTING_JOB', 'Submitting job to execution system'),
        ('QUEUED', 'Job queued to execution system queue'),
        ('RUNNING', 'Job running on execution system'),
        ('ARCHIVING', 'Transferring job output to archive system'),
        ('BLOCKED', 'Job blocked'),
        ('PAUSED', 'Job processing suspended'),
        ('FINISHING', 'Getting job output files'),
        ('FINISHED', 'Job completed successfully'),
        ('CANCELLED', 'Job execution intentionally stopped'),
        ('FAILED', 'Job failed'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True)
    appId = models.CharField(max_length=255)
    uuid = models.CharField(max_length=40, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    primary = models.BooleanField(default=False)

    def __str__(self):
        return f"Job {self.uuid} - {self.status}"

class RarefactionJobDetail(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="rarefaction_detail")
    dada2_job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="rarefaction_jobs")
    minDepth = models.PositiveIntegerField(default=0)
    maxDepth = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Rarefaction details for Job {self.job.uuid}"


class RarefactionResult(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="rarefaction_result")
    alpha_rarefaction_qzv = models.FileField(upload_to="rarefaction_files/", blank=True, null=True)
    log_file = models.FileField(upload_to="rarefaction_files/", blank=True, null=True)

    def __str__(self):
        return f"Rarefaction results for Job {self.job.uuid}"

class CoreMetricsJobDetail(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="coremetrics_detail")
    dada2_job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="coremetrics_jobs")
    sdepth = models.PositiveIntegerField(default=0)
    classifier = models.CharField(max_length=255)

    def __str__(self):
        return f"Core Metrics details for Job {self.job.uuid}"

class CoreMetricsResult(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="coremetrics_result")

    taxonomy_qza = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)

    # Each core metric qzv
    bray_curtis_bioenv = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    bray_curtis_emperor = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    evenness_correlation = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    evenness_group_significance = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    faith_pd_correlation = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    faith_pd_group_significance = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    observed_features_correlation = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    observed_features_group_significance = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    shannon_correlation = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    shannon_group_significance = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    jaccard_emperor = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    taxa_bar_plots = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    taxonomy_qzv = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    unweighted_unifrac_bioenv = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    unweighted_unifrac_emperor = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)
    weighted_unifrac_emperor = models.FileField(upload_to="coremetrics_files/", blank=True, null=True)

    def __str__(self):
        return f"CoreMetrics results for Job {self.job.uuid}"

class GneissJobDetail(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="gneiss_detail")
    coremetrics_job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="gneiss_jobs")
    category = models.CharField(max_length=36)
    formula = models.CharField(max_length=512)
    taxalevel = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Gneiss details for Job {self.job.uuid}"

class GneissResult(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="gneiss_result")

    heatmap = models.FileField(upload_to="gneiss_files/", blank=True, null=True)

    def __str__(self):
        return f"Gneiss results for Job {self.job.uuid}"

class AncomJobDetail(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="ancom_detail")
    coremetrics_job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="ancom_jobs")
    category = models.CharField(max_length=36)
    formula = models.CharField(max_length=512)
    taxalevel = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Ancom details for Job {self.job.uuid}"

class AncomResult(models.Model):
    job = models.OneToOneField("Job", on_delete=models.CASCADE, related_name="ancom_result")

    heatmap = models.FileField(upload_to="ancom_files/", blank=True, null=True)
    abundance_barplot = models.FileField(upload_to="ancom_files/", blank=True, null=True)
    ancom = models.FileField(upload_to="ancom_files/", blank=True, null=True)
    differentials = models.FileField(upload_to="ancom_files/", blank=True, null=True)

    def __str__(self):
        return f"Ancom results for Job {self.job.uuid}"

class JobPodFile(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    podfile = models.ForeignKey(PodFile, on_delete=models.CASCADE)


class BasecallingJob(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    model = models.CharField(max_length=255)
    output_name = models.CharField(max_length=255)
    kit_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

class TrimJob(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    data_file = models.ForeignKey(DataFile, on_delete=models.CASCADE)

    def __str__(self):
        return f"Job: {self.job}, DataFile: {self.data_file}"

class ConsensusJob(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    forward_file = models.ForeignKey(DataFile, on_delete=models.CASCADE, related_name='forward_file_consensus_job')
    reverse_file = models.ForeignKey(DataFile, on_delete=models.CASCADE, related_name='reverse_file_consensus_job')

class ConsensusData(models.Model):
    consensus = models.ForeignKey(DataFile, on_delete=models.CASCADE)
    forward_align = models.TextField()
    reverse_align = models.TextField()

class ProjectBlastDone(models.Model):
    project_data_file = models.ForeignKey(ProjectDataFile, on_delete=models.CASCADE)
    clade = models.CharField(max_length=8, default='default')

class BlastJob(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    data_file = models.ForeignKey(DataFile, on_delete=models.CASCADE)
    clade = models.CharField(max_length=8, default='default')

class BlastData(models.Model):
    blast_file = models.ForeignKey(DataFile, on_delete=models.CASCADE)

class BlastResult(models.Model):
    blast_data = models.ForeignKey(BlastData, on_delete=models.CASCADE)
    accession = models.CharField(max_length=16)
    details = models.TextField()
    length = models.CharField(max_length = 5)
    evalue = models.CharField(max_length = 32)
    mismatches = models.CharField(max_length = 5)
    sequence = models.TextField()
    bitscore = models.CharField(max_length = 8)

class MuscleJob(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    associated_input = models.FileField(upload_to='muscle_files/', null=True, blank=True)

class MuscleFile(models.Model):
    muscle_job = models.ForeignKey(MuscleJob, on_delete=models.CASCADE)
    data_file = models.ForeignKey(DataFile, on_delete=models.CASCADE)

class MuscleData(models.Model):
    muscle_job = models.ForeignKey(MuscleJob, on_delete=models.CASCADE)
    associated_alignment = models.FileField(upload_to='alignment_files/', null=True, blank=True)
    original_associated_alignment = models.FileField(upload_to='alignment_files/', null=True, blank=True)


class MuscleTrim(models.Model):
    muscle_data = models.OneToOneField(MuscleData, on_delete=models.CASCADE, related_name='trim')
    left_trim = models.IntegerField(default=0)
    right_trim = models.IntegerField(default=0)

class MuscleSequence(models.Model):
    muscle_data = models.ForeignKey(MuscleData, on_delete=models.CASCADE, related_name='sequences')
    name = models.CharField(max_length=255)
    bases = models.TextField()

class MuscleConservation(models.Model):
    muscle_data = models.ForeignKey(MuscleData, on_delete=models.CASCADE, related_name='conservations')
    position = models.IntegerField()
    value = models.FloatField()

class MuscleVariation(models.Model):
    muscle_data = models.ForeignKey(MuscleData, on_delete=models.CASCADE, related_name='variations')
    position = models.IntegerField()
    variations = models.CharField(max_length=8)

class MuscleConsensus(models.Model):
    muscle_data = models.OneToOneField(MuscleData, on_delete=models.CASCADE, related_name='consensus')
    sequence = models.TextField()

class MuscleSimilarity(models.Model):
    muscle_data = models.ForeignKey(MuscleData, on_delete=models.CASCADE, related_name='similarities')
    sequence1 = models.CharField(max_length=255)
    sequence2 = models.CharField(max_length=255)
    similarity_percentage = models.FloatField()

class PhylipNJJob(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    muscle_data = models.ForeignKey(MuscleData, on_delete=models.CASCADE)
    outgroup = models.CharField(max_length=255)

class PhylipNJData(models.Model):
    phylipnj_job = models.ForeignKey(PhylipNJJob, on_delete=models.CASCADE)
    outtree = models.TextField()

class PhylipMLJob(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    muscle_data = models.ForeignKey(MuscleData, on_delete=models.CASCADE)
    outgroup = models.CharField(max_length=255)

class PhylipMLData(models.Model):
    phylipml_job = models.ForeignKey(PhylipMLJob, on_delete=models.CASCADE)
    outtree = models.TextField()

class NanoporeSampleSet(models.Model):
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=64, default='')
    directory = models.CharField(max_length=500)

    def __str__(self):
        return self.name

class ProjectNanoporeSequence(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    nanopore_sequence = models.ForeignKey(NanoporeSequence, on_delete=models.CASCADE)

    def __str__(self):
        return f"Project {self.project_id} - Sequence {self.nanopore_sequence.name}"

class FastpJob(models.Model):
    nanopore_sequence = models.ForeignKey(NanoporeSequence, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=(('queued', 'Queued'), ('completed', 'Completed'), ('failed', 'Failed')), default='queued')

    #class Meta:
    #    unique_together = ('nanopore_sequence', 'project')

    def __str__(self):
        return f"FastpJob for {self.nanopore_sequence.name} in project {self.project.title}"

class FastpResult(models.Model):
    project_nanopore_sequence = models.ForeignKey(ProjectNanoporeSequence, on_delete=models.CASCADE)
    filtered_file = models.FileField(upload_to='fastp_output/')
    json_file = models.FileField(upload_to='fastp_output/')
    html_file = models.FileField(upload_to='fastp_output/')
    processed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Fastp Result for {self.project_nanopore_sequence.nanopore_sequence.name}"

class PorechopJob(models.Model):
    nanopore_sequence = models.ForeignKey(NanoporeSequence, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default='pending')  # e.g., pending, running, completed, failed
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class PorechopResult(models.Model):
    project_nanopore_sequence = models.ForeignKey(ProjectNanoporeSequence, on_delete=models.CASCADE)
    chopped_file = models.FileField(upload_to='porechop_output/')
    html_log_file = models.FileField(upload_to='porechop_output/')  # Store the HTML log
    processed_at = models.DateTimeField(auto_now_add=True)

class MedakaJob(models.Model):
    nanopore_sequence = models.ForeignKey(NanoporeSequence, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default='pending')  # pending, completed, failed
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class MedakaResult(models.Model):
    project_nanopore_sequence = models.ForeignKey(ProjectNanoporeSequence, on_delete=models.CASCADE)
    fasta_file = models.FileField(upload_to='medaka_files/')
    fasta_file_medaka_headers = models.FileField(upload_to='medaka_files/')
    medaka_output_dir = models.FilePathField(path='medaka_output/', match='.*', recursive=True)  # Updated to FilePathField
    processed_at = models.DateTimeField(default=timezone.now)

class Specimen(models.Model):
    """Model to Store Specimen Details."""
    datafile = models.OneToOneField('DataFile', on_delete=models.CASCADE, related_name='specimen')
    codon = models.CharField(max_length=255, null=True, blank=True)
    institution_storing = models.CharField(max_length=255, null=True, blank=True)
    identifier_name = models.CharField(max_length=255, null=True, blank=True)
    identifier_email = models.EmailField(null=True, blank=True)
    genus = models.CharField(max_length=255, null=True, blank=True)
    species = models.CharField(max_length=255, null=True, blank=True)
    date_collected = models.DateField(null=True, blank=True)
    country = models.CharField(max_length=255, null=True, blank=True)
    state_province = models.CharField(max_length=255, null=True, blank=True)
    city = models.CharField(max_length=255, null=True, blank=True)
    habitat = models.CharField(max_length=255, null=True, blank=True)
    exact_site = models.TextField(null=True, blank=True)
    isolation_source = models.TextField(null=True, blank=True)
    sample_collected_from_host = models.BooleanField(default=False)
    host_organism_name = models.CharField(max_length=255, null=True, blank=True)
    latitude = models.TextField(null=True, blank=True)
    longitude = models.TextField(null=True, blank=True)
    altitude = models.FloatField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    sex = models.CharField(max_length=50, null=True, blank=True)
    reproduction = models.CharField(max_length=50, null=True, blank=True)
    life_stage = models.CharField(max_length=50, null=True, blank=True)
    primer_used = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"Specimen {self.genus} {self.species} - Collected {self.date_collected}"


class Author(models.Model):
    datafile = models.ForeignKey('DataFile', on_delete=models.CASCADE, related_name='authors')
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    affiliation = models.CharField(max_length=255)
    specimen = models.ForeignKey(Specimen, on_delete=models.CASCADE)
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.affiliation}) (DataFile ID {self.datafile.id})"

class SequenceRepository(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_public = models.BooleanField(default=False)

    def toggle_visibility(self):
        self.is_public = not self.is_public
        self.save()

    def __str__(self):
        return self.name

class DataFolder(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)

    class Meta:
        unique_together = ('user', 'name')

class SangerSequenceFolder(models.Model):
    datafile = models.ForeignKey('DataFile', on_delete=models.CASCADE)
    datafolder = models.ForeignKey('DataFolder', on_delete=models.CASCADE)

    class Meta:
        unique_together = ('datafile', 'datafolder')

class NanoporeSequenceFolder(models.Model):
    usernanoporesequence = models.ForeignKey('UserNanoporeSequence', on_delete=models.CASCADE)
    datafolder = models.ForeignKey('DataFolder', on_delete=models.CASCADE)

    class Meta:
        unique_together = ('usernanoporesequence', 'datafolder')

class BlastCache(models.Model):
    db = models.CharField(max_length=255)  # e.g., path or name of BLAST DB used
    read_hash = models.CharField(max_length=64, db_index=True)  # hash of DataFile.reads
    job = models.ForeignKey('Job', on_delete=models.CASCADE)
    blast_job = models.ForeignKey('BlastJob', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
