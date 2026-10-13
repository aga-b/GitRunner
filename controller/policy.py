"""Pure scheduling and run-selection policy, independently testable."""
from collections import Counter, deque


def eligible_repository(repo, owner, includes, excludes):
    name = repo["full_name"]
    return (repo["owner"]["login"].lower() == owner.lower()
            and not repo.get("archived") and not repo.get("disabled")
            and (not includes or name.lower() in includes)
            and name.lower() not in excludes)


def trusted_run(run, repo, actors):
    if run.get("actor", {}).get("login", "").lower() not in actors:
        return False
    if run.get("event") not in {"push", "pull_request", "workflow_dispatch", "schedule"}:
        return False
    if run["event"] == "pull_request":
        prs = run.get("pull_requests", [])
        # Do not provision for fork PRs or ambiguous PR metadata.
        if not prs or any(((pr.get("head") or {}).get("repo") or {}).get("id") != repo["id"] for pr in prs):
            return False
    return True


def accepts_job(job, labels, required_label):
    requested = {label.lower() for label in job.get("labels", [])}
    return (job.get("status") == "queued" and required_label in requested
            and requested <= labels)


def plan(queued, idle, active, maximum, per_repo):
    """Fair, one-at-a-time allocation across repos under a global job limit.

    queued and idle are repo->counts; active includes provisioning and busy jobs.
    Idle registrations are reservations, so eventual API consistency cannot cause
    the same demand to spawn a fresh runner on every poll.
    """
    slots = max(0, maximum - sum(active.values()))
    demand = {repo: max(0, count - idle.get(repo, 0)) for repo, count in queued.items()}
    ring = deque(repo for repo, count in demand.items() if count)
    allocated = Counter()
    result = []
    while slots and ring:
        repo = ring.popleft()
        if active.get(repo, 0) + allocated[repo] >= per_repo:
            continue
        result.append(repo)
        allocated[repo] += 1
        slots -= 1
        demand[repo] -= 1
        if demand[repo]:
            ring.append(repo)
    return result
