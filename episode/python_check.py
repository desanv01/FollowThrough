"""Explicit Python Docker public-check backend; never executes candidate code on host.

The existing LiveCheckBridge owns request/inventory/version binding and projection.
This injected runner writes the same private container-check receipt contract, with
an explicit backend label. DockerSession's fixed controls and source normalization
are reused; its interactive grading protocol is deliberately not invoked.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from .check_bridge import BridgeProcessResult, _workspace_inventory
from .protected_session import IMAGE, _normalized_source, docker_env

LIMIT = 32 * 1024
TOTAL_SECONDS = 90.0
OWNER = "org.followthrough.owner_token"
RUN = "org.followthrough.run_id"
ENDPOINT = "npipe:////./pipe/docker_engine" if os.name == "nt" else "unix:///var/run/docker.sock"
_ID = re.compile(r"[0-9a-f]{64}\Z")
_RUN = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")
_FILES = {"processor.py", "preview.py", "public_checks.py", "TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json"}


class BackendError(Exception):
    pass


def _regular_path(path, directory=False):
    path = Path(path)
    if not path.is_absolute() or any(x == ".." for x in path.parts):
        raise BackendError("invalid_path")
    for item in (path, *path.parents):
        meta = item.lstat()
        if item.is_symlink() or getattr(meta, "st_file_attributes", 0) & 0x400:
            raise BackendError("reparse_path_refused")
    if (directory and not path.is_dir()) or (not directory and not path.is_file()):
        raise BackendError("invalid_path")
    value = str(path.resolve())
    if any(c in value for c in ",\r\n\x00"):
        raise BackendError("mount_delimiter_refused")
    return Path(value)


def bounded_command(argv, env, deadline, cap=LIMIT):
    """One absolute deadline includes kill/reap and both reader settlements."""
    started = time.monotonic()
    result = dict(exit_code=None, spawn_failed=False, direct_child_reaped=False,
                  reader_settled=False, timed_out=False, output_limit=False,
                  stdout=b"", stderr=b"", elapsed_ms=0)
    if deadline <= started:
        result["timed_out"] = True
        return result
    try:
        proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        result["spawn_failed"] = True
        return result
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    failed = threading.Event()

    def reader(name, pipe):
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                room = cap - len(streams[name])
                streams[name].extend(chunk[:max(0, room)])
                if len(chunk) > room:
                    overflow.set()
                    break
        except OSError:
            failed.set()
        finally:
            pipe.close()

    readers = [threading.Thread(target=reader, args=(name, getattr(proc, name)), daemon=True)
               for name in streams]
    for thread in readers:
        thread.start()
    reserve = min(.4, max(.02, (deadline - started) / 5))
    wait_until = deadline - reserve
    while proc.poll() is None and not overflow.is_set() and time.monotonic() < wait_until:
        time.sleep(.005)
    if proc.poll() is None:
        result["timed_out"] = not overflow.is_set()
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=max(.001, (deadline - time.monotonic()) / 2))
        result["direct_child_reaped"] = True
        result["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired:
        pass
    for thread in readers:
        thread.join(timeout=max(0, deadline - time.monotonic()))
    result.update(reader_settled=not failed.is_set() and all(not t.is_alive() for t in readers),
                  output_limit=overflow.is_set(), stdout=bytes(streams["stdout"]),
                  stderr=bytes(streams["stderr"]), elapsed_ms=int((time.monotonic() - started) * 1000))
    return result


def _clean(result):
    return (not result["spawn_failed"] and result["direct_child_reaped"]
            and result["reader_settled"] and not result["timed_out"]
            and not result["output_limit"])


class PythonCheckRunner:
    """LiveCheckBridge runner, fixed argv interface; injected commands are tests only."""
    def __init__(self, command=None):
        self.command = command or bounded_command

    def __call__(self, argv, timeout):
        try:
            if len(argv) != 10 or argv[1::2] != ["--workspace", "--run-id", "--output-dir", "--docker", "--execute"]:
                raise BackendError("invalid_backend_arguments")
            workspace, run_id, output, docker = argv[2], argv[4], argv[6], argv[8]
            operation = _Operation(workspace, run_id, output, docker, timeout, self.command)
            return operation.execute()
        except (BackendError, OSError, ValueError):
            return BridgeProcessResult(2, b"", b"python_check_refused")


class _Operation:
    def __init__(self, workspace, run_id, output, docker, timeout, command):
        self.workspace = _regular_path(workspace, True)
        self.docker = _regular_path(docker)
        if not _RUN.fullmatch(run_id):
            raise BackendError("invalid_run_id")
        self.run_id = run_id
        self.output = Path(output)
        parent = _regular_path(self.output.parent, True)
        self.output = parent / self.output.name
        if self.output.exists() or self.output.is_symlink() or self.output.name in ("", ".", ".."):
            raise BackendError("output_exists")
        if self.output.is_relative_to(self.workspace) or self.workspace.is_relative_to(self.output) or self.docker.is_relative_to(self.workspace):
            raise BackendError("invalid_output")
        self.inventory = _workspace_inventory(self.workspace)
        if not self.inventory or any(item["name"] not in _FILES or item["bytes"] > 1024 * 1024 for item in self.inventory):
            raise BackendError("invalid_inventory")
        if sum(item["bytes"] for item in self.inventory) > 2 * 1024 * 1024:
            raise BackendError("inventory_limit")
        names = {item["name"] for item in self.inventory}
        candidates = names & {"processor.py", "preview.py"}
        if len(candidates) != 1 or "public_checks.py" not in names:
            raise BackendError("invalid_inventory")
        self.candidate = next(iter(candidates))
        for name in names:
            _regular_path(self.workspace / name)
        self.output.mkdir()
        (self.output / "docker-config").mkdir()
        self.env = docker_env()
        self.env["DOCKER_CONFIG"] = str(self.output / "docker-config")
        self.started = time.monotonic()
        self.deadline = self.started + min(TOTAL_SECONDS, max(1., float(timeout)))
        self.command = command
        self.token = uuid.uuid4().hex
        self.name = "ft-check-" + run_id
        self.id = None
        self.create_attempted = False
        self.commands = []
        self.attached = None
        self.public_exit = None
        self.image_id = None
        self.witness = None
        self.post_witness = None
        self.cleanup = dict(state="unknown", method="not_created", removal_verified=False)

    def run(self, name, args, seconds=5, cleanup=False):
        deadline = min(self.cleanup_deadline if cleanup else self.deadline - 5, time.monotonic() + seconds)
        result = self.command([str(self.docker), "--host", ENDPOINT, *args], self.env, deadline)
        record = {k: v for k, v in result.items() if k not in ("stdout", "stderr")}
        record.update(name=name, argv=["--host", ENDPOINT, *args],
                      stdout_bytes=len(result["stdout"]), stderr_bytes=len(result["stderr"]),
                      stderr_excerpt=result["stderr"][:1024].decode("utf-8", "replace"))
        self.commands.append(record)
        return result

    def json_command(self, name, args, cleanup=False):
        result = self.run(name, args, cleanup=cleanup)
        if not _clean(result) or result["exit_code"] != 0:
            raise BackendError(name + "_failed")
        try:
            return json.loads(result["stdout"])
        except (ValueError, UnicodeDecodeError):
            raise BackendError(name + "_malformed") from None

    def identity(self, facts):
        try:
            labels = facts["Config"]["Labels"]
            return (type(facts) is dict and facts["Id"] == self.id
                    and facts["Name"] == "/" + self.name
                    and type(labels) is dict and labels.get(OWNER) == self.token
                    and labels.get(RUN) == self.run_id)
        except (KeyError, TypeError):
            return False

    def witness_ok(self, facts):
        try:
            if not self.identity(facts) or facts["Image"] != self.image_id:
                return False
            config, host = facts["Config"], facts["HostConfig"]
            exact = {"NetworkMode": "none", "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                     "Privileged": False, "PidsLimit": 64, "Memory": 256 * 1024 * 1024,
                     "NanoCpus": 1_000_000_000}
            for key, value in exact.items():
                if type(host[key]) is not type(value) or host[key] != value:
                    return False
            if (config["Image"] != IMAGE or config["User"] != "65534:65534"
                    or config["Entrypoint"] != ["python"]
                    or config["Cmd"] != ["-I", "-B", "/workspace/public_checks.py"]
                    or config["WorkingDir"] != "/workspace" or config["Tty"] is not False
                    or config["OpenStdin"] is not False):
                return False
            if host.get("CapAdd") not in (None, []) or host.get("Binds") not in (None, []):
                return False
            if host["SecurityOpt"] not in (["no-new-privileges"], ["no-new-privileges=true"]):
                return False
            if host["LogConfig"]["Type"] != "none":
                return False
            tmpfs = host["Tmpfs"]
            if type(tmpfs) is not dict or set(tmpfs) != {"/tmp"}:
                return False
            if set(tmpfs["/tmp"].split(",")) != {"rw", "noexec", "nosuid", "size=16m"}:
                return False
            mounts = facts["Mounts"]
            expected = {"/workspace/" + n: _normalized_source(str(self.workspace / n))
                        for n in (self.candidate, "public_checks.py")}
            if type(mounts) is not list or len(mounts) != 2:
                return False
            seen = set()
            for mount in mounts:
                dest = mount["Destination"]
                if (dest not in expected or dest in seen or mount["Type"] != "bind"
                        or mount["RW"] is not False
                        or _normalized_source(mount["Source"]) != expected[dest]):
                    return False
                seen.add(dest)
            return seen == set(expected)
        except (KeyError, TypeError, AttributeError):
            return False

    def create_args(self):
        args = ["container", "create", "--pull=never", "--name", self.name,
                "--label", f"{OWNER}={self.token}", "--label", f"{RUN}={self.run_id}",
                "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--pids-limit=64", "--memory=256m", "--cpus=1", "--user=65534:65534",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--workdir=/workspace", "--log-driver=none"]
        for name in (self.candidate, "public_checks.py"):
            args += ["--mount", f"type=bind,src={self.workspace / name},dst=/workspace/{name},readonly"]
        return args + ["--entrypoint=python", IMAGE, "-I", "-B", "/workspace/public_checks.py"]

    def lifecycle(self):
        info = self.json_command("info", ["info", "--format", "{{json .}}"])
        if info.get("OSType") != "linux":
            raise BackendError("daemon_not_linux")
        image = self.json_command("image_inspect", ["image", "inspect", "--format", "{{json .}}", IMAGE])
        arches = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}
        if (image.get("Os") != "linux" or IMAGE not in image.get("RepoDigests", [])
                or arches.get(image.get("Architecture")) is None
                or arches.get(image.get("Architecture")) != arches.get(info.get("Architecture"))
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(image.get("Id")))):
            raise BackendError("image_identity_mismatch")
        self.image_id = image["Id"]
        if _workspace_inventory(self.workspace) != self.inventory:
            raise BackendError("inventory_changed")
        self.create_attempted = True
        create = self.run("create", self.create_args())
        container_id = create["stdout"].decode("ascii", "replace").strip()
        if not _clean(create) or create["exit_code"] != 0 or not _ID.fullmatch(container_id):
            raise BackendError("create_ambiguous")
        self.id = container_id
        facts = self.json_command("inspect_pre_start", ["inspect", "--format", "{{json .}}", self.id])
        if not self.witness_ok(facts) or facts["State"].get("Status") != "created" or facts["State"].get("Running") is not False:
            raise BackendError("isolation_mismatch")
        if _workspace_inventory(self.workspace) != self.inventory:
            raise BackendError("inventory_changed")
        self.witness = facts
        self.attached = self.run("start_attach", ["start", "--attach", self.id], seconds=60)
        if self.attached["output_limit"]:
            return "output_limit"
        if self.attached["timed_out"]:
            return "timeout"
        if not _clean(self.attached):
            raise BackendError("attach_unsettled")
        post = self.json_command("inspect_post_attach", ["inspect", "--format", "{{json .}}", self.id])
        self.post_witness = post
        state = post.get("State", {})
        if (not self.witness_ok(post) or any(state.get(k) is not False for k in ("Running", "Paused", "Restarting", "OOMKilled", "Dead"))
                or state.get("Status") != "exited" or state.get("Error") != ""
                or type(state.get("ExitCode")) is not int):
            raise BackendError("post_state_invalid")
        try:
            start = datetime.fromisoformat(state["StartedAt"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(state["FinishedAt"].replace("Z", "+00:00"))
            if start.year < 1970 or end < start:
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise BackendError("post_times_invalid") from None
        self.public_exit = state["ExitCode"]
        if self.attached["exit_code"] not in (0, self.public_exit):
            raise BackendError("attach_exit_mismatch")
        return "check_passed" if self.public_exit == 0 else "check_failed"

    def settle(self):
        self.cleanup_deadline = min(self.deadline, time.monotonic() + 5)
        if not self.create_attempted:
            self.cleanup = dict(state="settled", method="not_created", removal_verified=False, detail="create_not_attempted")
            return
        try:
            # Discover ambiguous creates only by both unguessable owner and run labels.
            if self.id is None:
                listed = self.run("cleanup_discover", ["ps", "-a", "--no-trunc", "--quiet", "--filter", f"label={OWNER}={self.token}", "--filter", f"label={RUN}={self.run_id}", "--filter", "name=^/" + self.name + "$"], cleanup=True)
                ids = listed["stdout"].decode("ascii", "replace").split()
                if not _clean(listed) or listed["exit_code"] != 0 or len(ids) != 1 or not _ID.fullmatch(ids[0]):
                    return
                self.id = ids[0]
            facts = self.json_command("cleanup_inspect", ["inspect", "--format", "{{json .}}", self.id], cleanup=True)
            if not self.identity(facts):
                return
            removed = self.run("cleanup_remove", ["rm", "--force", self.id], cleanup=True)
            if not _clean(removed) or removed["exit_code"] != 0:
                return
            for key, value in (("id", self.id), ("name", "^/" + self.name + "$"), ("label", f"{OWNER}={self.token}")):
                absent = self.run("cleanup_absence_" + key, ["ps", "-a", "--no-trunc", "--quiet", "--filter", key + "=" + value], cleanup=True)
                if not _clean(absent) or absent["exit_code"] != 0 or absent["stdout"].strip():
                    return
            self.cleanup = dict(state="settled", method="rm_force_owned", removal_verified=True)
        except (BackendError, OSError, ValueError):
            pass

    def execute(self):
        status, diagnostic = "infrastructure_error", None
        try:
            status = self.lifecycle()
        except (BackendError, OSError, ValueError, TypeError, KeyError) as error:
            diagnostic = str(error) if isinstance(error, BackendError) else "backend_failed"
        finally:
            self.settle()
        if self.cleanup["state"] != "settled":
            status, diagnostic = "infrastructure_error", "cleanup_unknown"
        stdout = self.attached["stdout"] if self.attached else b""
        stderr = self.attached["stderr"] if self.attached else b""
        (self.output / "stdout.txt").write_bytes(stdout)
        (self.output / "stderr.txt").write_bytes(stderr)
        candidate = next(x for x in self.inventory if x["name"] == self.candidate)
        receipt = dict(schema_version="container-check-0.1", evidence_kind="real_container_check",
                       backend="python-docker-public-0.1", backend_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       run_id=self.run_id, status=status, diagnostic=diagnostic,
                       workspace=dict(path=str(self.workspace), candidate_file=self.candidate,
                                      candidate_sha256=candidate["sha256"], inventory=self.inventory),
                       image=dict(reference=IMAGE, observed_id=self.image_id),
                       container=(dict(id=self.id, name=self.name, owner_token=self.token,
                                       attached=({k: self.attached[k] for k in ("exit_code", "timed_out", "output_limit", "direct_child_reaped")} if self.attached else None)) if self.id else None),
                       public_check_exit=self.public_exit, commands=self.commands, cleanup=self.cleanup,
                       isolation_witness=self.witness, post_witness=self.post_witness,
                       elapsed_ms=int((time.monotonic() - self.started) * 1000),
                       limitations=["local_trusted_docker_daemon_assumption", "stable_filesystem_assumption", "direct_child_only_process_settlement", "public_check_is_not_hidden_functional_grade", "explicit_python_backend_not_rust_qualification"])
        with (self.output / "receipt.json").open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, sort_keys=True)
        code = 0 if status == "check_passed" else 1 if status == "check_failed" else 2
        return BridgeProcessResult(code, b"", b"")
