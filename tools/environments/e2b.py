"""E2B execution environment (cloud or self-hosted).

Runs agent commands inside isolated E2B Firecracker microVM sandboxes via the
E2B Python SDK. Points at E2B cloud OR a self-hosted endpoint — set
``E2B_API_KEY`` plus, for self-host, ``E2B_DOMAIN`` / ``E2B_API_URL`` (e.g. the
24/7 E2B container on a VM).

Durable resume (CMA-style): the sandbox is *paused* on cleanup (filesystem
preserved) and reattached via ``Sandbox.connect(sandbox_id)`` on resume — see
``handle`` / ``reconnect`` and the durable-session wiring in terminal_tool /
hermes_state. Per-call isolation: each task_id gets its own sandbox.

Modeled on :mod:`tools.environments.daytona` (heredoc stdin, blocking SDK calls
wrapped in a _ThreadedProcessHandle, FileSyncManager for ~/.hermes parity).
"""

import logging
import os
import shlex
import threading
from pathlib import Path

from tools.environments.base import (
    BaseEnvironment,
    _ThreadedProcessHandle,
)
from tools.environments.file_sync import (
    FileSyncManager,
    iter_sync_files,
    quoted_mkdir_command,
    quoted_rm_command,
    unique_parent_dirs,
)

logger = logging.getLogger(__name__)


class E2BEnvironment(BaseEnvironment):
    """E2B sandbox execution backend (one dedicated sandbox per task_id)."""

    _stdin_mode = "heredoc"

    def __init__(
        self,
        image: str = "base",
        cwd: str = "/home/user",
        timeout: int = 60,
        *,
        task_id: str = "default",
        persistent_filesystem: bool = True,
        sandbox_timeout: int = 3600,
        api_key: str | None = None,
        domain: str | None = None,
        reuse_sandbox_id: str | None = None,
        env: dict | None = None,
    ):
        requested_cwd = cwd
        super().__init__(cwd=cwd, timeout=timeout, env=env or {})

        try:
            from tools.lazy_deps import ensure as _lazy_ensure
            _lazy_ensure("terminal.e2b", prompt=False)
        except ImportError:
            pass
        except Exception as e:
            raise ImportError(str(e))
        from e2b import Sandbox

        self._task_id = task_id
        self._persistent = persistent_filesystem
        self._lock = threading.Lock()
        self._api_key = api_key or os.getenv("E2B_API_KEY")
        self._domain = domain or os.getenv("E2B_DOMAIN")

        # Only pass connection overrides the SDK understands; otherwise it falls
        # back to E2B_API_KEY / E2B_DOMAIN / E2B_API_URL env vars on its own.
        conn: dict = {}
        if self._api_key:
            conn["api_key"] = self._api_key
        if self._domain:
            conn["domain"] = self._domain

        if reuse_sandbox_id:
            # Durable resume — reattach to the existing (possibly paused) sandbox.
            self._sandbox = Sandbox.connect(reuse_sandbox_id, **conn)
            logger.info(
                "E2B: reconnected to sandbox %s (task=%s)", reuse_sandbox_id, task_id
            )
        else:
            self._sandbox = Sandbox.create(
                template=image or "base",
                timeout=sandbox_timeout,
                metadata={"hermes_task_id": task_id},
                **conn,
            )
            logger.info(
                "E2B: created sandbox %s (task=%s)", self._sandbox.sandbox_id, task_id
            )

        # Resolve the sandbox home directory (E2B base template uses /home/user).
        self._remote_home = "/home/user"
        try:
            res = self._sandbox.commands.run("echo $HOME", timeout=10)
            home = (getattr(res, "stdout", "") or "").strip()
            if home:
                self._remote_home = home
                if requested_cwd in {"~", "/home/user"}:
                    self.cwd = home
        except Exception:
            pass

        self._sync_manager = FileSyncManager(
            get_files_fn=lambda: iter_sync_files(f"{self._remote_home}/.hermes"),
            upload_fn=self._e2b_upload,
            delete_fn=self._e2b_delete,
            bulk_upload_fn=self._e2b_bulk_upload,
            bulk_download_fn=self._e2b_bulk_download,
        )
        try:
            self._sync_manager.sync(force=True)
        except Exception as e:
            logger.warning("E2B: initial file sync failed: %s", e)
        self.init_session()

    # ------------------------------------------------------------------
    # Durable reconnect seam
    # ------------------------------------------------------------------

    @property
    def handle(self):
        sandbox = getattr(self, "_sandbox", None)
        sandbox_id = getattr(sandbox, "sandbox_id", None)
        if not sandbox_id or not getattr(self, "_persistent", False):
            return None
        return {
            "backend": "e2b",
            "task_id": getattr(self, "_task_id", "default"),
            "sandbox_id": sandbox_id,
        }

    @classmethod
    def reconnect(cls, handle, *, cwd, timeout, env=None):
        sandbox_id = handle.get("sandbox_id")
        if not sandbox_id:
            return None
        try:
            return cls(
                cwd=cwd,
                timeout=timeout,
                env=env,
                task_id=handle.get("task_id", "default"),
                reuse_sandbox_id=sandbox_id,
                persistent_filesystem=True,
            )
        except Exception as exc:  # noqa: BLE001 — reattach must fail closed
            logger.debug("E2B reconnect to %s failed: %s", sandbox_id, exc)
            return None

    # ------------------------------------------------------------------
    # File sync callbacks
    # ------------------------------------------------------------------

    def _e2b_upload(self, host_path: str, remote_path: str) -> None:
        parent = str(Path(remote_path).parent)
        self._sandbox.commands.run(f"mkdir -p {shlex.quote(parent)}")
        with open(host_path, "rb") as f:
            self._sandbox.files.write(remote_path, f.read())

    def _e2b_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        if not files:
            return
        parents = unique_parent_dirs(files)
        if parents:
            self._sandbox.commands.run(quoted_mkdir_command(parents))
        for host_path, remote_path in files:
            with open(host_path, "rb") as f:
                self._sandbox.files.write(remote_path, f.read())

    def _e2b_bulk_download(self, dest: Path) -> None:
        rel_base = f"{self._remote_home}/.hermes".lstrip("/")
        remote_tar = f"/tmp/.hermes_sync.{os.getpid()}.tar"
        self._sandbox.commands.run(
            f"tar cf {shlex.quote(remote_tar)} -C / {shlex.quote(rel_base)}"
        )
        data = self._sandbox.files.read(remote_tar, format="bytes")
        with open(dest, "wb") as f:
            f.write(data)
        try:
            self._sandbox.commands.run(f"rm -f {shlex.quote(remote_tar)}")
        except Exception:
            pass

    def _e2b_delete(self, remote_paths: list[str]) -> None:
        self._sandbox.commands.run(quoted_rm_command(remote_paths))

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _before_execute(self) -> None:
        try:
            self._sync_manager.sync()
        except Exception as e:
            logger.debug("E2B: pre-exec sync skipped: %s", e)

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ):
        sandbox = self._sandbox
        persistent = self._persistent
        if login:
            shell_cmd = f"bash -l -c {shlex.quote(cmd_string)}"
        else:
            shell_cmd = f"bash -c {shlex.quote(cmd_string)}"

        # Run as a BACKGROUND command so interrupt/timeout can kill the COMMAND,
        # not the whole sandbox. Killing the sandbox (sandbox.kill) on every
        # command timeout would destroy a persistent/durable session's
        # filesystem — exactly the failure mode we must avoid.
        state: dict = {"handle": None}

        def cancel():
            handle = state.get("handle")
            if handle is not None:
                try:
                    handle.kill()  # kill the command; the sandbox (filesystem) survives
                    return
                except Exception:
                    pass
            # cancel raced command startup (no handle yet). Only an *ephemeral*
            # sandbox may be torn down here; a persistent one must never be killed.
            if not persistent:
                try:
                    sandbox.kill()
                except Exception:
                    pass

        def exec_fn() -> tuple[str, int]:
            handle = sandbox.commands.run(shell_cmd, timeout=timeout, background=True)
            state["handle"] = handle
            try:
                res = handle.wait()
                out = (getattr(res, "stdout", "") or "") + (getattr(res, "stderr", "") or "")
                code = getattr(res, "exit_code", None)
                return (out, code if code is not None else 0)
            except Exception as e:
                # E2B raises CommandExitError on non-zero exit; it carries the
                # full result (stdout/stderr/exit_code). Surface that instead of
                # propagating, so non-zero exits behave like every other backend.
                code = getattr(e, "exit_code", None)
                if code is None:
                    raise
                out = (getattr(e, "stdout", "") or "") + (getattr(e, "stderr", "") or "")
                return (out, code)

        return _ThreadedProcessHandle(exec_fn, cancel_fn=cancel)

    def cleanup(self):
        with self._lock:
            sandbox = getattr(self, "_sandbox", None)
            if sandbox is None:
                return

            if getattr(self, "_sync_manager", None):
                try:
                    self._sync_manager.sync_back()
                except Exception as e:
                    logger.warning("E2B: sync_back failed: %s", e)

            try:
                if self._persistent:
                    # Pause preserves the filesystem; Sandbox.connect auto-resumes
                    # on the next reconnect. Falls back to leaving the sandbox
                    # alive (its keep-alive timeout) if pause is unavailable.
                    pause = getattr(sandbox, "beta_pause", None)
                    if callable(pause):
                        try:
                            pause()
                            logger.info(
                                "E2B: paused sandbox %s (filesystem preserved)",
                                sandbox.sandbox_id,
                            )
                        except Exception as e:
                            logger.warning(
                                "E2B: pause failed (%s); sandbox %s left alive until timeout",
                                e, sandbox.sandbox_id,
                            )
                    else:
                        logger.info(
                            "E2B: sandbox %s left alive (no pause API)",
                            sandbox.sandbox_id,
                        )
                else:
                    sandbox.kill()
                    logger.info("E2B: killed sandbox %s", sandbox.sandbox_id)
            except Exception as e:
                logger.warning("E2B: cleanup failed: %s", e)
            self._sandbox = None
