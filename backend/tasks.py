from Bio import SeqIO, AlignIO
from Bio.Seq import Seq
from Bio.motifs import Motif
from Bio.Align.AlignInfo import SummaryInfo
from collections import Counter
import os
import re
import subprocess
from celery import shared_task
from ansi2html import Ansi2HTMLConverter
from django.utils import timezone
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from .models import FastpJob, FastpResult, ProjectNanoporeSequence, PorechopResult, PorechopJob, MedakaJob, MedakaResult, DataFile, ProjectDataFile, BlastJob, BlastResult, Job, BlastData, MuscleJob, MuscleFile, MuscleData, MuscleSequence, MuscleConservation, MuscleVariation, MuscleConsensus, MuscleSimilarity, PhylipNJJob, PhylipNJData, PhylipMLJob, PhylipMLData
from django.conf import settings
import tempfile

@shared_task
def run_fastp_task(fastp_job_id):
    # Retrieve the FastpJob
    fastp_job = FastpJob.objects.get(id=fastp_job_id)
    project_nanopore_sequence = ProjectNanoporeSequence.objects.get(
        nanopore_sequence=fastp_job.nanopore_sequence,
        project=fastp_job.project
    )

    nanopore_sequence_file = project_nanopore_sequence.nanopore_sequence.file.name

    # Use the ID of the ProjectNanoporeSequence for unique filenames
    file_base_name = str(project_nanopore_sequence.id)

    # Output files based on the ProjectNanoporeSequence ID
    output_dir = 'fastp_output'
    os.makedirs(output_dir, exist_ok=True)

    filtered_file = os.path.join(output_dir, f'{file_base_name}.fastq.gz')
    json_file = os.path.join(output_dir, f'{file_base_name}.json')
    html_file = os.path.join(output_dir, f'{file_base_name}.html')

    # Run the fastp command
    fastp_command = [
        'fastp',
        '-i', nanopore_sequence_file,
        '-o', filtered_file,
        '-j', json_file,
        '-h', html_file
    ]
    try:
        subprocess.run(fastp_command, check=True)

        # Mark the job as completed
        fastp_job.status = 'completed'
        fastp_job.save()

        # Save the fastp result
        FastpResult.objects.create(
            project_nanopore_sequence=project_nanopore_sequence,
            filtered_file=f'fastp_output/{file_base_name}.fastq.gz',
            json_file=f'fastp_output/{file_base_name}.json',
            html_file=f'fastp_output/{file_base_name}.html'
        )

    except subprocess.CalledProcessError:
        # Log failure or handle as necessary
        fastp_job.status = 'failed'
        fastp_job.save()

@shared_task
def run_porechop_task(project_nanopore_sequence_id, filtered_file_path):
    output_dir = 'porechop_output'
    os.makedirs(output_dir, exist_ok=True)
    # Output files based on the ProjectNanoporeSequence ID
    try:
        # Prepare output paths for chopped file and HTML log file
        chopped_file = f"porechop_output/{project_nanopore_sequence_id}.fastq"
        log_file = f"porechop_output/{project_nanopore_sequence_id}.log"
        html_log_file = f"porechop_output/{project_nanopore_sequence_id}.html"

        # Run the porechop command and capture the ANSI output in the log file
        command = f"porechop -i {filtered_file_path} -o {chopped_file} > {log_file}"
        with open(log_file, 'w') as log:
            subprocess.run(command, shell=True, check=True, stdout=log, stderr=subprocess.STDOUT)

        # Convert ANSI log to HTML and save it
        with open(log_file, 'r') as log:
            ansi_log_content = log.read()
        
        conv = Ansi2HTMLConverter()
        html_content = conv.convert(ansi_log_content)

        with open(html_log_file, 'w') as html_log:
            html_log.write(html_content)

        # Delete the original log file after conversion to HTML
        if os.path.exists(log_file):
            os.remove(log_file)

        # Save the result to the PorechopResult table
        project_nanopore_sequence = ProjectNanoporeSequence.objects.get(id=project_nanopore_sequence_id)
        PorechopResult.objects.create(
            project_nanopore_sequence=project_nanopore_sequence,
            chopped_file=chopped_file,
            html_log_file=html_log_file,  # Store HTML log
            processed_at=timezone.now()
        )

        # Update the job status to completed
        job = PorechopJob.objects.get(project=project_nanopore_sequence.project, nanopore_sequence=project_nanopore_sequence.nanopore_sequence, status='pending')
        job.status = 'completed'
        job.save()

    except subprocess.CalledProcessError as e:
        # Update the job status to failed if something goes wrong
        project_nanopore_sequence = ProjectNanoporeSequence.objects.get(id=project_nanopore_sequence_id)
        job = PorechopJob.objects.get(project=project_nanopore_sequence.project, nanopore_sequence=project_nanopore_sequence.nanopore_sequence, status='pending')
        job.status = 'failed'
        job.save()

@shared_task
def run_medaka_task(project_nanopore_sequence_id):
    try:
        # Get the PorechopResult object
        porechop_result = PorechopResult.objects.get(project_nanopore_sequence_id=project_nanopore_sequence_id)
        chopped_file_path = porechop_result.chopped_file.path

        # Prepare file paths for the fasta and medaka output
        fasta_file_path = chopped_file_path.replace('.fastq', '.fasta')
        fasta_file_medaka_headers_path = fasta_file_path.replace('.fasta', '_medaka_headers.fasta')
        medaka_output_dir = f"medaka_output/{project_nanopore_sequence_id}/"
        consensus_fasta_path = os.path.join(medaka_output_dir, 'consensus.fasta')

        # Ensure output directory exists
        os.makedirs(medaka_output_dir, exist_ok=True)

        # Step 1: Convert fastq to fasta
        command1 = f"cat {chopped_file_path} | awk '{{if(NR%4==1) {{printf(\">%s\\n\",substr($0,2));}} else if(NR%4==2) print;}}' > {fasta_file_path}"
        subprocess.run(command1, shell=True, check=True)

        # Step 2: Modify headers for medaka
        command2 = f"cat {fasta_file_path} | sed s/'^>'/'>amp_rep'/g > {fasta_file_medaka_headers_path}"
        subprocess.run(command2, shell=True, check=True)

        # Step 3: Run medaka
        command3 = f"medaka smolecule --length 0 {medaka_output_dir} {fasta_file_medaka_headers_path}"
        #command3 = f"medaka smolecule --length 250 {medaka_output_dir} {fasta_file_medaka_headers_path}"
        subprocess.run(command3, shell=True, check=True)

        # Save MedakaResult
        project_nanopore_sequence = ProjectNanoporeSequence.objects.get(id=project_nanopore_sequence_id)
        MedakaResult.objects.create(
            project_nanopore_sequence=project_nanopore_sequence,
            fasta_file=fasta_file_path,
            fasta_file_medaka_headers=fasta_file_medaka_headers_path,
            medaka_output_dir=medaka_output_dir,  # Store directory path
            processed_at=timezone.now()
        )
        # Extract name and reads from the consensus.fasta file
        sequences = list(SeqIO.parse(consensus_fasta_path, 'fasta'))
        total_sequences = len(sequences)
        num_digits = len(str(total_sequences))

        base_name = project_nanopore_sequence.nanopore_sequence.name
        for idx, record in enumerate(sequences, start=1):
            name = f"{base_name}_{str(idx).zfill(num_digits)}_{record.description.split()[-1]}" if total_sequences > 1 else base_name
            reads = str(record.seq)
            # Create a DataFile instance for the consensus FASTA
            data_file = DataFile.objects.create(
                user=project_nanopore_sequence.project.user,
                name=name,  # Extracted name from FASTA file
                reads=reads,  # Extracted reads from FASTA file
                is_consensus=True,
                read_type='C',
                source='nanopore',
                nanopore_seq_id=project_nanopore_sequence.nanopore_sequence,
            )
            new_filename = f"{data_file.id}.fasta"
            header = re.sub(r'\W+', '_', data_file.name)
            fasta_content = ContentFile(f">{header}\n{data_file.reads}\n")
            new_fasta_file_path = default_storage.save(f"fasta_files/{new_filename}", fasta_content)
            data_file.associated_fasta.name = new_fasta_file_path
            data_file.save()
            # Create a ProjectDataFile instance
            project_data_file = ProjectDataFile.objects.create(
                project=project_nanopore_sequence.project,
                data_file=data_file
            )


        # Update the job status to completed
        job = MedakaJob.objects.get(project=project_nanopore_sequence.project, nanopore_sequence=project_nanopore_sequence.nanopore_sequence, status='pending')
        job.status = 'completed'
        job.save()

    except subprocess.CalledProcessError as e:
        # Update the job status to failed if something goes wrong
        project_nanopore_sequence = ProjectNanoporeSequence.objects.get(id=project_nanopore_sequence_id)
        job = MedakaJob.objects.get(project=project_nanopore_sequence.project, nanopore_sequence=project_nanopore_sequence.nanopore_sequence, status='pending')
        job.status = 'failed'
        job.save()

@shared_task
def run_blast_task(job_id, file_path, clade):
    try:
        # Fetch the associated Job instance
        job = Job.objects.get(id=job_id)
        job.status = "RUNNING"
        job.save()

        # Set up BLAST environment and command
        db = settings.SPECIES_MAP.get(clade, settings.SPECIES_MAP["default"])
        my_env = os.environ.copy()
        my_env["BLASTDB"] = settings.BLASTDB
        blastn_program = settings.BLASTN_PROGRAM

        # Construct the BLAST command as a list of arguments
        blastn_command = [
            blastn_program, "-task", "blastn", "-num_threads", "4", "-max_target_seqs", "50",
            "-evalue", "1e-10", "-word_size", "11", "-reward", "2", "-penalty", "-3",
            "-show_gis", "-dust", "no", "-db", db, "-query", file_path, 
            "-outfmt", "6 saccver stitle length bitscore evalue mismatch sseq"
        ]

        # Run the command using subprocess.run()
        result = subprocess.run(
            blastn_command, env=my_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=False
        )

        # Check for command errors
        if result.returncode != 0:
            job.status = "FAILED"
            #job.message = result.stderr
            job.save()
            return {
                "status": "error",
                "message": result.stderr
            }

        # Process the results
        job.status = "ARCHIVING"
        job.save()

        # Get the associated BlastJob instance
        blast_job = BlastJob.objects.get(job=job)

        results = []
        for line in result.stdout.splitlines():
            result_list = line.split("\t")
            results.append({
                "accession": result_list[0] if len(result_list) > 0 else "",
                "details": result_list[1] if len(result_list) > 1 else "",
                "length": result_list[2] if len(result_list) > 2 else "",
                "bitscore": result_list[3] if len(result_list) > 3 else "",
                "evalue": result_list[4] if len(result_list) > 4 else "",
                "mismatches": result_list[5] if len(result_list) > 5 else "",
                "sequence": result_list[6] if len(result_list) > 6 else "",
            })


        blast_data = BlastData.objects.create(
            blast_file = blast_job.data_file
        )

        # Save results to the database
        for result in results:
            BlastResult.objects.create(
                blast_data=blast_data,
                accession=result["accession"],
                details=result["details"],
                length=result["length"],
                evalue=result["evalue"],
                mismatches=result["mismatches"],
                sequence=result["sequence"],
                bitscore=result["bitscore"]
            )

        # Update job status to "FINISHED" if successful
        job.status = "FINISHED"
        #job.message = f"{len(results)} results found" if results else "No results"
        job.save()

        return {
            "status": "success",
            "message": f"Blast job completed with {len(results)} results."
        }

    except Exception as e:
        # Handle exceptions and mark the job as "FAILED"
        if job:
            job.status = "FAILED"
            #job.message = str(e)
            job.save()
        return {
            "status": "error",
            "message": str(e)
        }

VALID_BASES = {'A', 'T', 'G', 'C', 'N'}


def get_trimmed_range(seq):
    """Return start and end indexes (exclusive) that exclude leading and trailing '-' characters."""
    seq_str = str(seq)
    start = 0
    end = len(seq_str)

    while start < end and seq_str[start] == '-':
        start += 1
    while end > start and seq_str[end - 1] == '-':
        end -= 1

    return start, end

def pairwise_trimmed_similarity(seq_a, seq_b):
    """Align shorter (trimmed) sequence to same window in longer sequence and compare"""
    a_start, a_end = get_trimmed_range(seq_a)
    b_start, b_end = get_trimmed_range(seq_b)

    a_span = a_end - a_start
    b_span = b_end - b_start

    if a_span <= b_span:
        shorter, longer = seq_a, seq_b
        start, end = a_start, a_end
    else:
        shorter, longer = seq_b, seq_a
        start, end = b_start, b_end

    trimmed_len = end - start
    if trimmed_len <= 0:
        return "N/A"

    match_count = 0
    gaps = 0
    for i in range(trimmed_len):
        base_s = shorter[start + i].upper()
        base_l = longer[start + i].upper()
        if base_s == "-" or base_l == "-":
            gaps += 1
        if base_s in VALID_BASES and base_s == base_l:
            match_count += 1

    denominator = trimmed_len - gaps
    if denominator <= 0:
        return "N/A"

    similarity = match_count / denominator * 100
    return '{:.2f}'.format(round(similarity, 2))

def calculate_conservation_and_variation(msa, consensus):
    bases = ['A', 'C', 'T', 'G', 'N']
    conservations = []
    variations = []
    sequences = {}
    matches = {}
    total_sequences = len(msa)
    MAX_SEQUENCES_FOR_SIMILARITY = 50

    if total_sequences <= MAX_SEQUENCES_FOR_SIMILARITY:
        matches["consensus"] = {"consensus": "-"}

    for record in msa:
        sequences[record.id] = []

    for base_location in range(len(consensus)):
        variation = []
        conservation = 1.0
        base = consensus[base_location]
        for record in msa:
            if record.id == "consensus":
                continue
            align_base = record[base_location]

            if align_base in bases and base != align_base and align_base != "N":
                conservation -= 1 / len(msa)
                if align_base not in variation:
                    variation.append(align_base)
                sequences[record.id].append(align_base)
            elif align_base == "N" or align_base == "-":
                sequences[record.id].append(align_base)
            else:
                sequences[record.id].append("")

        conservations.append('{:.1f}'.format(round(conservation * 100, 1)))
        variations.append(variation)

    if total_sequences <= MAX_SEQUENCES_FOR_SIMILARITY:
        for record in msa:
            if record.id == "consensus":
                continue

            similarity = pairwise_trimmed_similarity(record.seq, consensus)
            matches["consensus"][record.id] = similarity
            matches[record.id] = {"consensus": similarity}

            for compared_record in msa:
                if record.id != compared_record.id:
                    if compared_record.id not in matches[record.id]:
                        similarity = pairwise_trimmed_similarity(record.seq, compared_record.seq)
                        matches[record.id][compared_record.id] = similarity

                    if compared_record.id not in matches:
                        matches[compared_record.id] = {}

                    matches[compared_record.id][record.id] = matches[record.id][compared_record.id]
                else:
                    matches[record.id][compared_record.id] = "-"

    return conservations, variations, sequences, matches

def calculate_consensus(msa):
    consensus = []
    for i in range(msa.get_alignment_length()):
        # Get the column as a list of nucleotides
        column = [record.seq[i] for record in msa if record.seq[i] != '-']  # Exclude gaps
        # Count the occurrences of each nucleotide
        counts = Counter(column)
        if counts:  # Check if there are any valid bases
            # Get the most common nucleotide; if there is a tie, it gets the first one
            most_common = counts.most_common(1)[0][0]
        else:
            most_common = '-'  # Handle the case where the column is all gaps
        consensus.append(most_common)
    return Seq(''.join(consensus))

@shared_task
def process_alignment(input_file_path, muscle_job_id):
    muscle_job = MuscleJob.objects.get(id=muscle_job_id)
    # Use tempfile to create a temporary output file for the alignment
    with tempfile.NamedTemporaryFile(delete=False, suffix=".fasta") as temp_outfile:
        outfile = temp_outfile.name  # Get the path of the temporary file

    muscle_program = settings.MUSCLE_PROGRAM
    muscle_version_command = f"{muscle_program} -version"
    p = subprocess.Popen(muscle_version_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    muscle_version = p.communicate()[0].decode('utf-8').strip()

    # Determine argument names based on MUSCLE version
    input_arg_name = "-in" if ("muscle 3" in muscle_version or "MUSCLE v3" in muscle_version) else "-align"
    output_arg_name = "-out" if ("muscle 3" in muscle_version or "MUSCLE v3" in muscle_version) else "-output"

    # Prepare and run the MUSCLE command
    muscle_command = f"{muscle_program} {input_arg_name} {input_file_path} {output_arg_name} {outfile}"
    print(muscle_command)
    p = subprocess.Popen(muscle_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    returnVal = p.wait()

    # Prepare response data
    data = {}
    if returnVal != 0:
        data["status"] = "error"
        message = p.stderr.read().decode('utf-8')  # Use stderr for errors
        data["message"] = message
        print("Error in alignment process:", message)
    else:
        msa = AlignIO.read(outfile, "fasta")
        #alignment = msa.alignment
        #motif = Motif('ACGTN', msa)
        #consensus = str(motif.consensus)
        consensus = calculate_consensus(msa)
        conservations, variations, sequences, matches = calculate_conservation_and_variation(msa, consensus)

        data["conservations"] = conservations
        data["variations"] = variations
        data["consensus"] = consensus
        data["sequences"] = sequences
        data["similarity"] = matches
        data["status"] = "success"
        data["message"] = ""
        
        # Create a MuscleData instance
        muscle_data = MuscleData.objects.create(muscle_job=muscle_job)

        # Save the alignment content into the database
        with open(outfile, 'r') as temp_file:
            muscle_content_file = ContentFile(temp_file.read().encode('utf-8'))
            muscle_file_name = f"{muscle_data.id}.fasta"
            muscle_file_path = default_storage.save(f"alignment_files/{muscle_file_name}", muscle_content_file)

        muscle_data.associated_alignment.name = muscle_file_path
        muscle_data.save()
        print("Muscle data created")

        # Create MuscleSequence instances
        muscle_sequences = data['sequences']
        sequence_objs = [
            MuscleSequence(
                muscle_data=muscle_data,
                name=sequence,
                bases=','.join(bases)
            ) for sequence, bases in muscle_sequences.items()
        ]
        print("Muscle sequences obj created")

        # Create MuscleConservation instances
        conservation_objs = [
            MuscleConservation(
                muscle_data=muscle_data,
                position=i + 1,
                value=float(value)
            ) for i, value in enumerate(data["conservations"])
        ]
        print("Muscle conservation obj created")

        # Create MuscleVariation instances
        variation_objs = [
            MuscleVariation(
                muscle_data=muscle_data,
                position=i + 1,
                variations=','.join(value)
            ) for i, value in enumerate(data["variations"])
        ]
        print("Muscle variation obj created")

        # Create MuscleSimilarity instances in bulk
        similarity_objs = [
            MuscleSimilarity(
                muscle_data=muscle_data,
                sequence1=key1,
                sequence2=key2,
                similarity_percentage=float(percentage)
            ) for key1, sequence in data["similarity"].items()
            for key2, percentage in sequence.items() if percentage != "-"
        ]
        print("Muscle similarity obj created")

        # Bulk create the objects
        MuscleSequence.objects.bulk_create(sequence_objs)
        print("Muscle sequences created")
        MuscleConservation.objects.bulk_create(conservation_objs)
        print("Muscle conservation created")
        MuscleVariation.objects.bulk_create(variation_objs)
        print("Muscle variation created")

        MuscleConsensus.objects.create(
            muscle_data=muscle_data,
            sequence=data["consensus"]
        )
        print("Muscle consensus created")

        MuscleSimilarity.objects.bulk_create(similarity_objs)
        print("Muscle similarity created")

    try:
        os.remove(outfile)
    except Exception as e:
        print(f"Error deleting temporary file: {e}")

    #print(execute_phylip_pipeline(muscle_file_path, ""))

def rename_fasta_headers(input_path, header_mapping):
    """Renames FASTA headers and maps old to new headers."""
    # Create temp output_fasta
    with tempfile.NamedTemporaryFile(delete=False, suffix='.fasta') as output_fasta:
        with open(input_path, 'r') as infile:
            header_mapping.clear()
            counter = 1
            for line in infile:
                if line.startswith('>'):
                    new_header = f'seq_{counter:03d}'
                    header_mapping[line.strip()[1:]] = new_header
                    output_fasta.write(f'>{new_header}\n'.encode())
                    counter += 1  # Increment counter only for header lines
                else:
                    output_fasta.write(line.encode())
    return output_fasta.name
                
def convert_fasta_to_phylip_format(fasta_file):
    """Converts FASTA file to PHYLIP format."""
    # Create temp phylip_file
    phylip_file = tempfile.NamedTemporaryFile(delete=False, suffix='.phy', mode='w')
    records = SeqIO.parse(fasta_file, 'fasta')
    SeqIO.write(records, phylip_file, 'phylip')
    phylip_file.close()
    return phylip_file.name
    
def remove_trailing_newlines(file_path):
    """Removes any empty trailing lines in the provided file."""
    with open(file_path, 'r+') as file:
        lines = file.readlines()
        file.seek(0)
        file.writelines(line.rstrip() + '\n' for line in lines if line.strip())
        file.truncate()
        
def restore_original_headers(header_mapping, file_path):
    """Restores original headers using the provided mapping."""
    with open(file_path, 'r+') as file:
        content = file.read()
        for original, new in header_mapping.items():
            content = content.replace(new, original)
        file.seek(0)
        file.write(content)
        file.truncate()
        
def find_outgroup_index(outgroup_name, header_mapping, tree_file):
    """Finds the index of the specified outgroup in the tree file."""
    with open(tree_file, 'r') as file:
        content = file.read().split(';', 1)[0]

    sequences = [content[i:i+7] for i in range(len(content)) if content[i] == 's']
    
    return sequences.index(header_mapping.get(outgroup_name, '')) + 1 if outgroup_name in header_mapping else 1

def find_outgroup_index_in_fasta_file(infile, outgroup):
    try:
        with open(infile, 'r') as f:
            # Collect all sequence headers that start with '>'
            list_of_names = [line[1:].strip() for line in f if line.startswith('>')]

        # Return the index of the outgroup (1-based), or default to 1
        return list_of_names.index(outgroup.strip()) + 1 if outgroup.strip() in list_of_names else 1
    except Exception:
        # Default to returning 1 in any failure case
        return 1

def run_program_with_control(program_path, control_input):
    """Runs a PHYLIP program with control input provided."""
    result = subprocess.run([program_path], input=control_input, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    #if result.returncode != 0:
    #    print(f"Error running {program_path}: {result.stderr}")
    return result.stdout

def run_seqboot(infile):
    """Runs the SEQBOOT program."""
    outfile = tempfile.NamedTemporaryFile(delete=False, mode='w')
    outfile_name = outfile.name
    try:
        os.remove(outfile_name)
    except Exception as e:
        print(f"Error deleting temporary file: {e}")
    seqboot_control = f"""{infile}\n2\nR\n100\nY\n101\nF\n{outfile_name}"""
    run_program_with_control(settings.SEQBOOT_PROGRAM, seqboot_control)
    outfile.close()
    return outfile_name
    

def run_dnadist(infile):
    """Runs the DNADIST program."""
    outfile = tempfile.NamedTemporaryFile(delete=False, mode='w')
    outfile_name = outfile.name
    try:
        os.remove(outfile_name)
    except Exception as e:
        print(f"Error deleting temporary file: {e}")
    dnadist_control = f"""{infile}\nF\n{outfile_name}\nL\n2\nM\nD\n100\nY"""
    run_program_with_control(settings.DNADIST_PROGRAM, dnadist_control)
    outfile.close()
    return outfile_name

def run_neighbor(infile):
    """Runs the NEIGHBOR program."""
    outfile = tempfile.NamedTemporaryFile(delete=False, mode='w')
    outfile_name = outfile.name
    outtree = tempfile.NamedTemporaryFile(delete=False, mode='w')
    outtree_name = outtree.name
    try:
        os.remove(outfile_name)
    except Exception as e:
        print(f"Error deleting temporary file: {e}")
    try:
        os.remove(outtree_name)
    except Exception as e:
        print(f"Error deleting temporary file: {e}")
    neighbor_control = f"""{infile}\nF\n{outfile_name}\n2\nL\nM\n100\n101\nY\nF\n{outtree_name}"""
    run_program_with_control(settings.NEIGHBOR_PROGRAM, neighbor_control)
    return outfile_name, outtree_name

def run_consense(intree, outgroup_name, header_mapping):
    """Runs the CONSENSE program and identifies the outgroup based on header mapping."""
    outfile = tempfile.NamedTemporaryFile(delete=False, mode='w')
    outfile_name = outfile.name
    outtree = tempfile.NamedTemporaryFile(delete=False, mode='w')
    outtree_name = outtree.name
    try:
        os.remove(outfile_name)
    except Exception as e:
        print(f"Error deleting temporary file: {e}")
    try:
        os.remove(outtree_name)
    except Exception as e:
        print(f"Error deleting temporary file: {e}")
    outgroup_index = find_outgroup_index(outgroup_name, header_mapping, intree)
    consense_control = f"""{intree}\nF\n{outfile_name}\nO\n{outgroup_index}\n1\n2\nY\nF\n{outtree_name}"""
    run_program_with_control(settings.CONSENSE_PROGRAM, consense_control)
    return outfile_name, outtree_name

def run_dnaml(infile, outgroup_index):
    outfile = tempfile.NamedTemporaryFile(delete=False, mode='w')
    outfile_name = outfile.name
    outtree = tempfile.NamedTemporaryFile(delete=False, mode='w')
    outtree_name = outtree.name
    try:
        os.remove(outfile_name)
    except Exception as e:
        print(f"Error deleting temporary file: {e}")
    try:
        os.remove(outtree_name)
    except Exception as e:
        print(f"Error deleting temporary file: {e}")
    dnaml_control = f"""{infile}\nF\n{outfile_name}\n2\nO\n{outgroup_index}\nY\nF\n{outtree_name}"""
    run_program_with_control(settings.DNAML_PROGRAM, dnaml_control)
    return outfile_name, outtree_name
    
def execute_phylip_pipeline(fasta_file, outgroup):
    """Executes the entire PHYLIP pipeline to generate a consensus tree from a FASTA alignment."""
    header_mapping = {}

    # Rename headers in the FASTA file
    fixed_fasta_file = rename_fasta_headers(fasta_file, header_mapping)

    # Convert the renamed FASTA file to PHYLIP format
    phylip_infile = convert_fasta_to_phylip_format(fixed_fasta_file)
    remove_trailing_newlines(phylip_infile)

    # Run SEQBOOT
    dnadist_infile = run_seqboot(phylip_infile)

    # Run DNADIST
    remove_trailing_newlines(dnadist_infile)
    neighbor_infile = run_dnadist(dnadist_infile)

    # Run NEIGHBOR
    #remove_trailing_newlines(neighbor_infile)
    neighbor_outfile, consense_intree = run_neighbor(neighbor_infile)

    # Run CONSENSE
    #remove_trailing_newlines(consense_intree)
    consense_outfile, consense_outtree = run_consense(consense_intree, outgroup, header_mapping)

    restore_original_headers(header_mapping, consense_outtree)


    # Read content from consense_outtree
    with open(consense_outtree, 'r') as file:
        content = file.read().replace('\n', '')

    # Delete all temporary files
    temp_files = [
        fixed_fasta_file, 
        phylip_infile, 
        dnadist_infile, 
        neighbor_infile, 
        consense_intree,
        neighbor_outfile,
        consense_outtree, 
        consense_outfile
    ]
    
    for temp_file in temp_files:
        if os.path.exists(temp_file):
            os.remove(temp_file)

    return content

def execute_phylip_ml_pipeline(fasta_file, outgroup):
    outgroup_index = find_outgroup_index_in_fasta_file(fasta_file, outgroup)
    header_mapping = {}

    # Rename headers in the FASTA file
    fixed_fasta_file = rename_fasta_headers(fasta_file, header_mapping)

    # Convert the renamed FASTA file to PHYLIP format
    phylip_infile = convert_fasta_to_phylip_format(fixed_fasta_file)
    remove_trailing_newlines(phylip_infile)

    # Run DNAML
    dnaml_outfile, dnaml_outtree = run_dnaml(phylip_infile, outgroup_index)

    restore_original_headers(header_mapping, dnaml_outtree)

    # Read content from dnaml_outtree
    with open(dnaml_outtree, 'r') as file:
        content = file.read().replace('\n', '')

    # Delete all temporary files
    temp_files = [
        fixed_fasta_file, 
        phylip_infile, 
        dnaml_outfile, 
        dnaml_outtree 
    ]
    
    for temp_file in temp_files:
        if os.path.exists(temp_file):
            os.remove(temp_file)

    return content

@shared_task
def run_phylip_nj_task(job_id, dataFile, outgroup):
    try:
        # Fetch the associated Job instance
        job = Job.objects.get(id=job_id)
        job.status = "RUNNING"
        job.save()

        content = execute_phylip_pipeline(dataFile, outgroup)

        if not content:
            job.status = "FAILED"
            #job.message = result.stderr
            job.save()
            return {
                "status": "error",
                "message": "No output"
            }

        # Process the results
        job.status = "ARCHIVING"
        job.save()

        # Get the associated PhylipNJJob instance
        phylipnj_job = PhylipNJJob.objects.get(job=job)
        if phylipnj_job:
            phylipnj_data = PhylipNJData.objects.create(phylipnj_job = phylipnj_job, outtree = content)
        # Update job status to "FINISHED" if successful
        job.status = "FINISHED"
        job.save()

        return {
            "status": "success",
            "message": "Phylip NJ job completed."
        }

    except Exception as e:
        # Handle exceptions and mark the job as "FAILED"
        if job:
            job.status = "FAILED"
            #job.message = str(e)
            job.save()
        return {
            "status": "error",
            "message": str(e)
        }

@shared_task
def run_phylip_ml_task(job_id, dataFile, outgroup):
    try:
        # Fetch the associated Job instance
        job = Job.objects.get(id=job_id)
        job.status = "RUNNING"
        job.save()

        content = execute_phylip_ml_pipeline(dataFile, outgroup)

        if not content:
            job.status = "FAILED"
            #job.message = result.stderr
            job.save()
            return {
                "status": "error",
                "message": "No output"
            }

        # Process the results
        job.status = "ARCHIVING"
        job.save()

        # Get the associated PhylipMLJob instance
        phylipml_job = PhylipMLJob.objects.get(job=job)
        if phylipml_job:
            phylipml_data = PhylipMLData.objects.create(phylipml_job = phylipml_job, outtree = content)
        # Update job status to "FINISHED" if successful
        job.status = "FINISHED"
        job.save()

        return {
            "status": "success",
            "message": "Phylip ML job completed."
        }

    except Exception as e:
        # Handle exceptions and mark the job as "FAILED"
        if job:
            job.status = "FAILED"
            #job.message = str(e)
            job.save()
        return {
            "status": "error",
            "message": str(e)
        }
