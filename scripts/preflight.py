"""Check a Fly app holds every variable `config.REQUIRED_IN_PROD` names.

    uv run python scripts/preflight.py --app metafora

Compares the list against the app's secrets and the local fly.toml's `[env]`.
Names only — Fly secrets are write-only — so this catches a variable that was
never set, not one set to a bad value. `config._verify()` still does that at
boot; this just gets the common failure named before the image is built.

Skipped when the fly.toml is not a verified environment.

Exits 0 pass, 1 missing, 2 could not check.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.core.config import REQUIRED_IN_PROD  # noqa: E402

#: Exit code for "could not check", kept apart from "checked, and it is broken".
UNDETERMINED = 2

RED = "\033[31m"
GREEN = "\033[32m"
DIM = "\033[2m"
OFF = "\033[0m"


def _die(message: str) -> None:
    """Exit 2: could not check, as distinct from checked and found broken."""
    print(message, file=sys.stderr)
    raise SystemExit(UNDETERMINED)


def fly_config(path: Path) -> dict:
    """The local fly.toml — the one about to be deployed, `[env]` included."""
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _die(f"{RED}preflight{OFF} cannot read {path}: {exc}")


def secret_names(app: str) -> set[str]:
    """The secret names Fly holds for this app."""
    try:
        out = subprocess.run(
            ["flyctl", "secrets", "list", "--app", app, "--json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    except FileNotFoundError:
        _die(f"{RED}preflight{OFF} flyctl is not installed")
    except subprocess.TimeoutExpired:
        _die(f"{RED}preflight{OFF} flyctl did not answer within 60s")
    except subprocess.CalledProcessError as exc:
        _die(
            f"{RED}preflight{OFF} flyctl could not list secrets for {app!r}:\n{exc.stderr.strip()}"
        )

    # `Name` in older flyctl, `name` in current; accept either.
    try:
        entries = json.loads(out)
        names = {
            value for entry in entries for key, value in entry.items() if key.lower() == "name"
        }
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        _die(f"{RED}preflight{OFF} could not parse flyctl's secret list: {exc}")

    if entries and not names:
        _die(
            f"{RED}preflight{OFF} flyctl listed {len(entries)} secrets and none had a "
            "name field — its JSON shape has changed and this script cannot read it"
        )
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, help="the Fly app to check")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "fly.toml",
        help="the fly.toml being deployed (default: the repo's)",
    )
    args = parser.parse_args()

    config = fly_config(args.config)
    env = config.get("env", {})

    # `_verify()` returns early in dev, so there is nothing to require.
    if env.get("METAFORA_ENV", "dev") == "dev":
        print(
            f"preflight · {args.app} {DIM}skipped — METAFORA_ENV is not a verified environment{OFF}"
        )
        return 0

    held = secret_names(args.app) | set(env)
    missing = [group for group in REQUIRED_IN_PROD if not held & set(group)]

    print(f"preflight · {args.app}")
    for group in REQUIRED_IN_PROD:
        names = " or ".join(group)
        if held & set(group):
            print(f"  {GREEN}ok{OFF}    {names}")
        else:
            print(f"  {RED}FAIL{OFF}  {names} {DIM}— set on neither the app nor its fly.toml{OFF}")

    if not missing:
        print(f"{GREEN}all set{OFF}")
        return 0

    flat = " ".join(group[0] for group in missing)
    setters = " ".join(f"{group[0]}=..." for group in missing)
    sys.stdout.flush()
    print(
        f"\n{RED}not deployable{OFF}: {len(missing)} missing — the machine would "
        f"raise ConfigError at import and crash-loop.\n"
        f"  flyctl secrets set --app {args.app} {setters}",
        file=sys.stderr,
    )
    print(f"::error::preflight: {args.app} is missing {flat}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
