# dnasubway2-django — Back-End Service Documentation

The back-end service for **DNA Subway** is built on Django and Celery. It handles biological sequence analysis workflows, user and project data management, interaction with CyVerse/Tapis High-Performance Computing (HPC) infrastructure, third-party bioinformatics API integrations, and asynchronous job execution.

---

## 1. System Architecture & Tech Stack

* **Web Framework:** Django (Python 3.9+)
* **Asynchronous Task Queue:** Celery
* **In-Memory Data Store / Broker:** Redis
* **HPC Execution & Cloud Services:** Tapis SDK (`tapipy`) & CyVerse Infrastructure
* **Bioinformatics Integration:** Biopython, BLAST+, MUSCLE, ClustalW / ClustalO, PHYLIP package suite

---

## 2. System Prerequisites & External Dependencies

The back-end requires the following system services and external command-line utilities:

* **Python:** `3.9` or higher
* **Redis Server:** `6.x` or higher
* **Database Engine:** PostgreSQL (production recommended) or SQLite (development)
* **Command-Line Bioinformatics Tools** (must be installed on system PATH or configured in `settings.py`):
  * **BLAST+:** `blastn`
  * **Aligners:** `muscle`, `clustalw`, `clustalo`
  * **PHYLIP Suite:** `seqboot`, `dnadist`, `neighbor`, `consense`, `dnaml`

---

## 3. Installation & Setup Instructions

### Environment Initialization
```bash
# Clone the repository
git clone <repository-url>
cd dnasubway-backend

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

### Database Migration & Cron Task Setup
```bash
# Apply database migrations
python3 manage.py makemigrations
python3 manage.py migrate

# Register scheduled crontab jobs
python3 manage.py crontab add
```

---

## 4. Running Application Services

Running the full application back-end requires three primary concurrent processes:

### 1. In-Memory Data Store (Redis)
Start Redis server to act as the Celery task broker and results backend:
```bash
redis-server
```

### 2. Celery Asynchronous Worker
Execute Celery worker process for handling async analysis pipelines:
```bash
python3 -m celery -A dnasubway2 worker --loglevel=info --concurrency=24
```

### 3. Django Server
Start the Django development / application server:
```bash
python3 manage.py runserver 0.0.0.0:8007
```

> **Note on Production Deployment:** In a production setting, run Django via WSGI/ASGI application servers (e.g., Gunicorn or uWSGI) managed by process managers like systemd or Supervisor behind a reverse proxy (e.g., Apache or Nginx).

---

## 5. Configuration Settings Reference

All settings are managed within `dnasubway2/settings.py` (or supplied via environment variables). Below is the complete configuration reference required for the Django back-end:

### Core Framework & Application Settings
| Setting Name | Type | Description |
| :--- | :--- | :--- |
| `BASE_DIR` | `string` | Absolute path to project root directory |
| `SECRET_KEY` | `string` | Secret key for cryptographic signing |
| `DEBUG` | `boolean` | Enables/disables debug mode |
| `ALLOWED_HOSTS` | `list[string]` | Allowed host headers for request security |
| `INSTALLED_APPS` | `list[string]` | Active Django applications |
| `MIDDLEWARE` | `list[string]` | Active middleware components |
| `ROOT_URLCONF` | `string` | Primary URL routing module |
| `TEMPLATES` | `list` | Template engine configuration |
| `WSGI_APPLICATION` | `string` | Path to WSGI application callable |
| `DATABASES` | `dict` | Database connection definitions |
| `DEFAULT_AUTO_FIELD` | `string` | Default primary key field type |
| `LANGUAGE_CODE` | `string` | Default language code (e.g., `'en-us'`) |
| `TIME_ZONE` | `string` | Timezone setting (e.g., `'UTC'`) |
| `USE_I18N` | `boolean` | Internationalization toggle |
| `USE_TZ` | `boolean` | Timezone awareness toggle |
| `AUTH_PASSWORD_VALIDATORS` | `list[dict]` | Password validation rules |
| `AUTHENTICATION_BACKENDS` | `list[string]` | Authentication backend modules |

### Network, SSL, CORS & Security
| Setting Name | Type | Description |
| :--- | :--- | :--- |
| `REACT_URL` | `string` | Base URL of connected React front-end application |
| `PROTOCOL` | `string` | Operating protocol (`http` or `https`) |
| `APPEND_SLASH` | `boolean` | Automatic trailing slash appender |
| `CORS_ALLOW_CREDENTIALS` | `boolean` | Enable credentials across CORS requests |
| `CORS_ALLOWED_ORIGINS` | `list[string]` | Allowed origins for cross-origin requests |
| `CSRF_TRUSTED_ORIGINS` | `list[string]` | Origins trusted for CSRF state-changing requests |
| `CSRF_FAILURE_VIEW` | `string` | Custom view for CSRF failure handling |
| `SECURE_SSL_REDIRECT` | `boolean` | Redirect HTTP requests to HTTPS |
| `SESSION_COOKIE_SECURE` | `boolean` | Restrict session cookies to HTTPS |
| `CSRF_COOKIE_SECURE` | `boolean` | Restrict CSRF cookies to HTTPS |
| `SESSION_COOKIE_SAMESITE` | `enum` | SameSite attribute for session cookies |
| `CSRF_COOKIE_SAMESITE` | `enum` | SameSite attribute for CSRF cookies |

### Request Limits & File Handling
| Setting Name | Type | Description |
| :--- | :--- | :--- |
| `DATA_UPLOAD_MAX_NUMBER_FILES` | `number` | Maximum uploaded files per request |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | `number` | Maximum upload size in memory (bytes) |
| `STATICFILES_DIRS` | `list[string]` | Directories for static file discovery |
| `STATIC_URL` | `string` | Public URL prefix for static assets |
| `MEDIA_ROOT` | `string` | Local filesystem path for stored media files |

### Celery & Async Task Configuration
| Setting Name | Type | Description |
| :--- | :--- | :--- |
| `CELERY_BROKER_URL` | `string` | URL of message broker (e.g., `redis://127.0.0.1:6379/0`) |
| `CELERY_RESULT_BACKEND` | `string` | URL for task result storage |
| `CELERY_ACCEPT_CONTENT` | `list[string]` | Permitted serialization formats (e.g., `['json']`) |
| `CELERY_TASK_SERIALIZER` | `string` | Default task serialization method |
| `CRONJOBS` | `list[tuple]` | Scheduled cron job definitions |

### HPC Integration & Tapis Services
| Setting Name | Type | Description |
| :--- | :--- | :--- |
| `TAPIS_URL` / `TAPIS_BASE_URL` | `string` | Base API endpoint for Tapis platform |
| `TAPIS_USERNAME` / `TAPIS_PASSWORD` | `string` | Primary Tapis service authentication |
| `TAPIS_SERVICE_USERNAME` / `TAPIS_SERVICE_PASSWORD` | `string` | Internal service credentials for Tapis |
| `TAPIS_CYVERSE_USERNAME` / `TAPIS_CYVERSE_PASSWORD` | `string` | CyVerse infrastructure account credentials |
| `INSTANCE_NAME` | `string` | Identifier for the back-end deployment instance |
| `MAX_WAIT_TIME` | `number` | Timeout limit (seconds) for async API calls |
| `OPENRC_PATH` | `string` | OpenStack/OpenRC configuration file path |
| `SHELVE_INSTANCE` | `boolean` | Enable/disable cloud instance shelving |

### QIIME2 Tapis Application Definitions
| Setting Name | Type | Description |
| :--- | :--- | :--- |
| `QIIME2_DEMUX_APP_*` | `string` | App ID, Tapis ID, and version for Demultiplexing |
| `QIIME2_DADA2_APP_*` | `string` | App ID, Tapis ID, and version for DADA2 denoising |
| `QIIME2_RAREFACTION_APP_*` | `string` | App ID, Tapis ID, and version for Rarefaction |
| `QIIME2_COREMETRICS_APP_*` | `string` | App ID, Tapis ID, and version for Core Metrics |
| `QIIME2_ANCOM_APP_*` | `string` | App ID, Tapis ID, and version for ANCOM analysis |
| `QIIME2_PRONAME_IMPORT_APP_*` | `string` | App ID, Tapis ID, and version for Proname Import |
| `QIIME2_PRONAME_FILTER_APP_*` | `string` | App ID, Tapis ID, and version for Proname Filtering |
| `QIIME2_PRONAME_REFINE_APP_*` | `string` | App ID, Tapis ID, and version for Proname Refinement |
| `QIIME2_PRONAME_TAXONOMY_APP_*` | `string` | App ID, Tapis ID, and version for Proname Taxonomy |
| `QIIME2_GNEISS_APP_*` | `string` | App ID, Tapis ID, and version for Gneiss analysis |

### Local Bioinformatics Tools & Databases
| Setting Name | Type | Description |
| :--- | :--- | :--- |
| `BLASTDB` | `string` | Path to local BLAST reference database |
| `SPECIES_MAP` | `dict` | Mapping dictionary for reference species datasets |
| `BLASTN_PROGRAM` | `string` | Path to `blastn` executable |
| `MUSCLE_PROGRAM` | `string` | Path to `muscle` executable |
| `CLUSTALW_PROGRAM` | `string` | Path to `clustalw` executable |
| `CLUSTALO_PROGRAM` | `string` | Path to `clustalo` executable |
| `SEQBOOT_PROGRAM` | `string` | Path to PHYLIP `seqboot` executable |
| `DNADIST_PROGRAM` | `string` | Path to PHYLIP `dnadist` executable |
| `NEIGHBOR_PROGRAM` | `string` | Path to PHYLIP `neighbor` executable |
| `CONSENSE_PROGRAM` | `string` | Path to PHYLIP `consense` executable |
| `DNAML_PROGRAM` | `string` | Path to PHYLIP `dnaml` executable |
| `BARCODING_PROJECTS` | `dict` | Configuration dictionary for DNA barcoding projects |
| `PRIMERS` | `dict` | Supported primer set definitions |
| `CLASSIFIER` | `dict` | Sequence classifier database paths/mappings |
| `CHIMERA_DBS` | `dict` | Databases for chimera detection algorithms |
| `MEDAKA_REFERENCES` | `dict` | Reference genomes for Medaka consensus sequencing |

### External Third-Party APIs & Submission Portals
| Setting Name | Type | Description |
| :--- | :--- | :--- |
| `MAILGUN_DOMAIN` / `MAILGUN_API_KEY` | `string` | Mailgun API credentials for notification delivery |
| `MAILGUN_FROM_EMAIL` | `string` | Sender email address for system emails |
| `BOLD_API_KEY` | `string` | API key for Barcode of Life Data System |
| `GOOGLE_SEARCH_API_KEY` / `GOOGLE_SEARCH_CX` | `string` | Google Search API key and Custom Search Engine ID |
| `GB_SUBMISSION_DIR` | `string` | Directory for GenBank submission preparation |
| `GB_TBL2ASN_TEMPLATE_PATH` | `string` | Template path for NCBI `tbl2asn` |
| `GB_FTP_USER` / `GB_FTP_PW` | `string` | NCBI GenBank FTP account credentials |
| `GB_FTP_PASSIVE` | `boolean` | Passive mode toggle for FTP connections |
| `GB_VALIDATION_USER` | `string` | Username for GenBank validation services |

---

## 6. Production Process Management & Service Isolation

In a standardized production host, background components (Redis, Celery worker, and Django WSGI) should be wrapped in system service managers (such as systemd or Supervisor) to guarantee automatic recovery upon system restart.

Example systemd unit targets:
* `redis-server.service` — In-memory datastore daemon
* `celery-worker.service` — Celery background task processing daemon
* `django-backend.service` — WSGI web server handling backend requests
