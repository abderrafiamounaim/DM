import os
import pathlib

import yaml
from dotenv import load_dotenv

load_dotenv()

SF_USERNAME = os.environ["SF_SOURCE_USERNAME"]
SF_PASSWORD = os.environ["SF_SOURCE_PASSWORD"]
SF_TOKEN = os.environ["SF_SOURCE_SECURITY_TOKEN"]
SF_DOMAIN = os.environ.get("SF_SOURCE_DOMAIN", "login")
DATABASE_URL = os.environ["DATABASE_URL"]

SCOPES_FILE = pathlib.Path(__file__).parent / "scopes.yaml"


def _load_yaml() -> dict:
    with open(SCOPES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_scopes() -> dict:
    """Return the full per-object config: {object_type: {fields: [...], filter: "..."}}."""
    return _load_yaml()


def get_object_config(scopes: dict, object_type: str) -> dict:
    if object_type not in scopes:
        raise ValueError(
            f"No config for '{object_type}' in scopes.yaml. "
            f"Configured objects: {list(scopes.keys())}. "
            f"Add a '{object_type}:' block to scopes.yaml before running."
        )
    cfg = scopes[object_type]
    fields = list(dict.fromkeys(cfg.get("fields", [])))
    if "Id" not in fields:
        fields.insert(0, "Id")
    return {"fields": fields, "filter": cfg.get("filter")}
