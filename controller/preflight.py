import os
from pathlib import Path
from docker_host import Docker
from github_api import GitHub
from policy import eligible_repository
from main import csv, positive

api, docker = GitHub(), Docker()
owner = os.environ['GITHUB_OWNER']
repos = [r for r in api.repositories() if eligible_repository(r, owner, csv('INCLUDE_REPOS'), csv('EXCLUDE_REPOS'))]
if not repos:
    raise SystemExit('No owned repositories are visible to the GitHub App')
for repo in repos:
    name = repo['full_name']
    api.request('GET', f'/repos/{name}/actions/runners')
    api.request('GET', f'/repos/{name}/actions/runs?per_page=1')
    print('API access OK:', name)
docker.call('image', 'inspect', os.environ['RUNNER_IMAGE'])
docker.call('image', 'inspect', os.environ['DIND_IMAGE'])
info = docker.call('info', '--format', '{{json .}}')
import json
info = json.loads(info)
if info.get('OSType') != 'linux' or info.get('CgroupVersion') != '2':
    raise SystemExit('A Linux Docker host with cgroup v2 is required')
maximum = positive('MAX_RUNNERS', 2)
def memory(value):
    value = value.lower()
    if value.endswith('g'): return float(value[:-1])*1024**3
    if value.endswith('m'): return float(value[:-1])*1024**2
    return float(value)
required = maximum*(memory(os.environ.get('RUNNER_MEMORY','4g')) + memory(os.environ.get('DIND_MEMORY','2g'))) + 2*1024**3
if info['MemTotal'] < required:
    raise SystemExit(f'Host RAM is too small for configured limits: need at least {required/1024**3:.1f} GiB; lower MAX_RUNNERS or memory limits')
print('Images and host capacity OK; running disposable Docker/service smoke test next.')
from smoke import main
main()
