"""Shared pytest fixtures for the Omni-Dev test suite.

The whole suite runs offline against an injected ``FakeBackend`` (Requirement 8.8).
These fixtures give every test a clean, isolated filesystem and a guaranteed-clean
model-call injection point:

* :func:`temp_home` redirects the user home directory (``USERPROFILE``/``HOME``) to
  a per-test ``tmp_path`` so :mod:`src.config_store` reads and writes under a
  throwaway directory instead of the real user profile.
* :func:`temp_project` ``chdir``s into a fresh project directory under ``tmp_path``
  and restores the original cwd on teardown, so project-config keyed by absolute
  path is likewise isolated.
* :func:`clear_completion_fn` clears any injected completion function on teardown so
  one test's :class:`~tests.fakes.FakeBackend` can never leak into another.

None of these fixtures touch the network or any real model provider.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture
def temp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect the user home dir to a temp dir for config/transcript isolation.

    :mod:`src.config_store` resolves its global file from ``USERPROFILE`` (Windows)
    or the expanded ``~`` (which honors ``HOME`` on POSIX) at call time. By pointing
    both environment variables at ``tmp_path`` we guarantee that ``get_global_config``
    / ``save_global_config`` (and anything keyed off the global dir, e.g. transcripts)
    operate entirely inside the throwaway directory.

    Yields the temp home path so tests can assert on the on-disk layout.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    yield home


@pytest.fixture
def temp_project(tmp_path: Path) -> Iterator[Path]:
    """Provide a fresh project directory and ``chdir`` into it for the test.

    Project_Config is keyed by the project's absolute path (the cwd by default),
    so each test gets its own project directory. The original working directory is
    always restored on teardown, even if the test raises.

    Yields the project directory path.
    """
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    original_cwd = os.getcwd()
    os.chdir(project)
    try:
        yield project
    finally:
        os.chdir(original_cwd)


@pytest.fixture(autouse=True)
def clear_completion_fn() -> Iterator[None]:
    """Ensure no injected completion function leaks across tests.

    Autouse so that even tests which forget to clean up cannot poison a later test.
    The injection point lives in :mod:`src.model_router`; we reset it to ``None``
    (the "use the real ``litellm.completion``" default) on teardown.
    """
    try:
        try:
            from src import model_router
        except ImportError:  # pragma: no cover - import-path fallback
            import model_router  # type: ignore
    except Exception:  # pragma: no cover - module not importable yet
        model_router = None  # type: ignore

    yield

    if model_router is not None:
        try:
            model_router.set_completion_fn(None)
        except Exception:  # pragma: no cover - defensive teardown
            pass
