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
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
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
        self.poll = positive("POLL_SECONDS", 10)
        self.poll_idle = positive("POLL_IDLE_SECONDS", 20)
        self.sweep = positive("SWEEP_SECONDS", 120)
        self.parallelism = positive("PARALLELISM", 8)
        self.min_free_mb = positive("MIN_FREE_MEMORY_MB", 4096)
        self.job_reserve_mb = positive("JOB_MEMORY_RESERVE_MB", 3072)
        self.mirror = os.environ.get("DOCKER_MIRROR", "").strip()
        self.mirror_network = os.environ.get("DOCKER_MIRROR_NETWORK", "personal-ci-mirror").strip()
        self.playwright_volume = os.environ.get("PLAYWRIGHT_CACHE_VOLUME", "personal-ci-playwright").strip()
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
        self.executor = ThreadPoolExecutor(max_workers=self.parallelism, thread_name_prefix="ci")
        self.retiring = {}
        self.lock = threading.Lock()
        self.next_sweep = 0
        self.provision_times = deque(maxlen=50)
        self.hot = False
        self.metrics = {}
        self.mem_total_mb = self.mem_used_mb = None

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
        started = time.time()
        now = int(started)
        slot = f"{self.pool}-{now}-{uuid.uuid4().hex[:8]}"
        data = {"repo": repo, "born": now, "runner_id": None, "slot": slot}
        self.record(slot, data)
        try:
            self._provision(repo, slot, now, data)
        except BaseException:
            # A half-built slot must not keep a repo's capacity reserved until the idle timeout.
            self.cleanup_failed(repo, slot, data.get("runner_id"))
            raise
        elapsed = time.time() - started
        with self.lock:
            self.provision_times.append(elapsed)
        LOG.info("Provisioned %s for %s in %.1fs", slot, repo, elapsed)
        return elapsed

    def cleanup_failed(self, repo, slot, runner_id):
        try:
            if runner_id:
                try:
                    self.api.request("DELETE", f"/repos/{repo}/actions/runners/{runner_id}")
                except APIError as error:
                    if error.status != 404:
                        raise
            self.docker.remove_slot(self.pool, slot)
            (self.state / "provisioning" / f"{slot}.json").unlink(missing_ok=True)
        except Exception as error:
            LOG.warning("Cleanup of failed slot %s incomplete (%s); reconcile will retry", slot, type(error).__name__)

    def _provision(self, repo, slot, now, data):
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
        daemon = ["dockerd", "--host=unix:///var/run/docker.sock",
                  "--host=tcp://127.0.0.1:2375", "--tls=false"]
        if self.mirror:
            daemon += [f"--registry-mirror={self.mirror}",
                       f"--insecure-registry={self.mirror.split('://', 1)[-1]}"]
        dind = self.docker.call("run", "-d", "--name", slot + "-docker", "--network", network,
                               "--privileged", "--memory", os.environ.get("DIND_MEMORY", "2g"),
                               "--memory-swap", os.environ.get("DIND_MEMORY", "2g"),
                               "--cpus", os.environ.get("DIND_CPUS", "1"), "--pids-limit", "2048",
                               "--log-opt", "max-size=10m", "--log-opt", "max-file=2",
                               "--env", "DOCKER_TLS_CERTDIR=", *mounts,
                               *self.docker.labels({**labels, "role": "dind"}),
                               self.dind_image, *daemon)
        if self.mirror and self.mirror_network:
            self.docker.call("network", "connect", self.mirror_network, dind)
        deadline = time.time() + 40
        ready = False
        while time.time() < deadline:
            try:
                self.docker.call("exec", dind, "docker", "version", "--format", "{{.Server.Version}}", timeout=5)
                ready = True
                break
            except Exception:
                time.sleep(0.25)
        if not ready:
            raise RuntimeError("Per-job Docker daemon did not become ready")
        jit = self.api.request("POST", f"/repos/{repo}/actions/runners/generate-jitconfig",
                               {"name": slot, "runner_group_id": 1, "labels": sorted(self.labels),
                                "work_folder": "_work"})
        data["runner_id"] = jit["runner"]["id"]
        self.record(slot, data)
        # Playwright locks and validates its browser cache, so concurrent jobs can share it safely.
        shared = (["--mount", f"type=volume,source={self.playwright_volume},target=/home/runner/.cache/ms-playwright"]
                  if self.playwright_volume else [])
        self.docker.call("run", "-d", "--name", slot, "--network", f"container:{dind}",
                         "--memory", os.environ.get("RUNNER_MEMORY", "4g"),
                         "--memory-swap", os.environ.get("RUNNER_MEMORY", "4g"),
                         "--cpus", os.environ.get("RUNNER_CPUS", "2"), "--pids-limit", "2048",
                         "--shm-size", "1g", "--init",
                         "--log-opt", "max-size=10m", "--log-opt", "max-file=2",
                         "--env", "RUNNER_JIT_CONFIG", "--env", "DOCKER_HOST=tcp://127.0.0.1:2375",
                         "--env", "RUNNER_NAME=" + slot, *mounts, *shared,
                         *self.docker.labels({**labels, "role": "runner", "runner-id": str(data["runner_id"])}),
                         self.runner_image, env={"RUNNER_JIT_CONFIG": jit["encoded_jit_config"]})

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

    def retire(self, slot, repo, runner_id=None, containers=None):
        if containers is None:
            containers = self.docker.objects("container", self.pool)
        for item in containers:
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

    def retire_later(self, slot, repo, runner_id, containers):
        def job():
            try:
                self.retire(slot, repo, runner_id, containers)
            except Exception as error:
                self.errors.append(f"Retire failed: {slot} ({type(error).__name__})")
            finally:
                self.retiring.pop(slot, None)
        self.retiring[slot] = self.executor.submit(job)

    def wait_retired(self, timeout=120):
        for future in list(self.retiring.values()):
            future.result(timeout=timeout)

    def reconcile(self, registrations, containers=None, background=False):
        now = time.time()
        if containers is None:
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
            if slot in self.retiring:
                local_names.discard(slot)
                continue
            if finished or expired or orphan:
                try:
                    if background:
                        self.retire_later(slot, repo, data.get("runner_id"), containers)
                    else:
                        self.retire(slot, repo, data.get("runner_id"), containers)
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
        # A workflow can be `pending` while its matching job is already queued,
        # including when it is waiting for a self-hosted runner to come online.
        for status in ("queued", "pending", "in_progress"):
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

    UNITS = {"b": 1, "kb": 1e3, "kib": 1024, "mb": 1e6, "mib": 1024 ** 2, "gb": 1e9, "gib": 1024 ** 3}

    def memory_available_mb(self):
        """Host total minus live container usage; page cache is reclaimable and ignored."""
        if self.mem_total_mb is None or self.mem_used_mb is None:
            return None
        return int(self.mem_total_mb - self.mem_used_mb)

    def sample_memory(self):
        total = int(self.docker.call("info", "--format", "{{.MemTotal}}"))
        self.mem_total_mb = total // 1048576
        output = self.docker.call("stats", "--no-stream", "--format", "{{.MemUsage}}", timeout=30)
        used = 0.0
        for line in output.splitlines():
            match = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", line)
            if match:
                used += float(match[1]) * self.UNITS.get(match[2].lower(), 1)
        self.mem_used_mb = used / 1048576

    def start_memory_monitor(self, stop):
        def loop():
            while not stop.is_set():
                try:
                    self.sample_memory()
                except Exception as error:
                    self.mem_used_mb = None
                    LOG.warning("Memory sampling failed (%s); admission control disabled", type(error).__name__)
                stop.wait(10)
        threading.Thread(target=loop, name="memory", daemon=True).start()

    def memory_slots(self, idle_total):
        """New runners the host can take; idle runners are reserved but not yet using memory."""
        available = self.memory_available_mb()
        if available is None:
            return None
        return max(0, (available - self.min_free_mb - idle_total * self.job_reserve_mb) // self.job_reserve_mb)

    def fetch_parallel(self, function, items):
        futures = {item: self.executor.submit(function, item) for item in items}
        results = {}
        for item, future in futures.items():
            try:
                results[item] = future.result()
            except APIError as error:
                self.errors.append(str(error))
                self.cooldown[item if isinstance(item, str) else item["full_name"]] = time.time() + error.retry_after
        return results

    def tick(self):
        started = time.time()
        self.errors = []
        discovery_ok = True
        try:
            self.refresh_repositories()
        except APIError as error:
            # Local lifetime enforcement must continue during discovery outages.
            self.errors.append(str(error))
            discovery_ok = False
        containers = self.docker.objects("container", self.pool)
        local = {item["Config"]["Labels"].get(f"{PREFIX}.repo") for item in containers}
        for file in (self.state / "provisioning").glob("*.json"):
            local.add(json.loads(file.read_text())["repo"])
        # Runner lists are only needed where we own runners; sweep every repo occasionally
        # so leaked JIT registrations are still cleaned up.
        if started >= self.next_sweep:
            names = {repo["full_name"] for repo in self.repos} | local
            self.next_sweep = started + self.sweep
        else:
            names = {name for name in local if name}
        names = {name for name in names if time.time() >= self.cooldown.get(name, 0)}
        listed = self.fetch_parallel(
            lambda name: {r["name"]: r for r in self.api.pages(f"/repos/{name}/actions/runners", "runners", cached=True)},
            sorted(names))
        registrations = {name: listed[name] for name in listed}
        active, idle = self.reconcile(registrations, containers, background=True)
        queued = {}
        oldest = 0.0
        if discovery_ok:
            repos = [repo for repo in self.repos if time.time() >= self.cooldown.get(repo["full_name"], 0)]
            jobs = {}
            futures = {repo["full_name"]: self.executor.submit(self.queue, repo) for repo in repos}
            for name, future in futures.items():
                try:
                    jobs[name] = future.result()
                except APIError as error:
                    self.errors.append(str(error))
                    self.cooldown[name] = time.time() + error.retry_after
            queued = {name: len(found) for name, found in jobs.items()}
            created = [job.get("created_at") for found in jobs.values() for job in found if job.get("created_at")]
            if created:
                oldest = max(0.0, time.time() - min(time.mktime(time.strptime(c, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
                                                     for c in created))
        # Rotate repo order each polling round to avoid starving a repo
        # when several continuously busy repos free slots at the same time.
        order = list(queued)
        if order:
            pivot = int(time.time() // self.poll) % len(order)
            queued = {repo: queued[repo] for repo in order[pivot:] + order[:pivot]}
        draining = (self.state / "drain").exists()
        headroom = self.memory_slots(sum(idle.values()))
        maximum = self.maximum if headroom is None else min(self.maximum, sum(active.values()) + headroom)
        allocation = [] if draining else plan(queued, idle, active, maximum, self.per_repo)
        provision_started = time.time()
        results = {}
        for repo in allocation:
            results.setdefault(repo, []).append(self.executor.submit(self.provision, repo))
        for repo, futures in results.items():
            for future in futures:
                try:
                    future.result()
                    active[repo] += 1
                except Exception as error:
                    detail = str(error) if isinstance(error, (APIError, RuntimeError)) else type(error).__name__
                    self.cooldown[repo] = time.time() + 30
                    self.errors.append(f"Provision failed for {repo} ({detail})")
                    LOG.warning("Provision failed for %s (%s); slot cleaned up, retrying in 30s", repo, detail)
        self.hot = bool(allocation) or sum(active.values()) > 0 or any(queued.values())
        with self.lock:
            times = list(self.provision_times)
        self.metrics = {
            "tick_seconds": round(time.time() - started, 2),
            "provision_seconds_avg": round(sum(times) / len(times), 2) if times else None,
            "provision_seconds_max": round(max(times), 2) if times else None,
            "oldest_queued_seconds": round(oldest, 1),
            "memory_available_mb": self.memory_available_mb(),
            "admission_limit": maximum,
            "provisioned_this_tick": len(allocation),
            "provision_batch_seconds": round(time.time() - provision_started, 2) if allocation else 0,
        }
        write_json(self.state / "status.json", {"updated_at": int(time.time()), "owner": self.owner,
                   "max_runners": self.maximum, "active": dict(active), "queued": queued,
                   "idle": dict(idle), "draining": draining, "metrics": self.metrics,
                   "errors": self.errors})
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
    scaler.start_memory_monitor(stop)
    while not stop.is_set():
        wait = scaler.poll if scaler.hot else scaler.poll_idle
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
    scaler.wait_retired()
    LOG.info("Controller stopped; existing job containers continue running")


if __name__ == "__main__":
    main()
