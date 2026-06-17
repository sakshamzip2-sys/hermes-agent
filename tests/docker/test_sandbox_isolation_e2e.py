"""End-to-end isolation proof for the hardened Docker sandbox backend.

Real containers, no mocks. Auto-skips when Docker is unavailable (see
tests/docker/conftest.py). Proves the properties the product depends on:
host code cannot be read from the sandbox, durable reconnect rebinds the SAME
container, and distinct tasks get distinct, mutually-invisible containers.
"""
import subprocess
import uuid

from tools.environments.docker import DockerEnvironment, _docker_container_alive

# Default agent image, already present on dev/VM hosts; has bash.
IMAGE = "nikolaik/python-nodejs:python3.11-nodejs20"


def _force_rm(*container_ids: str) -> None:
    for cid in container_ids:
        if cid:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=30)


def _new_env(task_id: str) -> DockerEnvironment:
    return DockerEnvironment(
        image=IMAGE, cwd="/workspace", timeout=60, task_id=task_id,
        persistent_filesystem=True, persist_across_processes=True,
    )


def test_code_runs_inside_container_not_host():
    env = _new_env(f"iso-{uuid.uuid4().hex[:8]}")
    cid = env.handle["container_id"]
    try:
        out = env.execute("hostname")["output"].strip()
        assert out and out != subprocess.run(
            ["hostname"], capture_output=True, text=True
        ).stdout.strip()
        assert "RC=0" in env.execute("cat /proc/1/cgroup >/dev/null 2>&1; echo RC=$?")["output"]
    finally:
        _force_rm(cid)


def test_host_secret_unreadable_from_sandbox(tmp_path):
    # secret on the host, OUTSIDE any container mount
    secret = tmp_path / f"HOST_SECRET_{uuid.uuid4().hex}"
    secret.write_text("TOP-SECRET")
    env = _new_env(f"iso-{uuid.uuid4().hex[:8]}")
    cid = env.handle["container_id"]
    try:
        r = env.execute(f"cat {secret} 2>&1; echo RC=$?")
        assert "TOP-SECRET" not in r["output"]
        assert "RC=0" not in r["output"]  # the read must fail
    finally:
        _force_rm(cid)


def test_durable_reconnect_rebinds_same_container():
    env = _new_env(f"dur-{uuid.uuid4().hex[:8]}")
    handle = env.handle
    cid = handle["container_id"]
    try:
        env.execute("echo MARKER > /workspace/marker.txt")
        env2 = DockerEnvironment.reconnect(handle, cwd="/workspace", timeout=60)
        assert env2 is not None
        assert env2.handle["container_id"] == cid
        # filesystem survived the reattach → same container
        assert "MARKER" in env2.execute("cat /workspace/marker.txt 2>&1")["output"]
    finally:
        _force_rm(cid)


def test_distinct_tasks_get_isolated_containers():
    a = _new_env(f"iso-a-{uuid.uuid4().hex[:8]}")
    b = _new_env(f"iso-b-{uuid.uuid4().hex[:8]}")
    cid_a, cid_b = a.handle["container_id"], b.handle["container_id"]
    try:
        assert cid_a != cid_b
        a.execute("echo A_ONLY > /workspace/a.txt")
        r = b.execute("cat /workspace/a.txt 2>&1; echo RC=$?")
        assert "A_ONLY" not in r["output"]  # B cannot see A's files
        assert "RC=0" not in r["output"]
    finally:
        _force_rm(cid_a, cid_b)


def test_force_remove_destroys_container():
    env = _new_env(f"md-{uuid.uuid4().hex[:8]}")
    cid = env.handle["container_id"]
    assert _docker_container_alive(cid)
    _force_rm(cid)  # explicit synchronous teardown (make/destroy)
    assert not _docker_container_alive(cid)
