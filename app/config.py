"""Central configuration. Every value can be overridden in .env (see .env.example)."""
import os
import pathlib

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name, default=None):
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _float(name, default):
    return float(_env(name, default))


DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DATABASE_URL = _env("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'aurak.db').as_posix()}")
DATASET_ZIP = ROOT / _env("DATASET_ZIP", "AURAK_combined_enriched_2026-10-14.zip")
SECRET_KEY = _env("SECRET_KEY", "aurak-prototype-local-only-change-me")

# Demo accounts (seeded by scripts/import_data.py). Local prototype only.
ADMIN_EMAIL = _env("ADMIN_EMAIL", "admin@demo.aurak.local")
ADMIN_PASSWORD = _env("ADMIN_PASSWORD", "admin123")
FACULTY_EMAIL = _env("FACULTY_EMAIL", "faculty@demo.aurak.local")
FACULTY_PASSWORD = _env("FACULTY_PASSWORD", "faculty123")
FACULTY_DEMO_NAME = _env("FACULTY_DEMO_NAME", "Khaled Abdalgader")  # linked to the real faculty record
STUDENT_EMAIL = _env("STUDENT_EMAIL", "student@demo.aurak.local")
STUDENT_PASSWORD = _env("STUDENT_PASSWORD", "student123")
# Example approved student (Research Assistant): logs in with the AURAK student ID or email
DEMO_RA_NAME = _env("DEMO_RA_NAME", "Demo Research Assistant")
DEMO_RA_STUDENT_ID = _env("DEMO_RA_STUDENT_ID", "2023000001")
DEMO_RA_EMAIL = _env("DEMO_RA_EMAIL", "ra@demo.aurak.local")
DEMO_RA_PASSWORD = _env("DEMO_RA_PASSWORD", "ra123")
# Example student ID shown on the sign-up form
STUDENT_ID_EXAMPLE = _env("STUDENT_ID_EXAMPLE", "2023000000")

# OpenAlex
OPENALEX_API_KEY = _env("OPENALEX_API_KEY")
OPENALEX_MAILTO = _env("OPENALEX_MAILTO")
AURAK_OPENALEX_ID = "I4210153389"
OPENALEX_WORKS_PER_AUTHOR = int(_env("OPENALEX_WORKS_PER_AUTHOR", 50))

# Refresh
REFRESH_HOURS = _float("REFRESH_HOURS", 6)
SCHEDULER_ENABLED = _env("SCHEDULER_ENABLED", "1") == "1"
GRANTS_GOV_MAX = int(_env("GRANTS_GOV_MAX", 600))
EU_MAX = int(_env("EU_MAX", 1500))
HTTP_USER_AGENT = "Mozilla/5.0 (compatible; AURAK-grants-dashboard/0.1; student research prototype)"

# Matching
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
MATCH_WEIGHT_INTERESTS = _float("MATCH_WEIGHT_INTERESTS", 0.35)
MATCH_WEIGHT_PUBLICATIONS = _float("MATCH_WEIGHT_PUBLICATIONS", 0.30)
MATCH_WEIGHT_TOPICS = _float("MATCH_WEIGHT_TOPICS", 0.15)
MATCH_WEIGHT_PAST_GRANTS = _float("MATCH_WEIGHT_PAST_GRANTS", 0.10)
MATCH_WEIGHT_KEYWORDS = _float("MATCH_WEIGHT_KEYWORDS", 0.10)
MATCH_STRONG = _float("MATCH_STRONG", 0.45)      # score >= strong -> "strong match"
MATCH_MIN = _float("MATCH_MIN", 0.33)            # below this nobody is shown as a match
EVIDENCE_MIN = _float("EVIDENCE_MIN", 0.30)      # an item counts as evidence above this similarity
MATCH_TOP_FACULTY = int(_env("MATCH_TOP_FACULTY", 10))
MATCH_TOP_STUDENTS = int(_env("MATCH_TOP_STUDENTS", 5))
NOTIFY_MIN_SCORE = _float("NOTIFY_MIN_SCORE", 0.40)
NOTIFY_TOP_N = int(_env("NOTIFY_TOP_N", 5))

# Chatbot
LLM_PROVIDER = _env("LLM_PROVIDER", "ollama")
LLM_MODEL = _env("LLM_MODEL", "qwen2.5:7b")
OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434")
LLM_TIMEOUT = _float("LLM_TIMEOUT", 900)       # max seconds between streamed chunks (CPU laptops are slow)
LLM_KEEP_ALIVE = _env("LLM_KEEP_ALIVE", "60m")  # keep the model in memory between questions

# Optional cloud providers (LLM_PROVIDER). The default stays "ollama" (local Qwen). Others:
#   "claude"        Claude through the Anthropic API            -> ANTHROPIC_API_KEY
#   "claude_azure"  Claude through Microsoft Foundry (Azure)     -> ANTHROPIC_FOUNDRY_API_KEY + ANTHROPIC_FOUNDRY_RESOURCE
#   "azure_openai"  Microsoft Azure OpenAI (the models behind MS Copilot) -> AZURE_OPENAI_* below
# If the chosen cloud provider has no key or fails, the assistant falls back to the local Ollama model
# when LLM_FALLBACK_TO_OLLAMA=1 (default).
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
ANTHROPIC_FOUNDRY_API_KEY = _env("ANTHROPIC_FOUNDRY_API_KEY")
ANTHROPIC_FOUNDRY_RESOURCE = _env("ANTHROPIC_FOUNDRY_RESOURCE")
CLAUDE_MODEL = _env("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = _env("CLAUDE_EFFORT")  # optional: low / medium / high (model default when empty)
AZURE_OPENAI_ENDPOINT = _env("AZURE_OPENAI_ENDPOINT")      # e.g. https://<resource>.openai.azure.com
AZURE_OPENAI_API_KEY = _env("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = _env("AZURE_OPENAI_DEPLOYMENT")  # the deployment name created in Azure
AZURE_OPENAI_API_VERSION = _env("AZURE_OPENAI_API_VERSION", "2024-10-21")
LLM_FALLBACK_TO_OLLAMA = _env("LLM_FALLBACK_TO_OLLAMA", "1") == "1"
