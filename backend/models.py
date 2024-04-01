from django.contrib.auth.models import User
from django.db import models

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
