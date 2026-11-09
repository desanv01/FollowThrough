"""Original offline controls for the explicit Python public-check backend.

Fake Docker lifecycle evidence is synthetic. Tiny subprocess controls execute only
owned print/sleep snippets, never candidate code, Docker or a provider.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from episode.check_bridge import LiveCheckBridge
from episode import live, python_check as backend

CID = "a" * 64
IMAGE_ID = "sha256:" + "b" * 64


def result(stdout=b"", exit_code=0):
    return dict(stdout=stdout, stderr=b"", exit_code=exit_code, spawn_failed=False,
                direct_child_reaped=True, reader_settled=True, timed_out=False,
                output_limit=False, elapsed_ms=1)


class FakeDocker:
    def __init__(self, *, mismatch=False, exit_code=0, unknown=False):
        self.mismatch, self.exit_code, self.unknown = mismatch, exit_code, unknown
        self.requests = []
        self.started = False
        self.removed = False
        self.facts = None

    def __call__(self, argv, env, deadline):
        self.requests.append(argv)
        args = argv[3:]
        if args[0] == "info":
            return result(json.dumps(dict(OSType="linux", Architecture="x86_64")).encode())
        if args[:2] == ["image", "inspect"]:
            return result(json.dumps(dict(Os="linux", Architecture="amd64", Id=IMAGE_ID, RepoDigests=[backend.IMAGE])).encode())
        if args[:2] == ["container", "create"]:
            labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
            mounts = [args[i + 1] for i, a in enumerate(args) if a == "--mount"]
            mount_facts = []
            for mount in mounts:
                fields = dict(item.split("=", 1) for item in mount.split(",") if "=" in item)
                mount_facts.append(dict(Type="bind", Source=fields["src"], Destination=fields["dst"], RW=False))
            self.facts = dict(Id=CID, Name="/" + args[args.index("--name") + 1], Image=IMAGE_ID,
                              Config=dict(Image=backend.IMAGE, User="65534:65534", Entrypoint=["python"],
                                          Cmd=["-I", "-B", "/workspace/public_checks.py"], WorkingDir="/workspace",
                                          Tty=False, OpenStdin=False, Labels=dict(x.split("=", 1) for x in labels)),
                              HostConfig=dict(NetworkMode="none", ReadonlyRootfs=True, CapDrop=["ALL"],
                                              Privileged=False, PidsLimit=64, Memory=268435456, NanoCpus=1000000000,
                                              SecurityOpt=["no-new-privileges"], LogConfig=dict(Type="none"),
                                              Tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"}), Mounts=mount_facts)
            return result(CID.encode() + b"\n")
        if args[0] == "inspect":
            facts = copy.deepcopy(self.facts)
            if self.mismatch:
                facts["Mounts"][0]["RW"] = True
            if self.unknown:
                facts["Config"]["Labels"][backend.OWNER] = "someone-else"
            facts["State"] = dict(Status="exited" if self.started else "created", Running=False,
                                  Paused=False, Restarting=False, OOMKilled=False, Dead=False, Error="",
                                  ExitCode=self.exit_code, StartedAt="2026-11-09T02:00:00Z",
                                  FinishedAt="2026-11-09T02:00:01Z")
            return result(json.dumps(facts).encode())
        if args[0] == "start":
            self.started = True
            return result(b"original authored public output\n", self.exit_code)
        if args[0] == "rm":
            self.removed = True
            return result(CID.encode())
        if args[0] == "ps":
            assert "--no-trunc" in args
            return result()
        raise AssertionError(args)


class PythonCheckTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "tmp" / "python-check-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "processor.py").write_text("# inert candidate\n")
        (self.workspace / "public_checks.py").write_text("# inert public check\n")
        self.docker = self.root / "fake-docker"
        self.docker.write_text("inert")

    def tearDown(self):
        self.tmp.cleanup()

    def bridge(self, fake):
        return LiveCheckBridge(backend.__file__, self.docker, self.root / "checks",
                               runner=backend.PythonCheckRunner(command=fake))

    def request(self):
        return dict(request_id="authored:1",
                    workspace=str(self.workspace), candidate_filename="processor.py",
                    candidate_sha256=hashlib.sha256((self.workspace / "processor.py").read_bytes()).hexdigest(),
                    requirements_version="v1")

    def test_bridge_pass_and_fail_bind_real_receipt_without_changing_projection(self):
        for code, expected in ((0, "passed"), (1, "failed")):
            fake = FakeDocker(exit_code=code)
            bridge = self.bridge(fake)
            bridge._counter = code
            request = self.request()
            response = bridge(request)
            self.assertEqual(response["status"], expected)
            self.assertEqual(response["candidate_sha256"], request["candidate_sha256"])
            self.assertEqual(response["exit_code"], code)
            self.assertEqual(response["cleanup"], "settled")
            self.assertTrue(fake.started and fake.removed)
            create = next(x for x in fake.requests if x[3:5] == ["container", "create"])
            self.assertEqual(create.count("--mount"), 2)
            self.assertFalse(any("type=bind" in x and "TASK.md" in x for x in create))

    def test_mutated_mount_refuses_start_and_cleans_only_verified_owned_container(self):
        fake = FakeDocker(mismatch=True)
        response = self.bridge(fake)(self.request())
        self.assertEqual(response["status"], "infrastructure_error")
        self.assertFalse(fake.started)
        self.assertTrue(fake.removed)
        self.assertEqual(response["cleanup"], "settled")

    def test_wrong_owner_is_never_removed_and_cleanup_remains_unknown(self):
        fake = FakeDocker(unknown=True)
        response = self.bridge(fake)(self.request())
        self.assertFalse(fake.started or fake.removed)
        self.assertEqual(response["status"], "infrastructure_error")
        self.assertEqual(response["cleanup"], "unknown")

    def test_bounded_subprocess_timeout_and_both_stream_overflow(self):
        for snippet, field in (("import time;time.sleep(5)", "timed_out"),
                               ("import sys;sys.stderr.write('x'*100000)", "output_limit"),
                               ("import sys;sys.stdout.write('x'*100000)", "output_limit")):
            observed = backend.bounded_command([sys.executable, "-I", "-c", snippet],
                                               backend.docker_env(), time.monotonic() + .5, cap=1024)
            self.assertTrue(observed[field], observed)
            self.assertTrue(observed["direct_child_reaped"])
            self.assertTrue(observed["reader_settled"])
            self.assertLessEqual(len(observed["stdout"]), 1024)
            self.assertLessEqual(len(observed["stderr"]), 1024)

    def test_explicit_backend_routing_and_rust_default(self):
        rust = live.build_check_port(self.docker, self.docker, self.root / "r")
        python = live.build_check_port(None, self.docker, self.root / "p", backend="python")
        self.assertEqual(rust.executable, self.docker)
        self.assertIsInstance(python.runner, backend.PythonCheckRunner)
        with self.assertRaises(live.LiveGateError):
            live.build_check_port(None, self.docker, self.root / "x", backend="other")
        operation = dict(episode_id="ep", operation_id="op", run_id="run", stage="development",
                         scenario_id="R3", arm="ordinary", trial=1, dataset_id="authored", campaign_id="fixture")
        gate = dict(gate_id="G4-R1-READY", status="READY", source=dict(commit="a" * 40),
                    operation={key: operation[key] for key in live.G4_OPERATION_KEYS},
                    limits=dict(max_calls=16, max_output_tokens_per_call=2048, max_call_seconds=25,
                                max_call_reservation_micro_usd=50000, max_operation_reservation_micro_usd=600000,
                                max_wall_seconds=900), budget=dict(campaign_id="fixture", cap_microdollars=20000000))
        path = self.root / "gate.json"
        with mock.patch("episode.live._git_head", return_value="a" * 40):
            path.write_text(json.dumps(gate))
            live.verify_g4_gate(path, self.root, operation)  # old Rust gates remain valid
            with self.assertRaises(live.LiveGateError):
                live.verify_g4_gate(path, self.root, operation, check_backend="python")
            gate["runtime"] = dict(check_backend="python", python_check_sha256="0" * 64)
            path.write_text(json.dumps(gate))
            with self.assertRaises(live.LiveGateError):
                live.verify_g4_gate(path, self.root, operation, check_backend="python")
            gate["runtime"]["python_check_sha256"] = hashlib.sha256(Path(backend.__file__).read_bytes()).hexdigest()
            path.write_text(json.dumps(gate))
            live.verify_g4_gate(path, self.root, operation, check_backend="python")


if __name__ == "__main__":
    unittest.main()
