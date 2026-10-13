"""Real Docker integration probe, no GitHub runner registration or workflow."""
import os
import time
import uuid
from docker_host import Docker


def main():
    docker= Docker()
    slot='ci-preflight-'+uuid.uuid4().hex[:8]
    labels={'pool':slot, 'slot':slot}
    network=slot+'-net'
    try:
        docker.call('network','create',*docker.labels(labels),network)
        daemon=docker.call('run','-d','--name',slot+'-docker','--network',network,
                          '--privileged','--memory','2g','--memory-swap','2g','--cpus','1',
                          '--env','DOCKER_TLS_CERTDIR=',*docker.labels(labels),
                          os.environ['DIND_IMAGE'],'dockerd','--host=unix:///var/run/docker.sock',
                          '--host=tcp://127.0.0.1:2375','--tls=false')
        ready=False
        for _ in range(30):
            try:
                docker.call('exec',daemon,'docker','info',timeout=5)
                ready=True
                break
            except Exception: time.sleep(1)
        if not ready: raise RuntimeError('Docker-in-Docker startup failed')
        # Probe an actual PostgreSQL service reachable through runner localhost,
        # Docker CLI/compose/buildx and runner's setup-node tool-cache path.
        command='''set -euo pipefail
docker compose version
docker buildx version
test -w /opt/hostedtoolcache
sudo -n true
command -v apt-get
psql --version
docker run -d --name pg -e POSTGRES_PASSWORD=ci-test-only -p 5432:5432 postgres:17-alpine
ready=0
for i in {1..60}; do
  if PGPASSWORD=ci-test-only psql -h 127.0.0.1 -U postgres -c 'SELECT 1' >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  docker logs --tail 80 pg
  echo 'PostgreSQL never became ready over runner localhost' >&2
  exit 1
fi
python3 -c "import socket; s=socket.create_connection(('127.0.0.1',5432),timeout=10); s.close()"
docker exec pg pg_isready -h 127.0.0.1 -U postgres
PGPASSWORD=ci-test-only psql -h 127.0.0.1 -U postgres -c 'SELECT 1'
echo 'Docker and localhost PostgreSQL smoke test passed'
'''
        probe=docker.call('run','-d','--name',slot+'-probe','--network','container:'+daemon,
                          '--env','DOCKER_HOST=tcp://127.0.0.1:2375','--entrypoint','bash',
                          *docker.labels(labels), os.environ['RUNNER_IMAGE'],'-c',command)
        exit_code=docker.call('wait',probe,timeout=300)
        # This fixed probe has no App credentials or JIT configuration.
        print(docker.call('logs','--tail','200',probe), flush=True)
        if exit_code != '0':
            raise RuntimeError(f'Docker/PostgreSQL probe failed (exit {exit_code})')
    finally:
        docker.remove_slot(slot,slot)


if __name__ == '__main__': main()
