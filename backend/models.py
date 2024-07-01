from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
import secrets

class Ethnicity(models.Model):
    native = models.BooleanField(default=False)
    asian = models.BooleanField(default=False)
    black = models.BooleanField(default=False)
    hispanic = models.BooleanField(default=False)
    pacific = models.BooleanField(default=False)
    white = models.BooleanField(default=False)
    other = models.BooleanField(default=False)
    unanswered = models.BooleanField(default=False)

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    country = models.CharField(max_length=2)
    postal_code = models.CharField(max_length=12)
    GENDER_CHOICES = (
        ('m', 'Male'),
        ('f', 'Female'),
        ('n', 'Non-Binary/Other'),
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

    def __str__(self):
        return f"{self.user.username}'s profile"

class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    @classmethod
    def create_token(cls, user):
        token = secrets.token_hex(32)
        expiration_time = timezone.now() + timezone.timedelta(hours=1)  # Token expires in 1 hour
        return cls.objects.create(user=user, token=token, expires_at=expiration_time)

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

class DataFile(models.Model):
    # Foreign key for the user who uploaded the file
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    # Sequence name
    name = models.CharField(max_length=255)

    # Sequence reads
    reads = models.TextField()

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
    ]
    read_type = models.CharField(max_length=1, choices=READ_CHOICES)

    # Associated ABI file (if uploaded)
    associated_abi = models.FileField(upload_to='abi_files/', null=True, blank=True)

    # Associated FASTA file
    associated_fasta = models.FileField(upload_to='fasta_files/', null=True, blank=True)

    # Boolean indicating if the file is public
    is_public = models.BooleanField(default=False)

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
