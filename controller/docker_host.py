"""Docker CLI wrapper. Only the controller can access the host daemon."""
import json
import os
import subprocess


PREFIX = "personal-ci"


class DockerError(RuntimeError):
    pass


class Docker:
    def call(self, *args, env=None, missing_ok=False, timeout=120, raw=False):
        result = subprocess.run(["docker", *args], capture_output=True, timeout=timeout,
                                env={**os.environ, **(env or {})})
        if result.returncode and not missing_ok:
            # Never report full commands, env or Docker stderr: they can contain JIT credentials.
            raise DockerError(f"Docker {args[0]} failed (exit {result.returncode})")
        output = result.stdout + result.stderr if args[0] == "logs" else result.stdout
        return output if raw else output.decode(errors="replace").strip()

    def objects(self, kind, pool):
        if kind == "container":
            ids = self.call("ps", "-aq", "--filter", f"label={PREFIX}.pool={pool}").split()
            return json.loads(self.call("inspect", *ids)) if ids else []
        ids = self.call(kind, "ls", "-q", "--filter", f"label={PREFIX}.pool={pool}").split()
        return json.loads(self.call(kind, "inspect", *ids)) if ids else []

    def labels(self, labels):
        return [part for key, value in labels.items() for part in ("--label", f"{PREFIX}.{key}={value}")]

    def remove_slot(self, pool, slot):
        # The runner shares the daemon's network namespace, so it is removed first.
        for name in (slot, slot + "-init", slot + "-docker"):
            self.call("rm", "-fv", name, missing_ok=True)
        for suffix in ("work", "externals", "tools"):
            self.call("volume", "rm", "-f", f"{slot}-{suffix}", missing_ok=True)
        self.call("network", "rm", f"{slot}-net", missing_ok=True)
