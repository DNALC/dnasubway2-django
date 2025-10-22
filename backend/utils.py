from django.conf import settings
from Bio import SeqIO
from Bio.Seq import Seq
from io import BytesIO, StringIO, TextIOWrapper
import csv
import gzip
import json
from urllib.request import urlopen
import os
import re
import requests
import subprocess
import tempfile
import time
import uuid
from tapipy.tapis import Tapis
from .models import Job, DataFile, TrimJob, ConsensusJob, ConsensusData, BlastJob, BlastData, BlastResult, MuscleJob, MuscleFile, MuscleData, MuscleSequence, MuscleConservation, MuscleVariation, MuscleConsensus, MuscleSimilarity, Project, ProjectDataFile, PhylipNJJob, PhylipNJData, PhylipMLJob, PhylipMLData
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
tapis = Tapis(base_url='https://cyverse.tapis.io', username=settings.TAPIS_CYVERSE_USERNAME, password=settings.TAPIS_CYVERSE_PASSWORD)
#tapis.get_tokens()

SERVICE_TOKEN = None

# https://www.ncbi.nlm.nih.gov/genbank/collab/country/
INSDC_COUNTRY_MAP = {
    "AF": "Afghanistan",
    "AL": "Albania",
    "DZ": "Algeria",
    "AS": "American Samoa",
    "AD": "Andorra",
    "AO": "Angola",
    "AI": "Anguilla",
    "AQ": "Antarctica",
    "AG": "Antigua and Barbuda",
    "AR": "Argentina",
    "AM": "Armenia",
    "AW": "Aruba",
    "AU": "Australia",
    "AT": "Austria",
    "AZ": "Azerbaijan",
    "BS": "Bahamas",
    "BH": "Bahrain",
    "BD": "Bangladesh",
    "BB": "Barbados",
    "BY": "Belarus",
    "BE": "Belgium",
    "BZ": "Belize",
    "BJ": "Benin",
    "BM": "Bermuda",
    "BT": "Bhutan",
    "BO": "Bolivia",
    "BA": "Bosnia and Herzegovina",
    "BW": "Botswana",
    "BV": "Bouvet Island",
    "BR": "Brazil",
    "VG": "British Virgin Islands",
    "BN": "Brunei",
    "BG": "Bulgaria",
    "BF": "Burkina Faso",
    "BI": "Burundi",
    "KH": "Cambodia",
    "CM": "Cameroon",
    "CA": "Canada",
    "CV": "Cape Verde",
    "KY": "Cayman Islands",
    "CF": "Central African Republic",
    "TD": "Chad",
    "CL": "Chile",
    "CN": "China",
    "CX": "Christmas Island",
    "CC": "Cocos Islands",
    "CO": "Colombia",
    "KM": "Comoros",
    "CK": "Cook Islands",
    "CR": "Costa Rica",
    "CI": "Cote d'Ivoire",
    "HR": "Croatia",
    "CU": "Cuba",
    "CW": "Curacao",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "CD": "Democratic Republic of the Congo",
    "DK": "Denmark",
    "DJ": "Djibouti",
    "DM": "Dominica",
    "DO": "Dominican Republic",
    "EC": "Ecuador",
    "EG": "Egypt",
    "SV": "El Salvador",
    "GQ": "Equatorial Guinea",
    "ER": "Eritrea",
    "EE": "Estonia",
    "SZ": "Eswatini",
    "ET": "Ethiopia",
    "FK": "Falkland Islands (Islas Malvinas)",
    "FO": "Faroe Islands",
    "FJ": "Fiji",
    "FI": "Finland",
    "AX": "Finland", # Pretend the Åland Islands is Finland, because it's not an approved country code
    "FR": "France",
    "GF": "French Guiana",
    "PF": "French Polynesia",
    "TF": "French Southern and Antarctic Lands",
    "GA": "Gabon",
    "GM": "Gambia",
    "GE": "Georgia",
    "DE": "Germany",
    "GH": "Ghana",
    "GI": "Gibraltar",
    "GR": "Greece",
    "GL": "Greenland",
    "GD": "Grenada",
    "GP": "Guadeloupe",
    "GU": "Guam",
    "GT": "Guatemala",
    "GG": "Guernsey",
    "GN": "Guinea",
    "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HT": "Haiti",
    "HM": "Heard Island and McDonald Islands",
    "HN": "Honduras",
    "HK": "Hong Kong",
    "HU": "Hungary",
    "IS": "Iceland",
    "IN": "India",
    "IO": "Indian Ocean",
    "ID": "Indonesia",
    "IR": "Iran",
    "IQ": "Iraq",
    "IE": "Ireland",
    "IM": "Isle of Man",
    "IL": "Israel",
    "IT": "Italy",
    "VA": "Italy", # Pretend Vatican City is Italy, because it's not an approved country code
    "JM": "Jamaica",
    "JP": "Japan",
    "JE": "Jersey",
    "JO": "Jordan",
    "KZ": "Kazakhstan",
    "KE": "Kenya",
    "KI": "Kiribati",
    "XK": "Kosovo",
    "KW": "Kuwait",
    "KG": "Kyrgyzstan",
    "LA": "Laos",
    "LV": "Latvia",
    "LB": "Lebanon",
    "LS": "Lesotho",
    "LR": "Liberia",
    "LY": "Libya",
    "LI": "Liechtenstein",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "MO": "Macau",
    "MG": "Madagascar",
    "MW": "Malawi",
    "MY": "Malaysia",
    "MV": "Maldives",
    "ML": "Mali",
    "MT": "Malta",
    "MH": "Marshall Islands",
    "MQ": "Martinique",
    "MR": "Mauritania",
    "MU": "Mauritius",
    "YT": "Mayotte",
    "MX": "Mexico",
    "FM": "Micronesia, Federated States of",
    "MD": "Moldova",
    "MC": "Monaco",
    "MN": "Mongolia",
    "ME": "Montenegro",
    "MS": "Montserrat",
    "MA": "Morocco",
    "MZ": "Mozambique",
    "MM": "Myanmar",
    "NA": "Namibia",
    "NR": "Nauru",
    "NP": "Nepal",
    "NL": "Netherlands",
    "NC": "New Caledonia",
    "NZ": "New Zealand",
    "NI": "Nicaragua",
    "NE": "Niger",
    "NG": "Nigeria",
    "NU": "Niue",
    "NF": "Norfolk Island",
    "KP": "North Korea",
    "MK": "North Macedonia",
    "MP": "Northern Mariana Islands",
    "NO": "Norway",
    "OM": "Oman",
    "PK": "Pakistan",
    "PW": "Palau",
    "PA": "Panama",
    "PG": "Papua New Guinea",
    "PY": "Paraguay",
    "PE": "Peru",
    "PH": "Philippines",
    "PN": "Pitcairn Islands",
    "PL": "Poland",
    "PT": "Portugal",
    "PR": "Puerto Rico",
    "QA": "Qatar",
    "CG": "Republic of the Congo",
    "RE": "Reunion",
    "RO": "Romania",
    "RU": "Russia",
    "RW": "Rwanda",
    "BL": "Saint Barthelemy",
    "SH": "Saint Helena",
    "KN": "Saint Kitts and Nevis",
    "LC": "Saint Lucia",
    "MF": "Saint Martin",
    "PM": "Saint Pierre and Miquelon",
    "VC": "Saint Vincent and the Grenadines",
    "WS": "Samoa",
    "SM": "San Marino",
    "ST": "Sao Tome and Principe",
    "SA": "Saudi Arabia",
    "SN": "Senegal",
    "RS": "Serbia",
    "SC": "Seychelles",
    "SL": "Sierra Leone",
    "SG": "Singapore",
    "SX": "Sint Maarten",
    "BQ": "Sint Maarten", # Pretend Bonaire, Sint Eustatius and Saba is Sint Maarten, because it's not an approved country code
    "SK": "Slovakia",
    "SI": "Slovenia",
    "SB": "Solomon Islands",
    "SO": "Somalia",
    "ZA": "South Africa",
    "GS": "South Georgia and the South Sandwich Islands",
    "KR": "South Korea",
    "SS": "South Sudan",
    "ES": "Spain",
    "LK": "Sri Lanka",
    "PS": "State of Palestine",
    "SD": "Sudan",
    "SR": "Suriname",
    "SJ": "Svalbard",
    "SE": "Sweden",
    "CH": "Switzerland",
    "SY": "Syria",
    "TW": "Taiwan",
    "TJ": "Tajikistan",
    "TZ": "Tanzania",
    "TH": "Thailand",
    "TL": "Timor-Leste",
    "TG": "Togo",
    "TK": "Tokelau",
    "TO": "Tonga",
    "TT": "Trinidad and Tobago",
    "TN": "Tunisia",
    "TR": "Turkey",
    "TM": "Turkmenistan",
    "TC": "Turks and Caicos Islands",
    "TV": "Tuvalu",
    "UG": "Uganda",
    "UA": "Ukraine",
    "AE": "United Arab Emirates",
    "GB": "United Kingdom",
    "US": "USA",
    "UM": "USA", # Pretend U.S. Minor Outlying Islands is USA, because it's not an approved country code
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VU": "Vanuatu",
    "VE": "Venezuela",
    "VN": "Viet Nam",
    "VI": "Virgin Islands",
    "WF": "Wallis and Futuna",
    "EH": "Western Sahara",
    "YE": "Yemen",
    "ZM": "Zambia",
    "ZW": "Zimbabwe",
}

def validate_fastq_gz(file_obj):
    """Validate that file is gzipped FASTQ using BioPython."""
    try:
        # Rewind file pointer
        file_obj.seek(0)
        with gzip.open(file_obj, "rt") as handle:
            # Try parsing the first record only (don't load the whole file)
            first = next(SeqIO.parse(handle, "fastq"))
            if not first.id:
                return False, "Invalid FASTQ: missing sequence ID"
        file_obj.seek(0)  # reset pointer after reading
        return True, None
    except Exception as e:
        return False, f"Invalid FASTQ: {e}"

CASE_INSENSITIVE_HEADERS = {'id', 'sampleid', 'sample id', 'sample-id', 'featureid', 'feature id', 'feature-id'}

CASE_SENSITIVE_HEADERS = {'#SampleID', '#Sample ID', '#OTUID', '#OTU ID', 'sample_name'}

NUMERIC_PATTERN = re.compile(r'^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$')
SAFE_ID_PATTERN = re.compile(r'^[\w.-]{1,36}$')

def number_valid(cell_val):
    if cell_val is None:
        return False
    s = cell_val.strip()
    if s == "":
        return True

    if not NUMERIC_PATTERN.fullmatch(s):
        return False

    try:
        num = float(s)
        if not float('-inf') < num < float('inf'):
            return False
    except ValueError:
        return False

    digits_only = re.sub(r'[^0-9]', '', s)
    if len(digits_only) > 15:
        return False

    return True

def validate_qiime2_tsv(tsv_string):
    errors = []
    lines = tsv_string.splitlines()
    header_line = None
    column_types = []
    id_set = set()

    # --- Step 1: Detect header row ---
    for i, line in enumerate(lines):
        line_strip = line.strip()
        if not line_strip:
            continue
        headers = [h.strip() for h in line_strip.split('\t')]
        if headers[0] in CASE_SENSITIVE_HEADERS or headers[0].lower() in {h.lower() for h in CASE_INSENSITIVE_HEADERS}:
            header_line_num = i
            header_line = headers
            break
    else:
        errors.append("No valid header row with a QIIME2 ID column found.")
        return errors

    # --- Step 2: Header checks ---
    if len(header_line) != len(set(h.lower() for h in header_line)):
        errors.append("Header names must be unique (case-insensitive).")
    if any(not h for h in header_line):
        errors.append("Column names cannot be empty.")
    for col_name in header_line[1:]:
        if col_name in CASE_SENSITIVE_HEADERS or col_name.lower() in {h.lower() for h in CASE_INSENSITIVE_HEADERS}:
            errors.append(f"Column '{col_name}' is a reserved ID header and cannot be used")

    # --- Step 3: Optional #q2:types ---
    next_line_idx = header_line_num + 1
    if next_line_idx < len(lines):
        line = lines[next_line_idx].strip()
        if line.lower().startswith('#q2:types'):
            type_cells = [c.strip().lower() for c in line.split('\t')]
            if len(type_cells) > len(header_line):
                errors.append(f"#q2:types row has more cells ({len(type_cells)}) than header ({len(header_line)})")
            elif len(type_cells) < len(header_line):
                type_cells += [''] * (len(header_line) - len(type_cells))  # pad missing types
            for t in type_cells[1:]:
                if t not in ('categorical', 'numeric', ''):
                    errors.append(f"#q2:types contains invalid type '{t}'")
            column_types = type_cells

    # --- Step 4: Data rows ---
    data_start_idx = header_line_num + 1
    if column_types:
        data_start_idx += 1  # skip #q2:types row

    for line_num, line in enumerate(lines[data_start_idx:], start=data_start_idx + 1):
        line_strip = line.strip()
        if not line_strip or line_strip.startswith('#'):
            continue
        cells = [c.strip() for c in line_strip.split('\t')]
        if len(cells) > len(header_line):
            errors.append(f"Line {line_num}: Row has more cells ({len(cells)}) than header ({len(header_line)})")
        elif len(cells) < len(header_line):
            cells += [''] * (len(header_line) - len(cells))  # pad missing cells

        # --- ID validation ---
        sample_id = cells[0]
        if not sample_id:
            errors.append(f"Line {line_num}: ID cell cannot be empty.")
        if sample_id.startswith('#'):
            errors.append(f"Line {line_num}: ID cannot start with '#'")
        if sample_id in CASE_SENSITIVE_HEADERS or sample_id.lower() in {h.lower() for h in CASE_INSENSITIVE_HEADERS}:
            errors.append(f"Line {line_num}: ID cannot be a reserved header name '{sample_id}'")
        if sample_id in id_set:
            errors.append(f"Line {line_num}: Duplicate ID '{sample_id}' found.")
        if not SAFE_ID_PATTERN.match(sample_id):
            errors.append(f"Line {line_num}: ID '{sample_id}' contains invalid characters or is too long (>36)")
        id_set.add(sample_id)

        # --- Numeric validation ---
        for col_idx, col_name in enumerate(header_line[1:], start=1):
            cell_val = cells[col_idx]
            col_type = ''
            if column_types:
                col_type = column_types[col_idx].lower()
            if cell_val and col_type == 'numeric' and not number_valid(cell_val):
                errors.append(f"Line {line_num}: Cell '{cell_val}' in column '{col_name}' is not a valid numeric value.")

    if not id_set:
        errors.append("No valid IDs found in the first column.")

    return errors

def validate_qiime2_metadata_format(file_obj):
    """
    Validate metadata file against QIIME2 rules + stricter identifier checks.
    Returns (True, None) if valid, (False, errors) otherwise.
    """
    try:
        file_obj.seek(0)
        text_stream = TextIOWrapper(file_obj, encoding="utf-8")

        # Read contents into a single UTF-8 string
        tsv_string = text_stream.read()

        errors = validate_qiime2_tsv(tsv_string)

        file_obj.seek(0)
        if errors:
            return False, errors

        return True, None

    except Exception as e:
        # Defensive fallback for unexpected I/O issues
        try:
            file_obj.seek(0)
        except Exception:
            pass
        return False, ["Error reading metadata file"]
    finally:
        text_stream.detach()

    file_obj.seek(0)
    return True, None

def base10_to_base36(num):
    if num < 0:
        raise ValueError("Only non-negative integers are supported")
    if num == 0:
        return "0"

    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    result = ""

    while num > 0:
        num, remainder = divmod(num, 36)
        result = digits[remainder] + result

    return result

def run_openstack(cmd):
    OPENRC_PATH = settings.OPENRC_PATH
    full_cmd = f"source {OPENRC_PATH} && {cmd}"
    try:
        return subprocess.check_output(
            ["bash", "-c", full_cmd],
            universal_newlines=True,
        )
    except Exception:
        # Catch anything unexpected
        return json.dumps({"status": "FAILURE"})

def shelve_instance(instance_name):
    MAX_WAIT_TIME = settings.MAX_WAIT_TIME
    try:
        state = run_openstack(f"/usr/local/bin/openstack server show -f json '{instance_name}'")
        status = json.loads(state)["status"]

        if status == "SHUTOFF":
            run_openstack(f"/usr/local/bin/openstack server start '{instance_name}'")
            for _ in range(MAX_WAIT_TIME // 10):
                time_elapsed += 10
                time.sleep(10)
                state = run_openstack(f"/usr/local/bin/openstack server show -f json '{instance_name}'")
                status = json.loads(state)["status"]
                if status == "ACTIVE":
                    break
                print(f"Waiting for ACTIVE... Time elapsed: {time_elapsed}s")

        if status != "ACTIVE":
            return False, f"Instance '{instance_name}' is not ACTIVE (current state: {status})"

        print(f"Shelving instance '{instance_name}'...")
        run_openstack(f"/usr/local/bin/openstack server shelve --wait '{instance_name}'")

        # Wait until instance is shelved
        for _ in range(MAX_WAIT_TIME // 10):
            time.sleep(10)
            state = run_openstack(f"/usr/local/bin/openstack server show -f json '{instance_name}'")
            status = json.loads(state)["status"]
            if status in ["SHELVED", "SHELVED_OFFLOADED"]:
                print(f"Instance shelved.")
                return True, None

        return False, f"Timeout: {instance_name} not SHELVED after {MAX_WAIT_TIME} seconds"

    except Exception as e:
        return False, str(e)

def ensure_instance_ready(instance_name):
    MAX_WAIT_TIME = settings.MAX_WAIT_TIME
    def can_ssh():
        ssh_cmd = (
            "ssh -o ConnectTimeout=5 "
            "-o BatchMode=yes "
            "-o StrictHostKeyChecking=no "
            "-i ~/.ssh/other/jetstream2 "
            "exouser@149.165.171.197 exit"
        )
        try:
            subprocess.check_call(ssh_cmd, shell=True)
            return True
        except subprocess.CalledProcessError:
            return False

    try:
        time_elapsed = 0
        state = run_openstack(f"/usr/local/bin/openstack server show -f json '{instance_name}'")
        status = json.loads(state)["status"]

        if status == "SHUTOFF":
            run_openstack(f"/usr/local/bin/openstack server start '{instance_name}'")
        elif status in ["SHELVED", "SHELVED_OFFLOADED"]:
            run_openstack(f"/usr/local/bin/openstack server unshelve '{instance_name}'")

        # Wait for it to become ACTIVE if necessary
        if status in ["SHUTOFF", "SHELVED", "SHELVED_OFFLOADED"]:
            for _ in range(MAX_WAIT_TIME // 10):
                time_elapsed += 10
                time.sleep(10)
                state = run_openstack(f"/usr/local/bin/openstack server show -f json '{instance_name}'")
                status = json.loads(state)["status"]
                if status == "ACTIVE":
                    break
                print(f"Waiting for ACTIVE... Time elapsed: {time_elapsed}s")

        if status != "ACTIVE":
            return False, f"Instance {instance_name} did not become ACTIVE within {MAX_WAIT_TIME}s"

        # Now wait for SSH availability
        print("Instance is ACTIVE. Checking SSH availability...")
        for _ in range(MAX_WAIT_TIME // 10):
            if can_ssh():
                print("SSH is now available.")
                return True, None
            time_elapsed += 10
            print(f"SSH not available yet... Time elapsed: {time_elapsed}s")
            time.sleep(10)

        return False, f"SSH not available on instance {instance_name} after {MAX_WAIT_TIME} seconds."

    except Exception as e:
        return False, str(e)

def get_service_token():
    global SERVICE_TOKEN
    SERVICE_USERNAME = settings.TAPIS_SERVICE_USERNAME
    SERVICE_PASSWORD = settings.TAPIS_SERVICE_PASSWORD
    BASE_URL = settings.TAPIS_URL

    payload = {
        "account_type": "service",
        "token_tenant_id": "admin",
        "token_username": SERVICE_USERNAME,
        "target_site_id": "tacc",
        "access_token_ttl": 99999,
    }

    headers = {
        'X-Tapis-Tenant': 'admin',
        'X-Tapis-User': SERVICE_USERNAME
    }

    try:
        rsp = requests.post(
            url=f"{BASE_URL}/v3/tokens",
            auth=(SERVICE_USERNAME, SERVICE_PASSWORD),
            headers=headers,
            json=payload
        )
        rsp.raise_for_status()
        result = rsp.json()["result"]["access_token"]
        SERVICE_TOKEN = result["access_token"]
    except Exception as e:
        print(f"Error generating token: {e}; message: {rsp.content}")

def generate_user_token(username):
    if not SERVICE_TOKEN:
        return None
    SERVICE_USERNAME = settings.TAPIS_SERVICE_USERNAME
    BASE_URL = settings.TAPIS_URL
    payload = {
        "account_type": "user",
        "token_tenant_id": SERVICE_USERNAME,
        "token_username": username,  # the username of the user you authenticated separately
        "target_site_id": "tacc",
        "access_token_ttl": 14400, # this dictates how long the user token lasts; we recommend 4 hours
    }

    headers = {'X-Tapis-Tenant': 'admin', 'X-Tapis-User': SERVICE_USERNAME, 'X-Tapis-Token': SERVICE_TOKEN}
    rsp = requests.post(url=f"{BASE_URL}/v3/tokens", headers=headers, json=payload)
    try:
        rsp.raise_for_status()
        result = rsp.json()["result"]["access_token"]
        user_token = result["access_token"]
        return user_token
    except Exception as e:
        print(f"Error generating token: {e}; message: {rsp.content}")
        return None

def connect_to_tapis(username, user_token):
    BASE_URL = settings.TAPIS_URL
    t = Tapis(base_url= BASE_URL, username=username, access_token=user_token)
    return t

def list_all_files(tapis, job_uuid, output_path='/', limit=150):
    files = []

    # List current path contents
    listing = tapis.jobs.getJobOutputList(jobUuid=job_uuid, outputPath=output_path, limit=limit)

    for item in listing:
        path = os.path.join(output_path, item.name)

        if item.type == 'dir':
            # Recurse into subdirectory
            files.extend(list_all_files(tapis, job_uuid, path, limit=200))
        else:
            files.append(path)

    return files

def download_tapis_file(system_id, user_token, remote_path):
    url = f"https://dnasubway.tapis.io/v3/files/content/{system_id}/{remote_path}"
    headers = {"X-Tapis-Token": user_token}

    try:
        resp = requests.get(url, headers=headers, timeout=60)
        if resp.status_code == 200:
            return resp.content
        else:
            print(f"Download failed {resp.status_code}: {resp.text}")
            return None
    except Exception as e:
        print(f"Error downloading {remote_path}: {e}")
        return None

def get_file_content(tapis, job_uuid, file_path):
    # Use getJobOutputDownload() to retrieve file content
    content = tapis.jobs.getJobOutputDownload(jobUuid=job_uuid, outputPath=file_path)
    return content


def extract_sequences(fasta_content):
    try:
        fasta_io = StringIO(fasta_content)
        sequences = list(SeqIO.parse(fasta_io, "fasta"))
        return sequences
    except Exception as e:
        return []

def extract_genbank_data(genbank_data):
    handle = StringIO(genbank_data)

    # List to store the name and reads
    result = []

    # Parse the GenBank data
    for record in SeqIO.parse(handle, "genbank"):
        # Extract species name from the description (first two words are usually the genus and species)
        species_name_parts = record.description.split(' ')[:2]  # Take the first two words
        name = (record.name or record.id) + "|" + '_'.join(species_name_parts)  # Create the name

        # Add the result to the list
        result.append({
            'name': name,
            'accession': record.id,
            'reads': str(record.seq)
        })

    return result

def is_text_file(content_bytes):
    """
    Checks if the given bytes represent a text file by attempting UTF-8 decoding.
    """
    try:
        content_bytes.decode('utf-8')  # Try decoding as text
        return True
    except UnicodeDecodeError:
        return False  # It's binary if decoding fails

def is_low_quality(quality_scores):
    average_quality = sum(quality_scores) / len(quality_scores)
    return average_quality < 20

def get_quality_scores(abi_file_path):
    # Check if the path is a URL or a local file
    if abi_file_path.startswith("http://") or abi_file_path.startswith("https://"):
        # Download the ABI file using urlopen
        with urlopen(abi_file_path) as response:
            abi_data = BytesIO(response.read())  # Load data into a BytesIO object
    else:
        # Open the local file
        abi_data = open(abi_file_path, "rb")

    try:
        # Read the ABI file
        record = SeqIO.read(abi_data, "abi")

        # Extract quality scores
        quality_scores = record.letter_annotations["phred_quality"]
        return quality_scores

    finally:
        # Close the file if it's a local file
        if isinstance(abi_data, BytesIO) == False:
            abi_data.close()

def common_prefix_clean(sequence1, sequence2):
    # Find the common prefix between the two strings
    min_length = min(len(sequence1), len(sequence2))
    common_prefix = []

    for i in range(min_length):
        if sequence1[i] == sequence2[i]:
            common_prefix.append(sequence1[i])
        else:
            break

    # Join the common prefix characters into a string
    common_str = ''.join(common_prefix)

    # Remove trailing non-alphanumeric characters using regex
    common_str = re.sub(r'[^a-zA-Z0-9]+$', '', common_str)

    # If no common prefix, return the format {sequence1} _{sequence2}
    if not common_str:
        return f'{sequence1}_{sequence2}'

    return common_str[:255]

def parse_reads(file_url):
    message = None
    sequence = None
    trace_exists = False
    returned_record = False
    quality_scores = None
    # Open the URL and read the content
    with urlopen(file_url) as response:
        file_content = response.read()

    try:
        # Try parsing as AB1
        for record in SeqIO.parse(BytesIO(file_content), "abi"):
            sequence = str(record.seq)
            trace_exists = True
            returned_record = record
            quality_scores = record.letter_annotations["phred_quality"]
    except (OSError, ValueError, TypeError):
        # Parsing as AB1 failed, try parsing as FASTQ
        try:
            file_str = file_content.decode('utf-8')
            records = list(SeqIO.parse(StringIO(file_str), "fastq"))
            if len(records) == 1:
                seq_record = records[0]
                sequence = str(seq_record.seq)
                returned_record = seq_record
                quality_scores = seq_record.letter_annotations["phred_quality"]
            else:
                message = "Error: FASTQ file must contain exactly one sequence."
        except (OSError, ValueError, TypeError):
            # Parsing as FASTQ failed, try parsing as FASTA
            try:
                file_str = file_content.decode('utf-8')
                records = list(SeqIO.parse(StringIO(file_str), "fasta"))
                if len(records) == 1:
                    seq_record = records[0]
                    sequence = str(seq_record.seq)
                    returned_record = seq_record
                else:
                    message = "Error: FASTA file must contain exactly one sequence."
            except (OSError, ValueError, TypeError):
                message = "Error: Unsupported file format."

    return message, sequence, trace_exists, returned_record, quality_scores

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

def submit_tapis_job(user, appId, params, projectId):
    tapis.get_tokens()
    try:
        job_response = tapis.jobs.submitJob(**params)
        project = Project.objects.get(id=projectId)
        job_uuid = job_response.get('uuid')
        print("Job submitted successfully. Job UUID:", job_uuid)
        job = Job.objects.create(user=user, appId=appId, project=project, uuid=job_uuid, status='PENDING')
        return job_uuid
    except Exception as e:
        print("Error submitting job:", e)
        return None

def placeholder_tapis_job(user, appId):
    job_uuid = str(uuid.uuid4()) + "-123"
    print("Placeholder job created successfully. Job UUID:", job_uuid)
    job = Job.objects.create(user=user, appId=appId, uuid=job_uuid, status='STARTING')
    return job

def fake_tapis_job(user, appId, params, projectId):
    project = Project.objects.get(id=projectId)
    job_uuid = str(uuid.uuid4()) + "-123"
    print("Fake job created successfully. Job UUID:", job_uuid)
    job = Job.objects.create(user=user, appId=appId, project=project, uuid=job_uuid, status='PENDING')
    return job_uuid

def multi_seq_muscle_jobs(output_file, file_ids):
    with open(output_file, 'w') as outfile:
        for file_id in file_ids:
            dataFile = DataFile.objects.get(id=file_id)
            fasta_path = dataFile.associated_fasta.path

            with open(fasta_path, 'r') as infile:
                header = None
                sequence_lines = []

                for line in infile:
                    line = line.strip()
                    if line.startswith(">"):
                        if header:
                            # Write previous record
                            sequence = "".join(sequence_lines)
                            if dataFile.read_type == "R":
                                sequence = str(Seq(sequence).reverse_complement())
                            outfile.write(header + "\n")
                            outfile.write(sequence + "\n")
                        header = line
                        sequence_lines = []
                    else:
                        sequence_lines.append(line)

                # Write last record
                if header:
                    sequence = "".join(sequence_lines)
                    if dataFile.read_type == "R":
                        sequence = str(Seq(sequence).reverse_complement())
                    outfile.write(header + "\n")
                    outfile.write(sequence + "\n")
    return output_file

# Retrieve job status
def get_job_status(job_uuid):
    try:
        job_details = tapis.jobs.getJob(jobUuid=job_uuid)
        return job_details
    except Exception as e:
        print("Error retrieving job details:", e)
        return None

def job_status_check(job_uuid, current_status):
    job = Job.objects.get(uuid=job_uuid)
    if job:
        job_details = tapis.jobs.getJob(jobUuid=job_uuid)
        print(job_details)
        if current_status == 'FINISHED':
            job_data = tapis.jobs.getJobOutputList(jobUuid=job_uuid, outputPath='/')
            if job_data:
                index = next((i for i, d in enumerate(job_data) if d.get('name') == "output.json"), -1)
                response_bytes = tapis.files.getContents(systemId="js2_tapis_test2", path=job_data[index].get("path"))
                response_string = response_bytes.decode('utf-8')
                try:
                    response_json = json.loads(response_string)
                except json.JSONDecodeError:
                    job.status = "FAILED"
                    job.save()
                    return
                if "status" in response_json and response_json["status"] == "error":
                    job.status = "FAILED"
                    job.save()
                    return
                try:
                    trim_job = TrimJob.objects.get(job=job)
                except:
                    trim_job = None
                try:
                    consense_job = ConsensusJob.objects.get(job=job)
                except:
                    consense_job = None
                try:
                    blast_job = BlastJob.objects.get(job=job)
                except:
                    blast_job = None
                try:
                    muscle_job = MuscleJob.objects.get(job=job)
                except:
                    muscle_job = None
                try:
                    phylipnj_job = PhylipNJJob.objects.get(job=job)
                except:
                    phylipnj_job = None
                try:
                    phylipml_job = PhylipMLJob.objects.get(job=job)
                except:
                    phylipml_job = None
                if response_json and trim_job:
                    print("Trim")
                    trim_job.data_file.reads = response_json["seq"]
                    trim_job.data_file.trim_start = (trim_job.data_file.trim_start if trim_job.data_file.trim_start else 0) + response_json["start_pos"]
                    trim_job.data_file.trim_end = (trim_job.data_file.trim_start if trim_job.data_file.trim_end else 0) + response_json["end_pos"]
                    trim_job.data_file.save()
                    print(response_json)
                elif response_json and consense_job:
                    print("Create consensus")
                    seq_name = common_prefix_clean(consense_job.forward_file.name, consense_job.reverse_file.name)
                    reads = response_json["consensus"]
                    consense_file = DataFile.objects.create(
                        user = consense_job.job.user,
                        name = seq_name,
                        trace_exists = False,
                        reads = reads,
                        read_type = "C",
                        source="consensus",
                    )
                    consense_file.forward_read = consense_job.forward_file
                    consense_file.reverse_read = consense_job.reverse_file
                    header = re.sub(r'\s+', '_', consense_file.name)
                    consense_content = ContentFile(f">{header}\n{consense_file.reads}\n")
                    fasta_file_name = f"{consense_file.id}.fasta"
                    fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", consense_content)
                    consense_file.associated_fasta.name = fasta_file_path
                    consense_file.save()
                    consense_project_file = ProjectDataFile.objects.create(
                        project = job.project,
                        data_file = consense_file,
                    )
                    ConsensusData.objects.create(consensus = consense_file,
                    forward_align = response_json["file1_align"],
                    reverse_align = response_json["file2_align"])
                    print(response_json)
                #elif response_json and blast_job:
                elif response_json and muscle_job:
                    muscle_alignment_index = next((i for i, d in enumerate(job_data) if d.get('name') == "alignment.fasta"), -1)
                    muscle_alignment_contents = tapis.files.getContents(systemId='js2_tapis_test2', path=job_data[muscle_alignment_index].get("path"))
                    muscle_alignment_string = muscle_alignment_contents.decode("utf-8")
                    muscle_data = MuscleData.objects.create(muscle_job = muscle_job)
                    muscle_content_file = ContentFile(muscle_alignment_string)
                    muscle_file_name = f"{muscle_data.id}.fasta"
                    muscle_file_path = default_storage.save(f"alignment_files/{muscle_file_name}", muscle_content_file)
                    muscle_data.associated_alignment.name = muscle_file_path
                    muscle_data.save()
                    print("Muscle data created")
                    muscle_sequences = response_json['sequences']
                    sequence_objs = [
                        MuscleSequence(
                            muscle_data=muscle_data,
                            name=sequence,
                            bases=','.join(bases)
                        ) for sequence, bases in muscle_sequences.items()
                    ]
                    print("Muscle sequences obj created")
                    conservation_objs = [
                        MuscleConservation(
                            muscle_data=muscle_data,
                            position=i+1,
                            value=float(value)
                        ) for i, value in enumerate(response_json["conservations"])
                    ]
                    print("Muscle conservation obj created")
                    variation_objs = [
                        MuscleVariation(
                            muscle_data=muscle_data,
                            position=i+1,
                            variations=','.join(value)
                        ) for i, value in enumerate(response_json["variations"])
                    ]
                    print("Muscle varitation obj created")
                    # Create MuscleSimilarity instances in bulk
                    similarity_objs = [
                        MuscleSimilarity(
                            muscle_data=muscle_data,
                            sequence1=key1,
                            sequence2=key2,
                            similarity_percentage=float(percentage)
                        ) for key1, sequence in response_json["similarity"].items()
                        for key2, percentage in sequence.items() if percentage != "-"
                    ]
                    print("Muscle similarity obj created")
                    MuscleSequence.objects.bulk_create(sequence_objs)
                    print("Muscle sequences created")
                    MuscleConservation.objects.bulk_create(conservation_objs)
                    print("Muscle conservation created")
                    MuscleVariation.objects.bulk_create(variation_objs)
                    print("Muscle variation created")
                    MuscleConsensus.objects.create(
                            muscle_data = muscle_data,
                            sequence = response_json["consensus"]
                    )
                    print("Muscle consensus created")
                    MuscleSimilarity.objects.bulk_create(similarity_objs)
                    print("Muscle similarity created")
                elif response_json and blast_job:
                    blast_data = BlastData.objects.create(
                        blast_file = blast_job.data_file
                    )
                    for result in response_json["results"]:
                        BlastResult.objects.create(
                            blast_data = blast_data,
                            accession = result["accession"],
                            details = result["details"],
                            length = result["length"],
                            evalue = result["evalue"],
                            mismatches = result["mismatches"],
                            sequence = result["sequence"],
                            bitscore = result["bitscore"]
                        )
                elif response_json and phylipnj_job:
                    phylipnj_data = PhylipNJData.objects.create(phylipnj_job = phylipnj_job, outtree = response_json["result"])
                elif response_json and phylipml_job:
                    phylipml_data = PhylipMLData.objects.create(phylipml_job = phylipml_job, outtree = response_json["result"])
                else:
                    print("Another job")
                return response_json
            elif current_status == 'FAILED':
                job.status = "FAILED"
                job.save()
                print("Job failed.")
                return
            elif current_status == 'STOPPED':
                job.status = "STOPPED"
                job.save()
                print("Job stopped.")
    return {
        "status": "error",
        "message": "Job failed."
    }

def sequence_trim(user, hostname, dataFile, left_trim, right_trim, projectId):
    associated_file = dataFile.associated_abi if dataFile.associated_abi else dataFile.associated_fasta
    file_path = os.path.join(hostname, associated_file.name)
    appId = "single_sequence_trim_app"
    envVariables = []
    if left_trim and right_trim:
        envVariables.append({"key":"LEFT_TRIM", "value":left_trim})
        envVariables.append({"key":"RIGHT_TRIM", "value":right_trim})
    print(file_path)
    job_params = {
        "name": "sequence_trim",
        "appId": appId,
        "appVersion": "0.7",
        "fileInputs":[{
            "name": "sequence",
            "sourceUrl": file_path
        }],
        "parameterSet": {
            "envVariables": envVariables
        },
        "subscriptions": [{
            "description": "Submit to URL on job new status",
            "enabled": True,
            "eventCategoryFilter": "JOB_NEW_STATUS",
            "deliveryTargets": [
              {
                "deliveryMethod": "WEBHOOK",
                "deliveryAddress": hostname + "/check_job_status"
              }
            ],
            "ttlMinutes": 10080
        }]
    }
    job_uuid = submit_tapis_job(user, appId, job_params, projectId)
    if job_uuid:
        job = Job.objects.get(uuid=job_uuid)
        trim_job = TrimJob.objects.create(job=job, data_file=dataFile)
        while True:
            status = get_job_status(job_uuid)
            if status:
                current_status = status.get("status")
                print(current_status)
                if current_status == 'FINISHED':
                    job_data = tapis.jobs.getJobOutputList(jobUuid=job_uuid, outputPath='/')
                    if job_data:
                        index = next((i for i, d in enumerate(job_data) if d.get('name') == "output.json"), -1)
                        response_bytes = tapis.files.getContents(systemId="js2_tapis_test2", path=job_data[index].get("path"))
                        response_string = response_bytes.decode('utf-8')
                        response_json = json.loads(response_string)
                        return response_json
                    break
                elif current_status == 'FAILED':
                    print("Job failed.")
                    #print(status)
                    break
                elif current_status == 'STOPPED':
                    print("Job stopped.")
                    #print(status)
                    break
            time.sleep(5) # Wait before checking the status again

    # Do trim
    return {
        "status": "error",
        "message": "Trim job failed."
    }

def local_blast(user, hostname, dataFile, clade, projectId):
    from .tasks import run_blast_task
    associated_file = dataFile.associated_fasta
    file_path = associated_file.name
    appId = "blastn_app"
    job_uuid = fake_tapis_job(user, appId, None, projectId)
    if job_uuid:
        job = Job.objects.get(uuid=job_uuid)
        blast_job = BlastJob.objects.create(job=job, data_file = dataFile, clade = clade if clade else 'default')
        run_blast_task.delay(job.id, file_path, clade)
        return {
            "status": "success",
            "message": "Blast job running."
        }
    return {
        "status": "error",
        "message": "Blast job failed."
    }

def blast(user, hostname, dataFile, clade, projectId):
    associated_file = dataFile.associated_fasta
    file_path = os.path.join(hostname, associated_file.name)
    appId = "blastn_app"
    envVariables = []
    if clade:
        envVariables.append({"key":"CLADE", "value":clade})
    print("RUN BLAST")
    print(file_path)
    job_params = {
        "name": "blastn",
        "appId": appId,
        "appVersion": "0.1",
        "fileInputs":[{
            "name": "sequence",
            "sourceUrl": file_path
        }],
        "parameterSet": {
            "envVariables": envVariables
        },
        "subscriptions": [{
            "description": "Submit to URL on job new status",
            "enabled": True,
            "eventCategoryFilter": "JOB_NEW_STATUS",
            "deliveryTargets": [{
                "deliveryMethod": "WEBHOOK",
                "deliveryAddress": hostname + "/check_job_status"
              }
            ],
            "ttlMinutes": 10080
        }]
    }
    job_uuid = submit_tapis_job(user, appId, job_params, projectId)
    if job_uuid:
        job = Job.objects.get(uuid=job_uuid) 
        blast_job = BlastJob.objects.create(job=job, data_file = dataFile, clade = clade if clade else 'default')
        print("BLAST JOB SUBMITTED")
        #while True:
        #    status = get_job_status(job_uuid)
        #    if status:
        #        current_status = status.get("status")
        #        print(current_status)
        #        if current_status == 'FINISHED':
        #            job_data = tapis.jobs.getJobOutputList(jobUuid=job_uuid, outputPath='/')
        #            if job_data:
        #                index = next((i for i, d in enumerate(job_data) if d.get('name') == "output.json"), -1)
        #                response_bytes = tapis.files.getContents(systemId="js2_tapis_test2", path=job_data[index].get("path"))
        #                response_string = response_bytes.decode('utf-8')
        #                response_json = json.loads(response_string)
        #                return response_json
        #            break
        #        elif current_status == 'FAILED':
        #            print("Job failed.")
        #            #print(status)
        #            break
        #        elif current_status == 'STOPPED':
        #            print("Job stopped.")
        #            #print(status)
        #            break
        #    time.sleep(5)
        return {
            "status": "success",
            "message": "Blast job running."
        }
    return {
        "status": "error",
        "message": "Blast job failed."
    }

def local_phylip_nj(user, hostname, muscle_data, outgroup, projectId):
    from .tasks import run_phylip_nj_task
    dataFile = muscle_data.associated_alignment
    appId = "phylip_nj_app"
    job_uuid = fake_tapis_job(user, appId, None, projectId)
    if job_uuid:
        job = Job.objects.get(uuid=job_uuid)
        phylipnj_job = PhylipNJJob.objects.create(job=job, muscle_data = muscle_data, outgroup = outgroup if outgroup else "")
        run_phylip_nj_task.delay(job.id, dataFile.name, outgroup)
        return {
            "status": "success",
            "message": "PHYLIP NJ job running."
        }
    return {
        "status": "error",
        "message": "PHYLIP NJ job failed."
    }

def local_phylip_ml(user, hostname, muscle_data, outgroup, projectId):
    from .tasks import run_phylip_ml_task
    dataFile = muscle_data.associated_alignment
    appId = "phylip_ml_app"
    job_uuid = fake_tapis_job(user, appId, None, projectId)
    if job_uuid:
        job = Job.objects.get(uuid=job_uuid)
        phylipml_job = PhylipMLJob.objects.create(job=job, muscle_data = muscle_data, outgroup = outgroup if outgroup else "")
        run_phylip_ml_task.delay(job.id, dataFile.name, outgroup)
        return {
            "status": "success",
            "message": "PHYLIP ML job running."
        }
    return {
        "status": "error",
        "message": "PHYLIP ML job failed."
    }

def phylip_nj(user, hostname, muscle_data, outgroup, projectId):
    dataFile = muscle_data.associated_alignment
    file_path = os.path.join(hostname, dataFile.name)
    appId = "phylip_nj_app"
    envVariables = []
    if outgroup:
        envVariables.append({"key":"DESIRED_OUTGROUP", "value":outgroup})
    print(file_path)
    job_params = {
        "name": "phylip_nj",
        "appId": appId,
        "appVersion": "0.1",
        "fileInputs":[{
            "name": "sequence",
            "sourceUrl": file_path
        }],
        "parameterSet": {
            "envVariables": envVariables
        },
        "subscriptions": [{
            "description": "Submit to URL on job new status",
            "enabled": True,
            "eventCategoryFilter": "JOB_NEW_STATUS",
            "deliveryTargets": [
              {
                "deliveryMethod": "WEBHOOK",
                "deliveryAddress": hostname + "/check_job_status"
              }
            ],
            "ttlMinutes": 10080
        }]
    }
    job_uuid = submit_tapis_job(user, appId, job_params, projectId)
    if job_uuid:
        job = Job.objects.get(uuid=job_uuid)
        phylipnj_job = PhylipNJJob.objects.create(job=job, muscle_data = muscle_data, outgroup = outgroup if outgroup else "")
        return {
            "status": "success",
            "message": "PHYLIP NJ job running"
        }
    return {
        "status": "error",
        "message": "PHYLIP NJ job failed."
    }
def phylip_ml(user, hostname, muscle_data, outgroup, projectId):
    dataFile = muscle_data.associated_alignment
    file_path = os.path.join(hostname, dataFile.name)
    appId = "phylip_ml_app"
    envVariables = []
    if outgroup:
        envVariables.append({"key":"DESIRED_OUTGROUP", "value":outgroup})
    print(file_path)
    job_params = {
            "name": "phylip_ml",
            "appId": appId,
            "appVersion": "0.1",
            "fileInputs":[{
                "name": "sequence",
                "sourceUrl": file_path
            }],
            "parameterSet": {
                "envVariables": envVariables
            },
            "subscriptions": [{
                "description": "Submit to URL on job new status",
                "enabled": True, "eventCategoryFilter": "JOB_NEW_STATUS",
                "deliveryTargets": [
                  {
                    "deliveryMethod": "WEBHOOK",
                    "deliveryAddress": hostname + "/check_job_status"
                  }
                ],
                "ttlMinutes": 10080
            }]
    }
    job_uuid = submit_tapis_job(user, appId, job_params, projectId)
    if job_uuid:
        job = Job.objects.get(uuid=job_uuid)
        phylipml_job = PhylipMLJob.objects.create(job=job, muscle_data = muscle_data, outgroup = outgroup if outgroup else "")
        return {
            "status": "success",
            "message": "PHYLIP ML job running"
        }
    return {
        "status": "error",
        "message": "PHYLIP ML job failed."
    }

def muscle(user, hostname, file_ids, projectId):
    file_path = os.path.join("muscle_files", str(projectId) + ".fasta")
    multi_seq_muscle_jobs(file_path, file_ids)
    file_path = os.path.join(hostname, file_path)
    appId = "muscle_app"
    print(file_path)
    job_params = {
            "name": "muscle",
            "appId": appId,
            "appVersion": "0.1",
            "fileInputs":[
              {
                "name": "sequence",
                "sourceUrl": file_path
              }
            ],
            "subscriptions": [
              {
                "description": "Submit to URL on job new status",
                "enabled": True,
                "eventCategoryFilter": "JOB_NEW_STATUS",
                "deliveryTargets": [
                  {
                    "deliveryMethod": "WEBHOOK",
                    "deliveryAddress": hostname + "/check_job_status"
                  }
                ],
                "ttlMinutes": 10080
              }
            ]
    }
    job_uuid = submit_tapis_job(user, appId, job_params, projectId)
    if job_uuid:
        job = Job.objects.get(uuid=job_uuid)
        muscle_job = MuscleJob.objects.create(job=job, associated_input = file_path)
        for file_id in file_ids:
            dataFile = DataFile.objects.get(id=file_id)
            muscle_file = MuscleFile.objects.create(muscle_job = muscle_job, data_file = dataFile)
        return {
            "status": "success",
        }
    return {
            "status": "error",
            "message": "MUSCLE job failed."
    }

def local_muscle(user, hostname, file_ids, projectId):
    from .tasks import process_alignment
    file_path = os.path.join("muscle_files", str(projectId) + ".fasta")
    multi_seq_muscle_jobs(file_path, file_ids)
    appId = "muscle_app"
    job_uuid = fake_tapis_job(user, appId, None, projectId)
    if job_uuid:
        job = Job.objects.get(uuid=job_uuid)
        muscle_job = MuscleJob.objects.create(job=job, associated_input = file_path)
        for file_id in file_ids:
            dataFile = DataFile.objects.get(id=file_id)
            muscle_file = MuscleFile.objects.create(muscle_job = muscle_job, data_file = dataFile)
        process_alignment.delay(file_path, muscle_job.id)
        return {
            "status": "success",
            "message": "MUSCLE job running."
        }
    return {
        "status": "error",
        "message": "MUSCLE job failed."
    }

def local_consense(hostname, file1, file2, file1_reverse, file2_reverse, project, pairLabel):
    return generate_consensus_from_datafile(hostname, file1, file2, file1_reverse, file2_reverse, project, pairLabel)

def consense(user, hostname, file1, file2, file1_reverse, file2_reverse, projectId):
    associated_file1 = file1.associated_abi if file1.associated_abi else file1.associated_fasta
    associated_file2 = file2.associated_abi if file2.associated_abi else file2.associated_fasta
    file1_path = os.path.join(hostname, associated_file1.name)
    file2_path = os.path.join(hostname, associated_file2.name)
    print(associated_file1.name)
    print(associated_file2.name)
    envVariables = []
    envVariables.append({"key":"FILE1_REVERSE", "value": file1_reverse})
    envVariables.append({"key":"FILE2_REVERSE", "value": file2_reverse})
    appId = "consense_app"
    job_params = {
            "name": "consense",
            "appId": appId,
            "appVersion": "0.4",
            "fileInputs":[
              {
                "name": "sequence_1",
                "sourceUrl": file1_path
              },
              {
                "name":"sequence_2",
                "sourceUrl":file2_path
              }
            ],
            "parameterSet": {
              "envVariables": envVariables
            },
            "subscriptions": [
              {
                "description": "Submit to URL on job new status",
                "enabled": True,
                "eventCategoryFilter": "JOB_NEW_STATUS",
                "deliveryTargets": [
                  {
                    "deliveryMethod": "WEBHOOK",
                    "deliveryAddress": hostname + "/check_job_status"
                  }
                ],
                "ttlMinutes": 10080
              }
            ]
    }
    job_uuid = submit_tapis_job(user, appId, job_params, projectId)
    if job_uuid:
        job = Job.objects.get(uuid=job_uuid)
        consense_job = ConsensusJob.objects.create(job=job,forward_file = file1, reverse_file = file2)
        return {
            "status": "success",
            "message": "Consensus job running"
        }
        #while True:
        #    status = get_job_status(job_uuid)
        #    if status:
        #        current_status = status.get("status")
        #        print(current_status)
        #        if current_status == 'FINISHED':
        #            job_data = tapis.jobs.getJobOutputList(jobUuid=job_uuid, outputPath='/')
        #            if job_data:
        #                index = next((i for i, d in enumerate(job_data) if d.get('name') == "output.json"), -1)
        #                response_bytes = tapis.files.getContents(systemId="js2_tapis_test2", path=job_data[index].get("path"))
        #                response_string = response_bytes.decode('utf-8')
        #                print(response_string)
        #                try:
        #                    response_json = json.loads(response_string)
        #                    return response_json
        #                except:
        #                    pass
        #            break
        #        elif current_status == 'FAILED':
        #            print("Job failed.")
        #            #print(status)
        #            break
        #        elif current_status == 'STOPPED':
        #            print("Job stopped.")
        #            #print(status)
        #            break
        #    time.sleep(5)
    return {
            "status": "error",
            "message": "Consensus job failed."
    }

def trim(sequence, forward_total, reverse_total):
    end_pos = len(sequence) - reverse_total
    trimmed_sequence = sequence[forward_total:end_pos]

    left_trim = sequence[:forward_total]
    right_trim = sequence[-reverse_total:] if reverse_total > 0 else ""
    start_pos = forward_total

    return left_trim, right_trim, start_pos, end_pos, trimmed_sequence

def trim_quality_scores(quality_scores):
    window_size = 18
    threshold = 22
    trim = 0

    for i in range(len(quality_scores)):
        window = quality_scores[i:i+window_size]
        window_sum = sum(window)
        window_avg = window_sum / window_size

        if window_avg < threshold:
            trim += 1
        else:
            return trim

    return trim

def trim_ambiguous_nucleotides(seq):
    window_length = 12
    threshold = 2
    total = 0

    for i in range(len(seq)):
        window = seq[i:i+window_length]
        cnt = window.count('N')

        if window.startswith('N') or cnt >= threshold:
            total += 1
        else:
            break

    return total

def suggested_trim(hostname, dataFile):
    associated_file = dataFile.associated_abi if dataFile.associated_abi else dataFile.associated_fasta
    file_path = hostname + "/" + associated_file.name
    message, sequence, _, _, quality_values = parse_reads(file_path)
    if message:
        print(message)
        return {
            "status": "error",
            "message": "Cannot read file."
        }
    qscore_trim_forward, qscore_trim_reverse = 0, 0
    forward_total, reverse_total = None, None

    if quality_values:
        qscore_trim_forward = trim_quality_scores(quality_values)
    forward_total = trim_ambiguous_nucleotides(sequence[qscore_trim_forward:])
    forward_total += qscore_trim_forward

    if quality_values:
        qscore_trim_reverse = trim_quality_scores(quality_values[::-1])
    reverse_total = trim_ambiguous_nucleotides(sequence[::-1][qscore_trim_reverse:])
    reverse_total += qscore_trim_reverse
    return {
        "status": "success",
        "left": forward_total,
        "right": reverse_total
    }

def local_sequence_trim(dataFile, left_trim_amount, right_trim_amount):
    left_trim, right_trim, start_pos, end_pos, trimmed_sequence = trim(dataFile.reads, left_trim_amount, right_trim_amount)
    dataFile.reads = trimmed_sequence
    dataFile.trim_start = dataFile.trim_start + start_pos if dataFile.trim_start else start_pos
    dataFile.trim_end = dataFile.trim_end - right_trim_amount if dataFile.trim_end else len(trimmed_sequence) + start_pos
    dataFile.left_trim = dataFile.left_trim + left_trim if dataFile.left_trim else left_trim
    dataFile.right_trim = right_trim + dataFile.right_trim if dataFile.right_trim else right_trim
    dataFile.save()
    header = re.sub(r'\s+', '_', dataFile.name)
    fasta_content = ContentFile(f">{header}\n{dataFile.reads}\n")
    fasta_file_name = f"{dataFile.id}.fasta"
    fasta_file_path = f"fasta_files/{fasta_file_name}"
    # Check if the file already exists and update it, or save if not
    if default_storage.exists(fasta_file_path):
        default_storage.delete(fasta_file_path) # Delete the old file if it exists
    fasta_file_path = default_storage.save(fasta_file_path, fasta_content)
    dataFile.associated_fasta.name = fasta_file_path
    dataFile.save()
    return {
        "status": "success",
        "message": "Trim job done."
    }

def undo_sequence_trim(dataFile):
    # Restore the original sequence by concatenating the trims and the reads
    restored_sequence = dataFile.left_trim + dataFile.reads + dataFile.right_trim

    # Reset trimming details
    dataFile.reads = restored_sequence
    dataFile.trim_start = None
    dataFile.trim_end = None
    dataFile.left_trim = ''
    dataFile.right_trim = ''
    dataFile.save()

    # Update the FASTA file with the restored sequence
    header = re.sub(r'\s+', '_', dataFile.name)
    fasta_content = ContentFile(f">{header}\n{restored_sequence}\n")
    fasta_file_name = f"{dataFile.id}.fasta"
    fasta_file_path = f"fasta_files/{fasta_file_name}"

    if default_storage.exists(fasta_file_path):
        default_storage.delete(fasta_file_path)

    fasta_file_path = default_storage.save(fasta_file_path, fasta_content)

    dataFile.associated_fasta.name = fasta_file_path
    dataFile.save()

    return {
        "status": "success",
        "message": "Undo trim successful, original sequence restored."
    }

def extract_sequence_and_quality(file_path, reverse=False):
    try:
        with open(file_path, "rb") as handle:
            seq_record = next(SeqIO.parse(handle, "abi"))
    except (OSError, ValueError):
        try:
            with open(file_path, "r") as handle:
                seq_record = next(SeqIO.parse(handle, "fastq"))
        except (OSError, ValueError):
            with open(file_path, "r") as handle:
                seq_record = next(SeqIO.parse(handle, "fasta"))

    if reverse:
        seq_record.seq = seq_record.seq.reverse_complement()
        if "phred_quality" in seq_record.letter_annotations:
            seq_record.letter_annotations["phred_quality"].reverse()

    return str(seq_record.seq), seq_record.letter_annotations["phred_quality"] if "phred_quality" in seq_record.letter_annotations else None, seq_record

def rename_fasta_headers(fasta_file, new_header):
    """Renames headers in a FASTA file and returns a path to the modified file."""
    temp_file = tempfile.NamedTemporaryFile(mode='w+', delete=False)
    try:
        with open(fasta_file, 'r') as infile, temp_file:
            for line in infile:
                if line.startswith('>'):
                    temp_file.write(f">{new_header}\n")
                else:
                    temp_file.write(line)
        return temp_file.name
    except Exception as e:
        os.remove(temp_file.name)
        raise e

def run_merger(forward_file, reverse_file):
    # Rename headers for forward and reverse files
    renamed_forward_file = rename_fasta_headers(forward_file, "forward")
    renamed_reverse_file = rename_fasta_headers(reverse_file, "reverse")
    # Create a temporary file using tempfile for the output
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as temp_outfile:
        temp_outfile_name = temp_outfile.name

    try:
        # Run the merger process
        process = subprocess.run(
            [
                "merger",
                "-asequence", renamed_forward_file,
                "-bsequence", renamed_reverse_file,
                "-sreverse2", "Y",
                "-outfile", temp_outfile_name,  # Use the temp file for outfile
                "-outseq", "stdout",            # Capture standard output for outseq
                "--auto"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

        if process.returncode != 0:
            raise Exception(f"Merger failed: {process.stderr}")

        # Read the content of the temporary output file
        with open(temp_outfile_name, "r") as temp_outfile:
            outfile_content = temp_outfile.read().strip()

        # Get the stdout output (outseq)
        outseq_output = process.stdout.strip()

    finally:
        # Clean up the temporary file
        import os
        os.remove(temp_outfile_name)
        os.remove(renamed_forward_file)
        os.remove(renamed_reverse_file)

    return outfile_content, outseq_output  # Return both outputs separately

def run_muscle(reverse_seqname, reverse, forward_seqname, forward, consensus, consensus_seqname):
    with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.fasta') as pair_file:
        # Write the sequences to the temporary file
        pair_file.write(f">{reverse_seqname}\n")
        pair_file.write(f"{reverse}\n")
        pair_file.write(f">{forward_seqname}\n")
        pair_file.write(f"{forward}\n")
        pair_file.write(f">{consensus_seqname}\n")
        pair_file.write(f"{consensus}\n")
        # Move the file pointer to the beginning for reading
        pair_file.seek(0)

        # Create a temporary file for aligned sequences
        with tempfile.NamedTemporaryFile(mode='w+', delete=True, suffix='.fasta') as muscle_output_file:
            # Run MUSCLE and write the output to the temporary file
            subprocess.run(
                ["muscle", "-align", pair_file.name, "-output", muscle_output_file.name],
                check=True
            )
            # Move the file pointer to the beginning to read the aligned sequences
            muscle_output_file.seek(0)
            aligned_sequences = muscle_output_file.read()

    return aligned_sequences

def fix_consensus_from_file(forward_alignment, reverse_alignment, consensus, forward_record, reverse_record, left_trim_forward, right_trim_forward, left_trim_reverse, right_trim_reverse):
    consensus_list = list(consensus)
    forward_alignment_list = list(forward_alignment)
    reverse_alignment_list = list(reverse_alignment)

    # Get quality scores
    qs1 = forward_record.letter_annotations.get("phred_quality", [0] * len(forward_record))
    qs2 = reverse_record.letter_annotations.get("phred_quality", [0] * len(reverse_record))
    qs2 = list(reversed(qs2))

    # Trim the quality scores based on respective left and right trims
    trimmed_qs1 = qs1[left_trim_forward:right_trim_forward]
    trimmed_qs2 = qs2[left_trim_reverse:right_trim_reverse]
    trimmed_qs2 = list(reversed(trimmed_qs2))

    def apply_gaps_to_quality(alignment, trimmed_qs):
        gapped_qs = []
        q_idx = 0
        for base in alignment:
            if base == '-':
                gapped_qs.append(-1)
            else:
                gapped_qs.append(trimmed_qs[q_idx] if q_idx < len(trimmed_qs) else -1)
                q_idx += 1
        return gapped_qs

    gapped_qs1 = apply_gaps_to_quality(forward_alignment_list, trimmed_qs1)
    gapped_qs2 = apply_gaps_to_quality(reverse_alignment_list, trimmed_qs2)

    for i in range(len(consensus)):
        chr1 = forward_alignment_list[i].upper()
        chr2 = reverse_alignment_list[i].upper()
        qs1_value = gapped_qs1[i]
        qs2_value = gapped_qs2[i]
        if chr1 == "-":
            consensus_list[i] = chr2
        elif chr2 == "-":
            consensus_list[i] = chr1
        elif chr1 != "N" and chr2 != "N":
            if chr1 != chr2:
                if qs1_value > qs2_value:
                    consensus_list[i] = chr1 if chr1 != '-' else consensus_list[i]
                else:
                    consensus_list[i] = chr2 if chr2 != '-' else consensus_list[i]
        elif chr1 != "N" and chr2 == "N":
            consensus_list[i] = chr1
        elif chr2 != "N" and chr1 == "N":
            consensus_list[i] = chr2
    return ''.join(consensus_list)

def extract_consensus(merger_outseq):
    # Split the input data into lines
    lines = merger_outseq.strip().splitlines()

    # Initialize an empty string to accumulate the sequence
    consensus_sequence = ""

    # Iterate over each line
    for line in lines:
        # Skip the header line that starts with '>'
        if line.startswith(">"):
            continue
        # Append the sequence lines to the consensus_sequence
        consensus_sequence += line.strip().upper()  # Remove any leading/trailing whitespace

    return consensus_sequence

def extract_aligned_sequences(merger_output):
    # Regex pattern to match aligned sequences with any name
    aligned_sequences_pattern = re.compile(
        r'^(?!#)(?P<label>\S+)\s+\d+\s+(?P<sequence>[ -~]+)\s+\d+$',
        re.MULTILINE
    )

    # Find aligned sequences
    aligned_sequences = aligned_sequences_pattern.findall(merger_output)

    # Initialize lists to store alignments
    alignments = []
    current_alignment = []
    align_1 = ""
    align_2 = ""

    # Process the extracted sequences
    for i in range(len(aligned_sequences)):
        label, seq = aligned_sequences[i]

        # Append the current sequence to the current alignment
        current_alignment.append((label.strip(), seq.strip()))

        # Check for the next entry to see if it belongs to the same alignment
        if (i + 1 < len(aligned_sequences) and
            aligned_sequences[i + 1][0] != label):  # If next label is different, finalize the current alignment
            alignments.append(current_alignment)
            current_alignment = []

    # Append the last alignment if it exists
    if current_alignment:
        alignments.append(current_alignment)

    for i, alignment in enumerate(alignments, start=1):
        for label, seq in alignment:
            if i % 2 == 1:
                align_1 += seq.strip().upper()
            else:
                align_2 += seq.strip().upper()
    return align_1, align_2

def extract_alignments(aligned_sequences):
    # Split the aligned sequences by '>'
    muscle_output_array = aligned_sequences.split(">")[1:]  # Skip the first empty element

    # Initialize a dictionary to hold the alignments
    alignments = {}

    for output in muscle_output_array:
        # Split each section into lines
        alignment_list = output.strip().split("\n")
        alignment_name = alignment_list.pop(0)  # Get the name
        alignment_sequence = "".join(alignment_list)  # Join the sequence lines

        # Store in the dictionary
        alignments[alignment_name] = alignment_sequence

    # Return the extracted alignments
    return alignments

def generate_consensus_from_datafile(hostname, datafile_1, datafile_2, datafile_1_reverse, datafile_2_reverse, project, pairLabel):
    consensus_seq_name = pairLabel if pairLabel else common_prefix_clean(datafile_1.name, datafile_2.name)
    existing = ProjectDataFile.objects.filter(
        project=project,
        data_file__name=consensus_seq_name
    ).exists()

    if existing and pairLabel:
        return {
            "status": "error",
            "message": f"Sequence with name {consensus_seq_name} already exists, choose a different name for this consensus."
        }
    if existing:
        for i in range(1, 11):
            new_name = f"{consensus_seq_name}-{i}"
            if not ProjectDataFile.objects.filter(project=project, data_file__name=new_name).exists():
                consensus_seq_name = new_name
                break
        else:
                return {
                    "status": "error",
                    "message": f"Sequence with name {consensus_seq_name} and {consensus_seq_name}-1 through -10 already exist. Please rename your consensus."
                }
    associated_file1 = datafile_1.associated_abi if datafile_1.associated_abi else datafile_1.associated_fasta
    associated_fasta1 = datafile_1.associated_fasta
    fasta_path_1 = os.path.join(hostname, associated_file1.name)
    associated_file2 = datafile_2.associated_abi if datafile_2.associated_abi else datafile_2.associated_fasta
    associated_fasta2 = datafile_2.associated_fasta
    fasta_path_2 = os.path.join(hostname, associated_file2.name)
    _, _, record_1 = extract_sequence_and_quality(associated_file1.path, reverse=datafile_1_reverse)
    _, _, record_2 = extract_sequence_and_quality(associated_file2.path, reverse=datafile_2_reverse)
    if datafile_1_reverse and not datafile_2_reverse:
        forward_record = record_2
        reverse_record = record_1
        forward_fasta_path = associated_fasta2.path
        reverse_fasta_path = associated_fasta1.path
        forward_datafile = datafile_2
        reverse_datafile = datafile_1
    elif not datafile_1_reverse and datafile_2_reverse:
        forward_record = record_1
        reverse_record = record_2
        forward_fasta_path = associated_fasta1.path
        reverse_fasta_path = associated_fasta2.path
        forward_datafile = datafile_1
        reverse_datafile = datafile_2
    else:
        forward_record = record_1
        reverse_record = record_2
        datafile_1_reverse = False
        datafile_2_reverse = True
        forward_fasta_path = associated_fasta1.path
        reverse_fasta_path = associated_fasta2.path
        forward_datafile = datafile_1
        reverse_datafile = datafile_2

    outfile_content, outseq_content = run_merger(forward_fasta_path, reverse_fasta_path)
    original_consensus = extract_consensus(outseq_content)
    original_forward_alignment, original_reverse_alignment = extract_aligned_sequences(outfile_content)
    muscle_output = run_muscle(reverse_datafile.name, original_reverse_alignment, forward_datafile.name, original_forward_alignment, original_consensus, consensus_seq_name)
    alignments = extract_alignments(muscle_output)
    forward_alignment = alignments.get(forward_datafile.name, "")
    reverse_alignment = alignments.get(reverse_datafile.name, "")
    consensus = alignments.get(consensus_seq_name, "")

    left_trim_forward = forward_datafile.trim_start or 0
    right_trim_forward = forward_datafile.trim_end or 0
    left_trim_reverse = reverse_datafile.trim_start or 0
    right_trim_reverse = reverse_datafile.trim_end or 0

    fixed_consensus = fix_consensus_from_file(forward_alignment, reverse_alignment, consensus, forward_record, reverse_record, left_trim_forward, right_trim_forward, left_trim_reverse, right_trim_reverse)

    #muscle_output = run_muscle(pair_fasta_path)
    #muscle_output_array = muscle_output.split(">")
    #muscle_output_array.pop(0)

    #consensus_output = muscle_output_array[0].split("\n")
    #consensus_name = consensus_output.pop(0)
    #consensus = "".join(consensus_output)

    #reverse_alignment_output = muscle_output_array[1].split("\n")
    #reverse_alignment_name = reverse_alignment_output.pop(0)
    #reverse_alignment = "".join(reverse_alignment_output)

    #forward_alignment_output = muscle_output_array[2].split("\n")
    #forward_alignment_name = forward_alignment_output.pop(0)
    #forward_alignment = "".join(forward_alignment_output)


    # Save the consensus as a new DataFile instance (adjust as needed)
    consensus_file = DataFile.objects.create(
        source="consensus",
        user=datafile_1.user,
        name=consensus_seq_name,
        trace_exists=False,
        reads=fixed_consensus,
        read_type="C"
    )
    consensus_file.forward_read = forward_datafile
    consensus_file.reverse_read = reverse_datafile
    header = re.sub(r'\s+', '_', consensus_file.name)
    content = ContentFile(f">{header}\n{consensus_file.reads}\n")
    fasta_file_name = f"{consensus_file.id}.fasta"
    fasta_file_path = default_storage.save(f"fasta_files/{fasta_file_name}", content)
    consensus_file.associated_fasta.name = fasta_file_path
    consensus_file.save()
    consense_project_file = ProjectDataFile.objects.create(
        project = project,
        data_file = consensus_file,
    )
    ConsensusData.objects.create(
        consensus = consensus_file,
        forward_align = forward_alignment,
        reverse_align = reverse_alignment
    )

    return {
        "status": "success",
        "message": "Consensus created",
    }
