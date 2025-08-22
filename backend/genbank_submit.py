#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GenBank submission pipeline.

What this file provides:
- A GenbankSubmission class with methods used by run():
    create_dir, create_fasta, create_smt, make_template, create_feature_table,
    run_table2asn, prep_submission_file, submit, validate_submission,
    plus helpers: _parse_json, _get_primer_sequence, _change_status,
    _email_user, _email_admin.

Django settings schema (required keys):
{
  "GB_SUBMISSION_DIR": "/path/to/work/dir",
  "GB_TBL2ASN_TEMPLATE_PATH": "/path/to/master_template.sbt",
  "GB_FTP_USER": "...",
  "GB_FTP_PW": "...",
  "GB_VALIDATION_USER": "...",
  "GB_FTP_PASSIVE": True/False,
  "BARCODING_PROJECTS": {"Urban Barcode Project": "PRJNAxxxxxx", "Barcode Long Island": "PRJNAyyyyyy", ...},
  "PRIMERS": {
      "forward": {"rbcL": {"name": "LCO1490", "seq": "GGTCAACAAATCATAAAGATATTGG"}, ...},
      "reverse": {"rbcL": {"name": "HCO2198", "seq": "TAAACTTCAGGGTGACCAAAAAATCA"}, ...}
  }
}

GenbankRecord object protocol (what `run()` expects):
- record.email:  str
- record.sequence_id:  str|int
- record.specimen_id:  str
- record.seq_type:   str
- record.data:         str  (JSON with keys used below)
- record.status:       str  (set by _change_status)
- record.update():     method (no-op ok)

JSON in record.data should include (as in Perl):
{
  "genus": "Genus",
  "species": "species",
  "trans_table": 1,
  "project": "UBP",
  "isolation_source": "...",
  "host": "...",
  "tax": "Identified By",
  "date_collected": "DD/MM/YYYY",
  "country": "Country",
  "state": "State/Region",
  "city": "City",
  "site_desc": "Site description",
  "latitude": "12.3456",
  "longitude": "-78.9012",
  "sex": "female",
  "stage": "adult",
  "f_primer": "FWD_ID",
  "r_primer": "REV_ID",
  "author_first1": "Alice", "author_last1": "Smith",
  "author_first3": "Bob",   "author_last3": "Jones"
  ...
}
"""

import json
import os
import requests
import tarfile
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Any, Optional, Tuple, List
from ftplib import FTP
from urllib.request import urlopen
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET
from django.conf import settings


# ---------- Minimal record implementation (optional helper) ----------
@dataclass
class GenbankRecord:
    email: str
    sequence_id: Any
    specimen_id: str
    seq_type: str
    data: str
    consensus: str
    status: str = "pending"

    def update(self):
        # Placeholder: in real usage, persist status change to DB
        pass


# ---------- Pipeline class ----------
class GenbankSubmission:
    def __init__(
        self,
    ):
        self.work_dir = Path(settings.GB_SUBMISSION_DIR)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.tbl2asn_template = Path(settings.GB_TBL2ASN_TEMPLATE_PATH)
        self.ftp_user = settings.GB_FTP_USER
        self.ftp_pw = settings.GB_FTP_PW
        self.ftp_passive = bool(settings.GB_FTP_PASSIVE)
        self.validation_user = settings.GB_VALIDATION_USER
        self.barcoding_projects = settings.BARCODING_PROJECTS
        self.primers = settings.PRIMERS
        self.cwd = Path.cwd()

    # --- Genetic Code Dictionaries ---
    standard_code: Dict[str, str] = {
        'TCA': 'S', 'TCC': 'S', 'TCG': 'S', 'TCT': 'S',
        'TTC': 'F', 'TTT': 'F', 'TTA': 'L', 'TTG': 'L',
        'TAC': 'Y', 'TAT': 'Y', 'TAA': '*', 'TAG': '*',
        'TGC': 'C', 'TGT': 'C', 'TGA': '*', 'TGG': 'W',
        'CTA': 'L', 'CTC': 'L', 'CTG': 'L', 'CTT': 'L',
        'CCA': 'P', 'CCC': 'P', 'CCG': 'P', 'CCT': 'P',
        'CAC': 'H', 'CAT': 'H', 'CAA': 'Q', 'CAG': 'Q',
        'CGA': 'R', 'CGC': 'R', 'CGG': 'R', 'CGT': 'R',
        'ATA': 'I', 'ATC': 'I', 'ATT': 'I', 'ATG': 'M',
        'ACA': 'T', 'ACC': 'T', 'ACG': 'T', 'ACT': 'T',
        'AAC': 'N', 'AAT': 'N', 'AAA': 'K', 'AAG': 'K',
        'AGC': 'S', 'AGT': 'S', 'AGA': 'R', 'AGG': 'R',
        'GTA': 'V', 'GTC': 'V', 'GTG': 'V', 'GTT': 'V',
        'GCA': 'A', 'GCC': 'A', 'GCG': 'A', 'GCT': 'A',
        'GAC': 'D', 'GAT': 'D', 'GAA': 'E', 'GAG': 'E',
        'GGA': 'G', 'GGC': 'G', 'GGG': 'G', 'GGT': 'G'
    }
    
    invertebrate_code = {
        'AGA': 'S', 'AGG': 'S', 'ATA': 'M', 'TGA': 'W'
    }
    
    vertebrate_code = {
        'AGA': '*', 'AGG': '*', 'ATA': 'M', 'TGA': 'W'
    }
    
    echinoderm_code = {
        'AGA': 'S', 'AGG': 'S', 'AAA': 'N', 'TGA': 'W'
    }
    
    
    def merge_dicts(self, base: Dict[str, str], override: Dict[str, str]) -> Dict[str, str]:
        """Return a copy of base with override applied."""
        merged = dict(base)
        merged.update(override)
        return merged
    
    
    def get_trans_code(self, trans_table: int) -> Dict[str, str]:
        """Return codon → amino acid mapping based on translation table."""
        if trans_table == 2:
            return self.merge_dicts(self.standard_code, self.vertebrate_code)
        elif trans_table == 5:
            return self.merge_dicts(self.standard_code, self.invertebrate_code)
        elif trans_table == 9:
            return self.merge_dicts(self.standard_code, self.echinoderm_code)
        return self.standard_code
    
    
    def get_gcode(self, codon: str, genetic_code: Dict[str, str]) -> str:
        """Return amino acid for a codon."""
        if codon in genetic_code:
            return genetic_code[codon]
        elif len(codon) < 3:
            return " "
        else:
            return "X"
    
    
    def translate(self, seq: str, orf: int, trans_table: int) -> Tuple[str, List[Tuple[int, str]], List[Tuple[int, str]]]:
        """Translate DNA sequence into protein with stop/start tracking."""
        if orf not in (1, 2, 3):
            raise ValueError(f"Invalid ORF: {orf}")
    
        seq = seq.upper()
        protein = []
        stop_codons = []
        start_codons = []
    
        pos = orf - 1
        genetic_code = self.get_trans_code(trans_table)
    
        while pos < len(seq):
            codon = seq[pos:pos+3]
            aa = self.get_gcode(codon, genetic_code)
    
            if aa == "*":
                stop_codons.append((pos + 1, codon))
            elif aa == "M":
                start_codons.append((pos + 1, codon))
    
            protein.append(aa)
            pos += 3
    
        return "".join(protein), start_codons, stop_codons
    
    
    def annotate_barcode(self, seq: str, primer: str, organism: str,
                         trans_table: int, isolation_source: str = "",
                         host: str = "") -> str:
        """Return GenBank-style annotation for rbcL/COI barcode genes."""
        seq = seq.upper()
        seq = "".join([c for c in seq if c in "CGTAN"])
    
        orf = 0
        translation = ""
        seq_len = len(seq)
    
        for frame in (1, 2, 3):
            ts, starts, stops = self.translate(seq, frame, trans_table)
            if not stops:
                orf = frame
                translation = ts
                break
    
        if not orf:
            raise RuntimeError("No valid ORF found for annotation")
    
        if "RBCL" in primer.upper():
            gene = "rbcL"
            organelle = "plastid:chloroplast"
            product_full = "ribulose-1,5-bisphosphate carboxylase/oxygenase large subunit"
        elif "COI" in primer.upper() or "CO1" in primer.upper():
            gene = "COI"
            organelle = "Mitochondria"
            product_full = "cytochrome c oxidase subunit I"
        else:
            raise ValueError(f"Unsupported primer: {primer}")
    
        annotation = []
        annotation.append(f"<1\t>{seq_len}\tgene")
        annotation.append(f"\t\t\t\tgene\t{gene}")
        annotation.append(f"<1\t>{seq_len}\tCDS")
        annotation.append(f"\t\t\t\tgene\t{gene}")
        annotation.append(f"\t\t\t\tcodon_start\t{orf}")
        annotation.append(f"\t\t\t\ttransl_table\t{trans_table}")
        annotation.append(f"\t\t\t\tproduct\t{product_full}")
        annotation.append(f"\t\t\t\ttranslation\t{translation}")
    
        return "\n".join(annotation)

    # -----------------------------------------
    # Parse JSON data from DB (metadata)
    def _parse_json(self, json_data: str) -> Dict[str, Any]:
        return json.loads(json_data or "{}")

    # ---------------------------------------
    # Create directory for each submission
    def create_dir(self, record: GenbankRecord) -> None:
        d = self.work_dir / str(record.sequence_id)
        d.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------
    # Get sequences and create FASTA files
    def create_fasta(self, record: GenbankRecord) -> Dict[str, Any]:
        seq_id = record.sequence_id
        specimen_id = record.specimen_id

        data = self._parse_json(record.data)
        organism = f"{data.get('genus','')} {data.get('species','')}".strip()
        trans_table = int(data.get("trans_table", 1))

        bioproject_name = data.get("project", "")
        bioproject_id = self.barcoding_projects.get(bioproject_name)
        if not bioproject_id:
            msg = f"Unknown BioProject for project '{bioproject_name}'"
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

        seq = record.consensus
        seq = seq.replace("-", "")

        fasta_path = self.work_dir / str(seq_id) / f"{specimen_id}.fsa"
        try:
            with open(fasta_path, "w") as fh:
                header = f">{specimen_id} [BioProject={bioproject_id}] [tech=barcode] [organism={organism}]"
                if trans_table != 1:
                    header += f" [mgcode={trans_table}] [location=mitochondrion]"
                fh.write(header + "\n")
                fh.write(seq)
            print(f"Fasta file created: {fasta_path}")
            return {"status": "success"}
        except Exception as e:
            msg = f"FAILED to create fasta file: {e}"
            print(msg)
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

    # ---------------------------------------
    # Creates the Source Modifier Table (.src)
    def create_smt(self, record: GenbankRecord) -> Dict[str, Any]:
        seq_id = record.sequence_id
        specimen_id = record.specimen_id
        data = self._parse_json(record.data)
        data_hash = dict(data)

        # Collect author names from keys like author_firstN / author_lastN
        names: Dict[str, Dict[str, str]] = {}
        for k, v in data_hash.items():
            if k.startswith("author_first") or k.startswith("author_last"):
                # extract suffix digits
                digits = "".join(ch for ch in k if ch.isdigit())
                if not digits:
                    continue
                entry = names.setdefault(digits, {})
                if "first" in k:
                    entry["first"] = v
                else:
                    entry["last"] = v

        names_new = ""
        for person_id in sorted(names, key=lambda x: int(x)):
            first = names[person_id].get("first", "")
            last = names[person_id].get("last", "")
            if first or last:
                names_new += f"{first} {last}, "

        # Primer lookups (mirrors original behavior; note Perl used f_primer twice for reverse)
        f_info = self._get_primer_sequence("forward", data.get("f_primer"))
        r_info = self._get_primer_sequence("reverse", data.get("r_primer") or data.get("f_primer"))
        if not f_info or not r_info:
            msg = "Primer lookup failed"
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

        # Date conversion DD/MM/YYYY -> D-Mon-YYYY
        months = {
            "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
            "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
            "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
        }
        date_collected = data.get("date_collected", "")
        try:
            d, m, y = date_collected.split("/")
            date_str = f"{d}-{months.get(m,m)}-{y}"
        except Exception:
            date_str = date_collected  # leave as-is if unexpected

        columns = [
            "Sequence_ID", "Isolate", "Isolation_source", "Identified_by", "Collected_by",
            "Collection_date", "Country", "Lat_Lon", "Sex", "Dev_stage",
            "Fwd_primer_name", "Fwd_primer_seq", "Rev_primer_name", "Rev_primer_seq"
        ]
        country_field = f"{data.get('country','')}: {data.get('state','')}, {data.get('city','')}, {data.get('site_desc','')}"
        latlon = f"{data.get('latitude','')} {data.get('longitude','')}"
        row = [
            specimen_id, specimen_id, data.get("isolation_source",""), data.get("tax",""),
            names_new.strip().rstrip(","), date_str, country_field, latlon,
            data.get("sex",""), data.get("stage",""),
            f_info.get("name",""), f_info.get("seq",""),
            r_info.get("name",""), r_info.get("seq",""),
        ]

        smt_path = self.work_dir / str(seq_id) / f"{specimen_id}.src"
        try:
            with open(smt_path, "w") as fh:
                fh.write("\t".join(columns) + "\n")
                fh.write("\t".join(row))
            print(f"SMT file created: {smt_path}")
            return {"status": "success"}
        except Exception as e:
            msg = f"FAILED to create smt file: {e}"
            print(msg)
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

    # ---------------------------------------
    # Primer lookup from config
    def _get_primer_sequence(self, strand: str, primer_id: Optional[str]) -> Optional[Dict[str, str]]:
        if not primer_id:
            return None
        try:
            info = self.primers[strand][primer_id]
            return {"name": info["name"], "seq": info["seq"]}
        except Exception:
            return None

    # ---------------------------------------
    # Make template.sbt for this submission
    def make_template(self, record: GenbankRecord) -> Dict[str, Any]:
        data = self._parse_json(record.data)
        seq_id = record.sequence_id

        # Build author block in template format
        # {name name {last "Last", first "First", initials "F.", suffix ""}},
        names: Dict[str, Dict[str, str]] = {}
        for k, v in data.items():
            if k.startswith("author_first") or k.startswith("author_last"):
                digits = "".join(ch for ch in k if ch.isdigit())
                if not digits:
                    continue
                entry = names.setdefault(digits, {})
                if "first" in k:
                    entry["first"] = v
                else:
                    entry["last"] = v

        names_left = len(names)
        lines = []
        for person_id in sorted(names, key=lambda x: int(x)):
            first = names[person_id].get("first", "")
            last = names[person_id].get("last", "")
            initial = (first[:1].upper() + ".") if first else ""
            comma = "," if names_left > 1 else ""
            lines.append(f'{{name name {{last "{last}", first "{first}", initials "{initial}", suffix ""}}}}{comma}')
            names_left -= 1
        names_block = "\n".join(lines)

        try:
            sbt = self.tbl2asn_template.read_text()
            sbt = sbt.replace("__$NAMES__", names_block)
            out_path = self.work_dir / str(seq_id) / "template.sbt"
            out_path.write_text(sbt)
            print(f"template.sbt file created: {out_path}")
            return {"status": "success"}
        except Exception as e:
            msg = f"FAILED to create sbt template file: {e}"
            print(msg)
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

    # ---------------------------------------
    # Create Feature Table (.tbl) using injected annotator
    def create_feature_table(self, record: GenbankRecord) -> Dict[str, Any]:
        seq_id = record.sequence_id
        specimen_id = record.specimen_id
        primer = record.seq_type

        seq = (record.consensus or "").replace("-", "")

        data = self._parse_json(record.data)
        organism = f"{data.get('genus','')} {data.get('species','')}".strip()
        trans_table = int(data.get("trans_table", 1))
        isolation_source = data.get("isolation_source", "")
        host = data.get("host", "")

        try:
            annotation = self.annotate_barcode(seq, primer, organism, trans_table, isolation_source, host)
        except Exception as e:
            msg = f"Annotation generation failed: {e}"
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

        if annotation:
            tbl_path = self.work_dir / str(seq_id) / f"{specimen_id}.tbl"
            try:
                with open(tbl_path, "w") as fh:
                    fh.write(f">Feature {specimen_id} Table1\n")
                    fh.write(annotation)
                print(f"Feature Table created: {tbl_path}")
                return {"status": "success"}
            except Exception as e:
                msg = f"FAILED to create Feature Table: {e}"
                print(msg)
                self._email_admin(record, msg)
                return {"status": "error", "message": msg}
        else:
            msg = "FAILED to generate annotation"
            print(msg)
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

    # ---------------------------------------
    # Run table2asn (preferred over tbl2asn)
    def run_table2asn(self, record: GenbankRecord) -> Dict[str, Any]:
        seq_id = record.sequence_id
        work_dir = str(self.work_dir / str(seq_id))
        template = str(self.work_dir / str(seq_id) / "template.sbt")
        output = str(self.work_dir / str(seq_id) / "genbank.asn")

        cmd = ["/usr/local/bin/table2asn.linux64", "-t", template, "-indir", work_dir, "-o", output]
        print("table2asn command:", " ".join(cmd))
        try:
            status = subprocess.run(cmd, check=False)
            if status.returncode != 0:
                msg = "FAILED: table2asn"
                print(msg)
                self._email_admin(record, msg)
                return {"status": "error", "message": msg}
            print(f"table2asn ran successfully, created file: {output}")
            return {"status": "success"}
        except Exception as e:
            msg = f"FAILED: table2asn execution error: {e}"
            print(msg)
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

    # ---------------------------------------
    # Wrap genbank.asn in a tar file for submission
    def prep_submission_file(self, record: GenbankRecord) -> Dict[str, Any]:
        specimen_id = record.specimen_id
        seq_id = record.sequence_id
        work = self.work_dir / str(seq_id)
        asn = work / "genbank.asn"
        tar_path = work / f"{specimen_id}.tar"

        if not asn.is_file():
            msg = f"genbank.asn not found at {asn}"
            print(msg)
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

        try:
            # Create a tar with a single member: genbank.asn
            with tarfile.open(tar_path, "w") as tar:
                tar.add(asn, arcname="genbank.asn")
            print(f"Final tar file created: {tar_path}")
            return {"status": "success"}
        except Exception as e:
            msg = f"FAILED to create final tar file: {e}"
            print(msg)
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

    # ---------------------------------------
    # FTP the submission file
    def submit(self, record: GenbankRecord) -> Dict[str, Any]:
        specimen_id = record.specimen_id
        seq_id = record.sequence_id
        work = self.work_dir / str(seq_id)
        tar_path = work / f"{specimen_id}.tar"
        if not tar_path.is_file():
            msg = f"Submission file not found: {tar_path}"
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

        try:
            ftp = FTP("ftp-private.ncbi.nlm.nih.gov", timeout=60)
            ftp.set_pasv(self.ftp_passive)
            ftp.login(self.ftp_user, self.ftp_pw)
            with open(tar_path, "rb") as fh:
                ftp.storbinary(f"STOR {tar_path.name}", fh)
            # Optional: list to confirm presence
            listing = []
            ftp.retrlines("LIST", listing.append)
            found = any(tar_path.name in line for line in listing)
            print("**:", next((line for line in listing if tar_path.name in line), "not listed"))
            ftp.quit()
            if not found:
                msg = "ftp put may have failed: file not listed"
                self._email_admin(record, msg)
                return {"status": "error", "message": msg}
            return {"status": "success"}
        except Exception as e:
            msg = f"FTP error: {e}"
            print(msg)
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

    # ---------------------------------------
    # Validate submission via NCBI WebSub API
    def validate_submission(self, record: GenbankRecord) -> Dict[str, Any]:
        specimen_id = record.specimen_id
        user = self.validation_user
        url = f"https://www.ncbi.nlm.nih.gov/WebSub/api/?user={user}&file={specimen_id}.tar"
        try:
            with urlopen(url, timeout=30) as resp:
                content = resp.read()
        except (HTTPError, URLError) as e:
            msg = f"Validation request failed: {e}"
            print(msg)
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

        try:
            root = ET.fromstring(content)
            # Expect <response><code>PASS|FAIL|PASS_WITH_WARNINGS</code>...</response>
            code_el = root.find(".//code")
            code = code_el.text.strip() if code_el is not None and code_el.text else "UNKNOWN"
        except Exception as e:
            msg = f"Failed to parse validation XML: {e}"
            print(msg)
            self._email_admin(record, msg)
            return {"status": "error", "message": msg}

        if code == "FAIL":
            print("FAILED VALIDATION")
            self._email_admin(record, "FAILED Validation - check email")
            return {"status": "error", "message": "FAILED Validation - check email"}
        elif code == "PASS_WITH_WARNINGS":
            print("PASSED WITH WARNINGS")
            self._change_status(record, "Passed With Warnings")
            self._email_user(record)
            return {"status": "success"}
        elif code == "PASS":
            print("PASSED VALIDATION!")
            self._change_status(record, "Passed validation")
            self._email_user(record)
            return {"status": "success"}
        else:
            msg = f"Unencountered response code: {code}"
            print(msg)
            self._email_admin(record, f"Unencountered response code: {code} - check email")
            return {"status": "error", "message": msg}

    # ----------------------------------------
    # Change status helper
    def _change_status(self, record: GenbankRecord, status: str) -> None:
        record.status = status
        record.update()

    # ---------------------------------------
    # Email helpers

    def send_email(self, to_email, subject, text):
        try:
            # Mailgun API endpoint
            url = f"https://api.mailgun.net/v3/{getattr(settings, 'MAILGUN_DOMAIN')}/messages"

            # Mailgun API credentials
            api_key = getattr(settings, 'MAILGUN_API_KEY')
            from_email = getattr(settings, 'MAILGUN_FROM_EMAIL')

            data = {
                'from': from_email,
                'to': to_email,
                'subject': subject,
                'text': text,
            }

            # Make POST request to Mailgun API
            response = requests.post(url, auth=('api', api_key), data=data)

            # Check if the email was successfully sent
            if response.status_code == 200:
                print("Email sent successfully")
            else:
                print("Failed to send email")
                return False
        except Exception as e:
            print(f"Error sending email: {e}")
            return False

        return True

    def _email_user(self, record: GenbankRecord) -> None:
        # In Perl, this looked up a user email from the DB. Here, we just notify.
        subject = "Your DNA Subway Submission to GenBank"
        message = (
            "Thank you for submitting your sequence. This email is to confirm that your submission "
            "has been processed successfully. This does not mean your sequence has been accepted and "
            "published to GenBank yet. You will receive another email after GenBank review.\n\n"
            f"Your DNA Subway submission ID is {record.specimen_id}.\n\n"
            "Thank you,\nThe DNA Subway Team"
        )
        self.send_email(record.email, subject, message)

    def _email_admin(self, record: GenbankRecord, message: str) -> None:
        subject = "FAILED GB Submission"
        body = f"ID: {record}\nSpecimen ID: {record.specimen_id}\nMessage:\n{message}"
        self.send_email("dnalcadmin@cshl.edu", subject, body)

    # ----------------------------------------
    # Run Everything From Here (faithful to Perl order/logic)
    def run(self, record: GenbankRecord) -> Dict[str, Any]:
        def bail_out(msg: str) -> Dict[str, Any]:
            return {"status": "error", "message": msg}

        # create_dir (no status)
        self.create_dir(record)

        # create_fasta
        st = self.create_fasta(record)
        if st["status"] != "success":
            return bail_out(f"ID: {record} ERROR: {st.get('message','')}")

        # create_smt
        st = self.create_smt(record)
        if st["status"] != "success":
            return bail_out(f"ID: {record} ERROR: {st.get('message','')}")

        # make_template
        st = self.make_template(record)
        if st["status"] != "success":
            return bail_out(f"ID: {record} ERROR: {st.get('message','')}")

        # create_feature_table
        st = self.create_feature_table(record)
        if st["status"] != "success":
            return bail_out(f"ID: {record} ERROR: {st.get('message','')}")

        # run_table2asn (Perl replaced tbl2asn with table2asn)
        st = self.run_table2asn(record)
        if st["status"] != "success":
            return bail_out(f"ID: {record} ERROR: {st.get('message','')}")

        # prep_submission_file (trace file step is intentionally omitted, matching Perl)
        st = self.prep_submission_file(record)
        if st["status"] != "success":
            return bail_out(f"ID: {record} ERROR: {st.get('message','')}")

        # submit
        st = self.submit(record)
        if st["status"] != "success":
            return bail_out(f"ID: {record} ERROR: {st.get('message','')}")

        # validate_submission
        st = self.validate_submission(record)
        if st["status"] != "success":
            return bail_out(f"ID: {record} ERROR: {st.get('message','')}")

        return {"status": st["status"], "message": st.get("message", "")}
