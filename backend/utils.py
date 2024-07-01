from Bio import SeqIO
from io import BytesIO, StringIO
from urllib.request import urlopen
import os
import re

def parse_reads(file_url):
    message = None
    sequence = None
    trace_exists = False
    returned_record = False
    # Open the URL and read the content
    with urlopen(file_url) as response:
        file_content = response.read()

    try:
        # Try parsing as AB1
        for record in SeqIO.parse(BytesIO(file_content), "abi"):
            sequence = str(record.seq)
            trace_exists = True
            returned_record = record
    except (OSError, ValueError):
        # Parsing as AB1 failed, try parsing as FASTQ
        try:
            records = list(SeqIO.parse(StringIO(file_content), "fastq"))
            if len(records) == 1:
                seq_record = records[0]
                sequence = str(seq_record.seq)
                returned_record = seq_record
            else:
                message = "Error: FASTQ file must contain exactly one sequence."
        except (OSError, ValueError):
            # Parsing as FASTQ failed, try parsing as FASTA
            try:
                records = list(SeqIO.parse(StringIO(file_content), "fasta"))
                if len(records) == 1:
                    seq_record = records[0]
                    sequence = str(seq_record.seq)
                    returned_record = seq_record
                else:
                    message = "Error: FASTA file must contain exactly one sequence."
            except (OSError, ValueError):
                message = "Error: Unsupported file format."

    return message, sequence, trace_exists, returned_record

def cleanSequenceName(name):
    primerRe = r'M13([FR])(?:_-21_)?(_R)?_(?:\w\d+)'
    # Remove file extension and anything before a slash
    sequenceName = os.path.splitext(os.path.basename(name))[0]
    # Remove whitepace
    sequenceName = re.sub(r'\s+', r'', sequenceName)
    # Replace invalid characters []().:; with _
    sequenceName = re.sub(r'[\[\]\(\)\.:;]+', r'_', sequenceName)
    # Remove primer
    sequenceName = re.sub(primerRe, r'\1\2', sequenceName)
    # Replace multiple underscores with single underscore
    sequenceName = re.sub(r'_+', r'_', sequenceName)
    return sequenceName
