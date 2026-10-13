"""Polling autoscaler for a personal GitHub account; one ephemeral runner per job."""
import fcntl
import json
import logging
import os
import re
import signal
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

from docker_host import Docker, PREFIX
from github_api import APIError, GitHub
from policy import accepts_job, eligible_repository, plan, trusted_run

LOG = logging.getLogger("autoscaler")


def csv(name, default=""):
    return {v.strip().lower() for v in os.environ.get(name, default).split(",") if v.strip()}


def positive(name, default):
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


class Scaler:
    def __init__(self, api=None, docker=None):
        self.owner = os.environ.get("GITHUB_OWNER", "aga-b")
        self.pool = os.environ.get("POOL_NAME", "agah-ci")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", self.pool):
            raise ValueError("POOL_NAME must be a short lowercase Docker-safe name")
        self.maximum = positive("MAX_RUNNERS", 2)
        self.per_repo = positive("MAX_RUNNERS_PER_REPO", self.maximum)
        self.poll = positive("POLL_SECONDS", 30)
        self.refresh = positive("REPO_REFRESH_SECONDS", 300)
        self.idle_timeout = positive("IDLE_TIMEOUT_SECONDS", 600)
        self.lifetime = positive("MAX_LIFETIME_SECONDS", 10800)
        self.runner_image = os.environ.get("RUNNER_IMAGE", "personal-ci-runner:local")
        self.dind_image = os.environ.get("DIND_IMAGE", "")
        if not self.dind_image:
            raise ValueError("Run scripts/build.sh to resolve DIND_IMAGE")
        self.labels = csv("RUNNER_LABELS", "self-hosted,linux,x64,ci-linux")
        self.required = os.environ.get("REQUIRED_LABEL", "ci-linux").lower()
        if self.required not in self.labels or not {"self-hosted", "linux", "x64"} <= self.labels:
            raise ValueError("RUNNER_LABELS must include self-hosted,linux,x64 and REQUIRED_LABEL")
        self.actors = csv("ALLOWED_ACTORS", self.owner)
        self.includes, self.excludes = csv("INCLUDE_REPOS"), csv("EXCLUDE_REPOS")
        self.state = Path(os.environ.get("STATE_DIR", "/state"))
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "logs").mkdir(exist_ok=True)
        (self.state / "provisioning").mkdir(exist_ok=True)
        self.api, self.docker = api or GitHub(), docker or Docker()
        self.repos, self.next_refresh = [], 0
        self.cooldown = {}
        self.errors = []
        self.busy = set()

    def refresh_repositories(self):
        if time.time() < self.next_refresh:
            return
        allowed = []
        for repo in self.api.repositories():
            if not eligible_repository(repo, self.owner, self.includes, self.excludes):
                continue
            if not repo.get("private", False):
                # Existing registrations can receive other jobs too. Enforce GitHub's
                # external-PR approval boundary, not just a controller-side filter.
                path = f"/repos/{repo['full_name']}/actions/permissions/fork-pr-contributor-approval"
                try:
                    policy = self.api.request("GET", path)
                    if policy.get("approval_policy") != "all_external_contributors":
                        self.api.request("PUT", path, {"approval_policy": "all_external_contributors"})
                        LOG.info("Required external-PR approval for %s", repo["full_name"])
                except APIError as error:
                    self.errors.append(str(error))
                    LOG.warning("Public repo excluded: cannot enforce approval policy for %s", repo["full_name"])
                    continue
            allowed.append(repo)
        self.repos = allowed
        self.next_refresh = time.time() + self.refresh

    def record(self, slot, data):
        write_json(self.state / "provisioning" / f"{slot}.json", data)

    def provision(self, repo):
        now = int(time.time())
        slot = f"{self.pool}-{now}-{uuid.uuid4().hex[:8]}"
        data = {"repo": repo, "born": now, "runner_id": None, "slot": slot}
        self.record(slot, data)
        labels = {"pool": self.pool, "slot": slot, "repo": repo, "born": str(now)}
        mounts = []
        for suffix, path in (("work", "/home/runner/_work"), ("externals", "/home/runner/externals"),
                             ("tools", "/opt/hostedtoolcache")):
            volume = slot + "-" + suffix
            self.docker.call("volume", "create", *self.docker.labels(labels), volume)
            mounts.extend(["--mount", f"type=volume,source={volume},target={path}"])
        network = slot + "-net"
        self.docker.call("network", "create", *self.docker.labels(labels), network)
        # Create runner first so Docker initializes shared externals/tool volumes
        # from the runner image. A later create supplies its JIT config.
        initializer = self.docker.call("create", "--name", slot + "-init",
                                      *self.docker.labels({**labels, "role": "init"}),
                                      *mounts, self.runner_image, "true")
        self.docker.call("rm", initializer)
        dind = self.docker.call("run", "-d", "--name", slot + "-docker", "--network", network,
                               "--privileged", "--memory", os.environ.get("DIND_MEMORY", "2g"),
                               "--memory-swap", os.environ.get("DIND_MEMORY", "2g"),
                               "--cpus", os.environ.get("DIND_CPUS", "1"), "--pids-limit", "2048",
                               "--log-opt", "max-size=10m", "--log-opt", "max-file=2",
                               "--env", "DOCKER_TLS_CERTDIR=", *mounts,
                               *self.docker.labels({**labels, "role": "dind"}),
                               self.dind_image, "dockerd", "--host=unix:///var/run/docker.sock",
                               "--host=tcp://127.0.0.1:2375", "--tls=false")
        ready = False
        for _ in range(30):
            try:
                self.docker.call("exec", dind, "docker", "info", timeout=5)
                ready = True
                break
            except Exception:
                time.sleep(1)
        if not ready:
            raise RuntimeError("Per-job Docker daemon did not become ready")
        jit = self.api.request("POST", f"/repos/{repo}/actions/runners/generate-jitconfig",
                               {"name": slot, "runner_group_id": 1, "labels": sorted(self.labels),
                                "work_folder": "_work"})
        data["runner_id"] = jit["runner"]["id"]
        self.record(slot, data)
        self.docker.call("run", "-d", "--name", slot, "--network", f"container:{dind}",
                         "--memory", os.environ.get("RUNNER_MEMORY", "4g"),
                         "--memory-swap", os.environ.get("RUNNER_MEMORY", "4g"),
                         "--cpus", os.environ.get("RUNNER_CPUS", "2"), "--pids-limit", "2048",
                         "--shm-size", "1g", "--init",
                         "--log-opt", "max-size=10m", "--log-opt", "max-file=2",
                         "--env", "RUNNER_JIT_CONFIG", "--env", "DOCKER_HOST=tcp://127.0.0.1:2375",
                         "--env", "RUNNER_NAME=" + slot, *mounts,
                         *self.docker.labels({**labels, "role": "runner", "runner-id": str(data["runner_id"])}),
                         self.runner_image, env={"RUNNER_JIT_CONFIG": jit["encoded_jit_config"]})
        LOG.info("Provisioned %s for %s", slot, repo)

    def preserve_logs(self, container, slot, role):
        # Bounded console logs; GitHub job logs remain the primary CI logs.
        output = self.docker.call("logs", "--tail", "5000", container, missing_ok=True, raw=True)
        path = self.state / "logs" / f"{slot}-{role}.log"
        path.write_bytes(output[-2_000_000:])
        path.chmod(0o600)
        if role == "runner":
            # Runner diagnostic files survive removal of the ephemeral container.
            self.docker.call("cp", f"{container}:/home/runner/_diag", str(self.state / "logs" / slot),
                             missing_ok=True, timeout=30)

    def retire(self, slot, repo, runner_id=None):
        for item in self.docker.objects("container", self.pool):
            labels = item["Config"]["Labels"]
            if labels.get(f"{PREFIX}.slot") == slot:
                if item["State"]["Status"] == "running":
                    self.docker.call("stop", "--time", "30", item["Id"], timeout=45)
                role = labels.get(f"{PREFIX}.role", "unknown")
                self.preserve_logs(item["Id"], slot, role)
        if runner_id:
            try:
                self.api.request("DELETE", f"/repos/{repo}/actions/runners/{runner_id}")
            except APIError as error:
                if error.status != 404:
                    raise
        self.docker.remove_slot(self.pool, slot)
        (self.state / "provisioning" / f"{slot}.json").unlink(missing_ok=True)
        LOG.info("Retired %s", slot)

    def reconcile(self, registrations):
        now = time.time()
        containers = self.docker.objects("container", self.pool)
        runners = {item["Config"]["Labels"][f"{PREFIX}.slot"]: item for item in containers
                   if item["Config"]["Labels"].get(f"{PREFIX}.role") == "runner"}
        # Include Docker-only reservations when provisioning was interrupted.
        for item in containers:
            labels = item["Config"]["Labels"]
            slot = labels[f"{PREFIX}.slot"]
            record = self.state / "provisioning" / f"{slot}.json"
            if not record.exists():
                self.record(slot, {"slot": slot, "repo": labels[f"{PREFIX}.repo"],
                                   "born": int(labels[f"{PREFIX}.born"]),
                                   "runner_id": labels.get(f"{PREFIX}.runner-id")})
        active, idle = Counter(), Counter()
        local_names = set(runners)
        for file in (self.state / "provisioning").glob("*.json"):
            data = json.loads(file.read_text())
            slot, repo = data["slot"], data["repo"]
            runner = runners.get(slot)
            remote = registrations.get(repo, {}).get(slot)
            age = now - data["born"]
            if data.get("seen_busy"):
                self.busy.add(slot)
            busy = bool(remote and remote.get("busy"))
            if busy:
                self.busy.add(slot)
                if not data.get("seen_busy"):
                    data["seen_busy"] = True
                    self.record(slot, data)
            finished = runner is not None and runner["State"]["Status"] in {"exited", "dead"}
            # Do not infer idleness from a failed API call. The hard lifetime bound
            # still ensures runaway containers eventually stop.
            known_idle = repo in registrations and not busy and slot not in self.busy
            expired = age > self.lifetime or (known_idle and age > self.idle_timeout)
            orphan = runner is None and age > self.idle_timeout
            if finished or expired or orphan:
                try:
                    self.retire(slot, repo, data.get("runner_id"))
                    self.busy.discard(slot)
                    local_names.discard(slot)
                    continue
                except Exception as error:
                    self.errors.append(f"Retire failed: {slot} ({type(error).__name__})")
            active[repo] += 1
            if not busy and slot not in self.busy:
                idle[repo] += 1
        # Clean JIT registrations left behind between the API call and journaling.
        pattern = re.compile(re.escape(self.pool) + r"-(\d+)-[a-f0-9]{8}$")
        for repo, entries in registrations.items():
            for name, entry in entries.items():
                match = pattern.fullmatch(name)
                if match and name not in local_names and not entry.get("busy"):
                    if now - int(match[1]) > self.idle_timeout:
                        try:
                            self.api.request("DELETE", f"/repos/{repo}/actions/runners/{entry['id']}")
                        except APIError as error:
                            if error.status != 404:
                                self.errors.append(str(error))
        return active, idle

    def queue(self, repo):
        result = []
        seen = set()
        for status in ("queued", "in_progress"):
            runs = self.api.pages(f"/repos/{repo['full_name']}/actions/runs?status={status}",
                                  "workflow_runs", cached=True)
            for run in runs:
                if run["id"] in seen or not trusted_run(run, repo, self.actors):
                    continue
                seen.add(run["id"])
                for job in self.api.pages(f"/repos/{repo['full_name']}/actions/runs/{run['id']}/jobs?filter=latest",
                                           "jobs", cached=True):
                    if accepts_job(job, self.labels, self.required):
                        result.append(job)
        return result

    def tick(self):
        self.errors = []
        discovery_ok = True
        try:
            self.refresh_repositories()
        except APIError as error:
            # Local lifetime enforcement must continue during discovery outages.
            self.errors.append(str(error))
            discovery_ok = False
        registrations = {}
        # Include busy runners in repos that have since been excluded from discovery.
        names = {repo["full_name"] for repo in self.repos}
        for file in (self.state / "provisioning").glob("*.json"):
            names.add(json.loads(file.read_text())["repo"])
        for name in names:
            try:
                registrations[name] = {r["name"]: r for r in self.api.pages(f"/repos/{name}/actions/runners", "runners", cached=True)}
            except APIError as error:
                self.errors.append(str(error))
                self.cooldown[name] = time.time() + error.retry_after
        active, idle = self.reconcile(registrations)
        queued = {}
        for repo in self.repos if discovery_ok else []:
            name = repo["full_name"]
            if name not in registrations or time.time() < self.cooldown.get(name, 0):
                continue
            try:
                queued[name] = len(self.queue(repo))
            except APIError as error:
                self.errors.append(str(error))
                self.cooldown[name] = time.time() + error.retry_after
        # Rotate repo order each polling round to avoid starving a fifth repo
        # when four continuously busy repos free slots at the same time.
        order = list(queued)
        if order:
            pivot = int(time.time() // self.poll) % len(order)
            queued = {repo: queued[repo] for repo in order[pivot:] + order[:pivot]}
        draining = (self.state / "drain").exists()
        allocation = [] if draining else plan(queued, idle, active, self.maximum, self.per_repo)
        for repo in allocation:
            try:
                self.provision(repo)
                active[repo] += 1
            except Exception as error:
                self.cooldown[repo] = time.time() + 120
                self.errors.append(f"Provision failed for {repo} ({type(error).__name__})")
                LOG.warning("Provision failed for %s; reserving partial slot until cleanup", repo)
                # Never allocate another slot after a partial create without recounting.
                break
        write_json(self.state / "status.json", {"updated_at": int(time.time()), "owner": self.owner,
                   "max_runners": self.maximum, "active": dict(active), "queued": queued,
                   "idle": dict(idle), "draining": draining, "errors": self.errors})
        cutoff = time.time() - positive("LOG_RETENTION_DAYS", 7) * 86400
        for path in (self.state / "logs").iterdir():
            if path.stat().st_mtime < cutoff:
                if path.is_dir():
                    import shutil
                    shutil.rmtree(path)
                else:
                    path.unlink()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    scaler = Scaler()
    lock = (scaler.state / "controller.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    LOG.info("Controller started: owner=%s maximum=%d", scaler.owner, scaler.maximum)
    while not stop.is_set():
        wait = scaler.poll
        try:
            scaler.tick()
        except Exception as error:
            # Never log response bodies, exception tracebacks, subprocess env or JIT data.
            message = str(error) if isinstance(error, APIError) else type(error).__name__
            LOG.error("Polling failed: %s", message)
            wait = max(wait, getattr(error, "retry_after", 30))
            write_json(scaler.state / "status.json", {"updated_at": int(time.time()), "errors": [message]})
        (scaler.state / "heartbeat").touch()
        stop.wait(wait)
    LOG.info("Controller stopped; existing job containers continue running")


if __name__ == "__main__":
    main()
