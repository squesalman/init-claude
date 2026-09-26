"""
Regression tests for config/settings.py's import-time SECRET_KEY fail-closed logic
(round-5 code review). This has to run in a subprocess: the fail-closed check happens
once, at module import time, when Django first loads settings — by the time a normal
pytest test runs, settings are already imported and cached, so no in-process test can
exercise "what happens when settings.py is imported with these env vars" more than once
per test session. A throwaway script proved this once and was deleted; this is what
keeps it proved.
"""

import os
import subprocess
import sys
from pathlib import Path

MANAGE_PY = Path(__file__).resolve().parent.parent / "manage.py"


def _run_check(**env_overrides: str) -> subprocess.CompletedProcess:
    # Start from a real copy of the parent env (DB connection vars etc. still needed),
    # then explicitly delete/set the keys under test — never just merge on top, or an
    # inherited DJANGO_SECRET_KEY from the calling shell could mask the exact bug this
    # is checking for.
    env = dict(os.environ)
    env.pop("DJANGO_SECRET_KEY", None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(MANAGE_PY), "check"],
        cwd=str(MANAGE_PY.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_secret_key_required_when_debug_false_and_unset():
    """
    DEBUG and ALLOWED_HOSTS already failed closed when their env vars were unset;
    SECRET_KEY fell back to a hardcoded, publicly-committed dev value unconditionally.
    If prod's .env is missing DJANGO_SECRET_KEY, the app must refuse to start rather
    than silently boot with a key anyone reading the public repo can use to forge
    sessions/CSRF tokens/password-reset tokens.
    """
    result = _run_check(DJANGO_DEBUG="false")

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_secret_key_falls_back_to_dev_default_when_debug_true():
    """The dev fallback must still work when DEBUG=True (local runserver/tests with
    zero setup) — only DEBUG=False is fail-closed."""
    result = _run_check(DJANGO_DEBUG="true")

    assert result.returncode == 0, result.stderr


def test_secret_key_from_env_used_when_debug_false():
    """A real deployment with DJANGO_SECRET_KEY actually set must start normally."""
    result = _run_check(
        DJANGO_DEBUG="false",
        DJANGO_SECRET_KEY="a-real-looking-production-secret-key-value",
    )

    assert result.returncode == 0, result.stderr


def test_secret_key_rejects_the_env_example_placeholder_when_debug_false():
    """
    Round-7 addendum: emptiness isn't the only bad value. .env.example ships
    DJANGO_SECRET_KEY=CHANGE-ME-run-get_random_secret_key, which looks like a real value
    someone might miss updating when copying .env.example to .env. If ops sets
    DEBUG=false and never touches that line, the app must not silently boot in
    "production mode" using a key that's public in this repo's git history.
    """
    result = _run_check(
        DJANGO_DEBUG="false",
        DJANGO_SECRET_KEY="CHANGE-ME-run-get_random_secret_key",
    )

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_secret_key_placeholder_falls_back_to_dev_default_when_debug_true():
    """The placeholder rejection is DEBUG=False-only, same as the empty-value check —
    dev (DEBUG=true) still works even if .env.example's line is used unmodified."""
    result = _run_check(
        DJANGO_DEBUG="true",
        DJANGO_SECRET_KEY="CHANGE-ME-run-get_random_secret_key",
    )

    assert result.returncode == 0, result.stderr


def test_secret_key_rejects_django_insecure_prefix_when_debug_false():
    """
    Round-8 code review: broadened beyond the one exact placeholder. Any value starting
    with "django-insecure-" is Django's own `startproject` default-key prefix — the
    realistic leftover-default scenario (more likely in practice than someone leaving
    the literal CHANGE-ME placeholder from .env.example in place) — and must be
    rejected the same way when DEBUG=False.
    """
    result = _run_check(
        DJANGO_DEBUG="false",
        DJANGO_SECRET_KEY="django-insecure-some-leftover-startproject-default-key",
    )

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_secret_key_django_insecure_prefix_falls_back_to_dev_default_when_debug_true():
    """Same DEBUG=False-only carve-out as the other two rejection cases."""
    result = _run_check(
        DJANGO_DEBUG="true",
        DJANGO_SECRET_KEY="django-insecure-some-leftover-startproject-default-key",
    )

    assert result.returncode == 0, result.stderr


def test_secret_key_real_value_not_starting_with_django_insecure_still_boots():
    """Sanity check alongside the two rejection tests above: a real production key
    (not matching either bad pattern) must still work — this isn't rejecting on
    length or some other property, only the two known-bad shapes."""
    result = _run_check(
        DJANGO_DEBUG="false",
        DJANGO_SECRET_KEY="a-completely-different-real-production-secret-key",
    )

    assert result.returncode == 0, result.stderr
