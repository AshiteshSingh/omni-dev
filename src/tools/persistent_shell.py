"""
PersistentShell - session-scoped stateful shell backing the run_command tool.

Python port of scratch_repo/src/utils/PersistentShell.ts, adapted for a
Windows-first (PowerShell) environment with POSIX (bash) support.

Why this exists
---------------
The original BashTool spawned a fresh ``subprocess`` for every command, so a
``cd`` or an environment-variable assignment in one command was lost by the
next. A real terminal keeps that state. PersistentShell keeps a single
long-lived shell process alive for the whole session, so working-directory and
environment changes carry across successive ``run_command`` invocations
(Requirements 11.1, 11.2, 11.3).

How it works
------------
* On Windows we spawn a long-lived ``powershell.exe`` (falling back to
  ``cmd.exe``); on POSIX we spawn ``bash``. The same process executes every
  command, so cwd/env persist for free.
* Each command is written to a temporary script file and *dot-sourced* into the
  live shell (``. script`` / ``source script`` / ``call script``) so that any
  ``cd`` or env mutation happens in the session scope, not a child scope.
* The command is bracketed by a unique random *sentinel* marker echoed before
  and after it. A background reader thread accumulates the shell's stdout; the
  caller polls for the trailing sentinel to know the command finished, and the
  sentinel line also carries the captured exit code (``$LASTEXITCODE`` on
  PowerShell / ``$?`` on bash) and the current working directory
  (Requirement 11.4). stderr is redirected to a temp file and read back.
* A bounded per-command timeout terminates a stuck command and keeps the shell
  usable: on Windows killing a single child of a persistent shell is
  unreliable, so we kill and respawn the shell process, restoring the last
  known cwd so the session continues (Requirement 11.5).
* ``interrupt()`` terminates the current command and keeps the shell alive;
  ``kill()`` fully terminates it (Requirement 11.6).
"""
from __future__ import annotations

import os
import time
import uuid
import threading
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ShellResult:
    """Result of a single command executed through the PersistentShell."""

    stdout: str
    stderr: str
    exit_code: int
    cwd: str
    timed_out: bool = False


# Exit codes used for abnormal termination (mirrors common shell conventions).
_TIMEOUT_EXIT_CODE = 124
_INTERRUPT_EXIT_CODE = 130


class PersistentShell:
    """A single long-lived shell process reused across commands in a session."""

    def __init__(self, cwd: Optional[str] = None):
        self._lock = threading.Lock()          # serialize run() calls
        self._buffer_lock = threading.Lock()   # guard the stdout line buffer
        self._buffer: List[str] = []
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_drain: Optional[threading.Thread] = None
        self._interrupt_event = threading.Event()
        self._cwd = os.path.abspath(cwd or os.getcwd())

        self._is_windows = os.name == "nt"
        self._kind: str = "bash"   # one of: "powershell", "cmd", "bash"
        self._exe: List[str] = []

        self._spawn()

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------
    def _select_shell(self) -> None:
        """Pick the shell executable and invocation for this platform."""
        if self._is_windows:
            powershell = self._which("powershell.exe") or self._which("pwsh.exe")
            if powershell:
                self._kind = "powershell"
                self._exe = [
                    powershell,
                    "-NoProfile",
                    "-NoLogo",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    "-",
                ]
                return
            cmd = self._which("cmd.exe") or "cmd.exe"
            self._kind = "cmd"
            self._exe = [cmd, "/Q"]
            return
        bash = self._which("bash") or "/bin/bash"
        self._kind = "bash"
        self._exe = [bash]

    @staticmethod
    def _which(name: str) -> Optional[str]:
        from shutil import which
        return which(name)

    def _spawn(self) -> None:
        """Start the long-lived shell process and its reader thread."""
        self._select_shell()

        creationflags = 0
        if self._is_windows:
            # Avoid popping a console window for the background shell.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self._proc = subprocess.Popen(
            self._exe,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )

        with self._buffer_lock:
            self._buffer = []

        self._reader_thread = threading.Thread(
            target=self._read_stdout, args=(self._proc.stdout,), daemon=True
        )
        self._reader_thread.start()
        # Drain the process-level stderr so a full pipe never blocks the shell.
        self._stderr_drain = threading.Thread(
            target=self._drain, args=(self._proc.stderr,), daemon=True
        )
        self._stderr_drain.start()

        self._init_shell()

    def _init_shell(self) -> None:
        """Silence prompts and pin UTF-8 output so marker parsing stays clean."""
        if self._kind == "powershell":
            self._send(
                "function prompt { '' }; "
                "$ProgressPreference='SilentlyContinue'; "
                "$ErrorActionPreference='Continue'; "
                "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                "$OutputEncoding=[System.Text.Encoding]::UTF8"
            )
        elif self._kind == "bash":
            self._send("export PS1=''; export PS2=''")
        elif self._kind == "cmd":
            self._send("prompt $S")

    def _read_stdout(self, stream) -> None:
        try:
            for line in iter(stream.readline, ""):
                with self._buffer_lock:
                    self._buffer.append(line.rstrip("\r\n"))
        except Exception:
            pass

    @staticmethod
    def _drain(stream) -> None:
        try:
            for _ in iter(stream.readline, ""):
                pass
        except Exception:
            pass

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _send(self, line: str) -> None:
        if not self._proc or not self._proc.stdin:
            return
        try:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------
    def run(self, command: str, timeout: float = 120) -> ShellResult:
        """Run a command in the persistent shell and return its result.

        cwd/env changes persist to subsequent calls. A command that exceeds
        ``timeout`` is terminated and ``timed_out=True`` is returned while the
        shell stays usable for the next command.
        """
        with self._lock:
            self._interrupt_event.clear()
            if not self._is_alive():
                self._spawn()

            uid = uuid.uuid4().hex
            begin = f"__OMNI_BEGIN_{uid}__"
            end = f"__OMNI_END_{uid}__"

            script_path, runner_path, err_path = self._write_scripts(command, uid)
            wrapper = self._build_wrapper(begin, end, script_path, runner_path, err_path)

            # Start from a clean buffer so we never match an old marker.
            with self._buffer_lock:
                self._buffer = []

            self._send(wrapper)

            result = self._await_result(begin, end, err_path, timeout)
            self._cleanup_files(script_path, runner_path, err_path)
            return result

    def _await_result(
        self, begin: str, end: str, err_path: str, timeout: float
    ) -> ShellResult:
        deadline = time.monotonic() + max(0.1, timeout)
        poll = 0.01

        while True:
            if self._interrupt_event.is_set():
                partial = self._collect_stdout(begin, end)
                self._respawn_preserving_cwd()
                return ShellResult(
                    stdout=partial,
                    stderr=self._read_errfile(err_path) + "\nCommand interrupted.",
                    exit_code=_INTERRUPT_EXIT_CODE,
                    cwd=self._cwd,
                    timed_out=False,
                )

            with self._buffer_lock:
                lines = list(self._buffer)

            end_idx = self._find_marker(lines, end)
            if end_idx is not None:
                stdout = self._extract_stdout(lines, begin, end_idx)
                code, cwd = self._parse_end_line(lines[end_idx], end)
                if cwd:
                    self._cwd = cwd
                return ShellResult(
                    stdout=stdout,
                    stderr=self._read_errfile(err_path),
                    exit_code=code,
                    cwd=self._cwd,
                    timed_out=False,
                )

            if not self._is_alive():
                # Shell died mid-command; surface what we have and respawn.
                partial = self._collect_stdout(begin, end)
                self._respawn_preserving_cwd()
                return ShellResult(
                    stdout=partial,
                    stderr=self._read_errfile(err_path) + "\nShell process exited unexpectedly.",
                    exit_code=1,
                    cwd=self._cwd,
                    timed_out=False,
                )

            if time.monotonic() > deadline:
                partial = self._collect_stdout(begin, end)
                self._respawn_preserving_cwd()
                return ShellResult(
                    stdout=partial,
                    stderr=(self._read_errfile(err_path) + f"\nCommand timed out after {timeout}s.").strip(),
                    exit_code=_TIMEOUT_EXIT_CODE,
                    cwd=self._cwd,
                    timed_out=True,
                )

            time.sleep(poll)

    # ------------------------------------------------------------------
    # Wrapper / script construction
    # ------------------------------------------------------------------
    def _write_scripts(self, command: str, uid: str):
        """Write the user command to a temp script; return (script, runner, err)."""
        tmp = tempfile.gettempdir()
        err_path = os.path.join(tmp, f"omni-shell-{uid}.err")
        # Pre-create the err file so reading it never raises before the shell writes.
        try:
            open(err_path, "w", encoding="utf-8").close()
        except OSError:
            pass

        if self._kind == "powershell":
            script_path = os.path.join(tmp, f"omni-shell-{uid}.ps1")
            self._write_file(script_path, command)
            return script_path, None, err_path

        if self._kind == "bash":
            script_path = os.path.join(tmp, f"omni-shell-{uid}.sh")
            self._write_file(script_path, command)
            return script_path, None, err_path

        # cmd.exe: a runner batch brackets the user script so %ERRORLEVEL%/%CD%
        # expand at runtime (line-by-line), not when the wrapper line is sent.
        script_path = os.path.join(tmp, f"omni-shell-{uid}.cmd")
        runner_path = os.path.join(tmp, f"omni-runner-{uid}.cmd")
        self._write_file(script_path, command)
        return script_path, runner_path, err_path

    @staticmethod
    def _write_file(path: str, content: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def _build_wrapper(
        self,
        begin: str,
        end: str,
        script_path: str,
        runner_path: Optional[str],
        err_path: str,
    ) -> str:
        if self._kind == "powershell":
            return (
                f"Write-Output '{begin}'; "
                f". '{script_path}' 2> '{err_path}'; "
                f"$__ok=$?; $__c=$LASTEXITCODE; "
                f"if($null -eq $__c){{$__c=[int](-not $__ok)}}; "
                f"Write-Output ('{end} ' + $__c + ' ' + $PWD.Path)"
            )

        if self._kind == "bash":
            return (
                f"echo '{begin}'; "
                f". '{script_path}' 2> '{err_path}'; "
                f"__c=$?; "
                f'echo "{end} $__c $(pwd)"'
            )

        # cmd.exe — build and invoke the runner batch.
        runner_body = (
            "@echo off\r\n"
            f"echo {begin}\r\n"
            f'call "{script_path}" 2>"{err_path}"\r\n'
            f"echo {end} %ERRORLEVEL% %CD%\r\n"
        )
        if runner_path:
            self._write_file(runner_path, runner_body)
            return f'call "{runner_path}"'
        return ""

    # ------------------------------------------------------------------
    # Output parsing helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _find_marker(lines: List[str], marker: str) -> Optional[int]:
        for i, line in enumerate(lines):
            if marker in line:
                return i
        return None

    def _extract_stdout(self, lines: List[str], begin: str, end_idx: int) -> str:
        begin_idx = self._find_marker(lines, begin)
        if begin_idx is None:
            start = 0
        else:
            start = begin_idx + 1
        body = lines[start:end_idx]
        return "\n".join(body).strip("\n")

    def _collect_stdout(self, begin: str, end: str) -> str:
        with self._buffer_lock:
            lines = list(self._buffer)
        begin_idx = self._find_marker(lines, begin)
        if begin_idx is None:
            return ""
        return "\n".join(lines[begin_idx + 1:]).strip("\n")

    @staticmethod
    def _parse_end_line(line: str, end: str):
        """Parse '<end> <code> <cwd...>' tolerating spaces in the cwd path."""
        idx = line.find(end)
        rest = line[idx + len(end):].strip()
        parts = rest.split(" ", 1)
        try:
            code = int(parts[0]) if parts and parts[0] != "" else 0
        except ValueError:
            code = 0
        cwd = parts[1].strip() if len(parts) > 1 else ""
        return code, cwd

    @staticmethod
    def _read_errfile(err_path: str) -> str:
        try:
            with open(err_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    @staticmethod
    def _cleanup_files(*paths) -> None:
        for path in paths:
            if not path:
                continue
            try:
                os.remove(path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Interruption / teardown
    # ------------------------------------------------------------------
    def interrupt(self) -> None:
        """Terminate the currently running command; keep the shell usable."""
        self._interrupt_event.set()
        # If no command is running we still want a healthy shell next time;
        # respawn-on-demand in run() handles a dead process.

    def _respawn_preserving_cwd(self) -> None:
        """Kill the shell and start a fresh one, restoring the last known cwd."""
        last_cwd = self._cwd
        self._terminate_process()
        if os.path.isdir(last_cwd):
            self._cwd = last_cwd
        self._spawn()
        # Restore the working directory in the new shell so the session
        # continues where it left off (env is best-effort across a respawn).
        if self._kind in ("powershell", "bash"):
            self._send(f"cd '{self._cwd}'")
        elif self._kind == "cmd":
            drive = os.path.splitdrive(self._cwd)[0]
            if drive:
                self._send(drive)
            self._send(f'cd "{self._cwd}"')

    def _terminate_process(self) -> None:
        if not self._proc:
            return
        try:
            if self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except OSError:
                    pass
            self._proc.kill()
            self._proc.wait(timeout=5)
        except Exception:
            pass

    def kill(self) -> None:
        """Fully terminate the shell process."""
        self._terminate_process()
        self._proc = None

    def get_cwd(self) -> str:
        return self._cwd


# ----------------------------------------------------------------------
# Module-level session singleton
# ----------------------------------------------------------------------
_shell_instance: Optional[PersistentShell] = None
_shell_lock = threading.Lock()


def get_shell() -> PersistentShell:
    """Return the session-scoped PersistentShell, creating it on first use."""
    global _shell_instance
    with _shell_lock:
        if _shell_instance is None or not _shell_instance._is_alive():
            _shell_instance = PersistentShell()
        return _shell_instance


def reset_shell() -> None:
    """Tear down the current session shell (used by tests / session reset)."""
    global _shell_instance
    with _shell_lock:
        if _shell_instance is not None:
            _shell_instance.kill()
            _shell_instance = None
