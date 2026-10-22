"""Show or turn off embedded chat security on the active Orchestrate instance.

IBM's wxO-embed-chat-security-tool.sh logs in through iam.platform.saas.ibm.com,
which rejected our IBM Cloud API key. This script reuses the token the ADK
already holds for the active environment (`orchestrate env activate ...`) and
calls the same endpoint the IBM script uses:
    GET/POST <instance URL>/v1/embed/secure/config

Usage (Windows):
    venv\\Scripts\\orchestrate.exe env activate nyp-dev-env
    venv\\Scripts\\python.exe scripts\\embed_security.py          # show
    venv\\Scripts\\python.exe scripts\\embed_security.py --off    # disable

Turning security off lets anyone with the page link chat with the agent
(the organisers allowed this on 22 Oct 2026). It prints no token or key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests
from ibm_watsonx_orchestrate_clients.common.utils import check_token_validity
from ibm_watsonx_orchestrate_core.utils.common import yaml_safe_load
from ibm_watsonx_orchestrate_core.utils.config import (
    AUTH_CONFIG_FILE,
    AUTH_CONFIG_FILE_FOLDER,
    AUTH_MCSP_TOKEN_OPT,
    AUTH_SECTION_HEADER,
    CONTEXT_ACTIVE_ENV_OPT,
    CONTEXT_SECTION_HEADER,
    DEFAULT_CONFIG_FILE,
    DEFAULT_CONFIG_FILE_FOLDER,
    ENV_WXO_URL_OPT,
    ENVIRONMENTS_SECTION_HEADER,
)


def _active_env() -> tuple[str, str, str]:
    with open(os.path.join(DEFAULT_CONFIG_FILE_FOLDER, DEFAULT_CONFIG_FILE)) as f:
        config = yaml_safe_load(f)
    env = config.get(CONTEXT_SECTION_HEADER, {}).get(CONTEXT_ACTIVE_ENV_OPT)
    if not env:
        sys.exit("No active environment. Run: orchestrate env activate nyp-dev-env")
    url = config.get(ENVIRONMENTS_SECTION_HEADER, {}).get(env, {}).get(ENV_WXO_URL_OPT)
    with open(os.path.join(AUTH_CONFIG_FILE_FOLDER, AUTH_CONFIG_FILE)) as f:
        auth = yaml_safe_load(f)
    token = auth.get(AUTH_SECTION_HEADER, {}).get(env, {}).get(AUTH_MCSP_TOKEN_OPT)
    if not url or not token or not check_token_validity(token):
        sys.exit(f"Token for '{env}' is missing or expired. Run: orchestrate env activate {env}")
    return env, url.rstrip("/"), token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--off", action="store_true", help="turn embedded chat security off")
    args = parser.parse_args()

    env, url, token = _active_env()
    endpoint = f"{url}/v1/embed/secure/config"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    print(f"Environment: {env}\nInstance:    {url}")

    if args.off:
        resp = requests.post(endpoint, headers=headers, json={"is_security_enabled": False}, timeout=30)
        print(f"POST {resp.status_code}: {resp.text[:500]}")
        if not resp.ok:
            sys.exit(1)

    resp = requests.get(endpoint, headers=headers, timeout=30)
    print(f"GET {resp.status_code}:")
    try:
        print(json.dumps(resp.json(), indent=2)[:2000])
    except ValueError:
        print(resp.text[:2000])


if __name__ == "__main__":
    main()
