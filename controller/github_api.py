"""Small GitHub REST client. Credentials stay in the controller."""
import base64
import http.client
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class APIError(RuntimeError):
    def __init__(self, status, path, retry_after=30):
        super().__init__(f"GitHub HTTP {status}: {path.split('?')[0]}")
        self.status = status
        self.retry_after = retry_after


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


class GitHub:
    def __init__(self):
        self.app_id = os.environ.get("GITHUB_APP_ID", "")
        self.installation_id = os.environ.get("GITHUB_INSTALLATION_ID", "")
        self.key_file = os.environ.get("GITHUB_APP_KEY_FILE", "/run/secrets/github-app.pem")
        self.pat_file = os.environ.get("GITHUB_TOKEN_FILE", "")
        if not self.pat_file and not (self.app_id and self.installation_id):
            raise ValueError("Configure a GitHub App or GITHUB_TOKEN_FILE")
        self.token = ""
        self.token_until = 0
        self.cache = {}
        self.auth_lock = threading.Lock()

    def jwt(self):
        now = int(time.time())
        header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": self.app_id}).encode())
        message = header + b"." + payload
        result = subprocess.run(["openssl", "dgst", "-sha256", "-sign", self.key_file],
                                input=message, capture_output=True, check=True)
        return (message + b"." + b64(result.stdout)).decode()

    def auth(self):
        if self.pat_file:
            return Path(self.pat_file).read_text().strip()
        with self.auth_lock:
            if time.time() >= self.token_until:
                value = self.request("POST", f"/app/installations/{self.installation_id}/access_tokens",
                                     body={}, token=self.jwt())
                self.token = value["token"]
                self.token_until = time.time() + 3000
            return self.token

    def request(self, method, path, body=None, token=None, cached=False):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Only relative GitHub API paths are allowed")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "personal-ci-autoscaler/1",
                   "X-GitHub-Api-Version": "2026-03-10", "Authorization": "Bearer " + (token or self.auth())}
        if cached and path in self.cache:
            headers["If-None-Match"] = self.cache[path][0]
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request("https://api.github.com" + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                value = json.loads(raw) if raw else None
                if cached and response.headers.get("ETag"):
                    # Bound cache growth when workflow runs change over time.
                    if len(self.cache) > 2000:
                        self.cache.clear()
                    self.cache[path] = (response.headers["ETag"], value)
                return value
        except urllib.error.HTTPError as error:
            if error.code == 304 and path in self.cache:
                return self.cache[path][1]
            if error.code == 401 and token is None and not self.pat_file:
                self.token_until = 0
            retry = max(30, int(error.headers.get("Retry-After", "30")))
            if error.headers.get("X-RateLimit-Remaining") == "0":
                retry = max(retry, int(error.headers.get("X-RateLimit-Reset", "0")) - int(time.time()) + 5)
            raise APIError(error.code, path, retry) from None
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, OSError):
            # Hide connection details and preserve the same outage policy as HTTP errors.
            raise APIError(0, path) from None

    def pages(self, path, key=None, cached=False):
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            data = self.request("GET", f"{path}{separator}per_page=100&page={page}", cached=cached)
            items = data[key] if key else data
            yield from items
            if len(items) < 100:
                return
            page += 1

    def repositories(self):
        if self.pat_file:
            return list(self.pages("/user/repos?affiliation=owner", cached=True))
        return list(self.pages("/installation/repositories", "repositories", cached=True))
