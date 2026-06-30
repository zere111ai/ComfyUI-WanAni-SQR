import hashlib
import json
import os
import tempfile
from pathlib import Path


PROGRESS_DIR = Path(__file__).resolve().parent / "batch_progress"


def make_job_id(directory, variant=""):
    normalized = os.path.normcase(os.path.abspath(directory))
    identity = f"{normalized}\n{variant}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def read_progress(job_id):
    path = PROGRESS_DIR / f"{job_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_progress(job_id, data):
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    target = PROGRESS_DIR / f"{job_id}.json"
    fd, temp_name = tempfile.mkstemp(prefix=f"{job_id}_", suffix=".tmp", dir=PROGRESS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
