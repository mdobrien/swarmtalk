"""Thin Mattermost REST v4 client. stdlib only."""
import json
import os
import mimetypes
import secrets
import urllib.error
import urllib.parse
import urllib.request

# MM_URL lets the test suite target its own throwaway server; default is the real one
BASE_URL = os.environ.get("MM_URL", "http://localhost:8065")


class ApiError(Exception):
    def __init__(self, status: int, body: dict | str, path: str):
        self.status, self.body, self.path = status, body, path
        msg = body.get("message") if isinstance(body, dict) else str(body)
        super().__init__(f"{status} {path}: {msg}")


class Api:
    def __init__(self, token: str | None = None, base_url: str = BASE_URL):
        self.token = token
        self.base = base_url.rstrip("/") + "/api/v4"

    def request(self, method: str, path: str, body=None, params: dict | None = None,
                raw_body: bytes | None = None, content_type: str = "application/json",
                parse: bool = True):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", content_type)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                if not parse:
                    return raw, dict(r.headers)
                return json.loads(raw) if raw else None, dict(r.headers)
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw
            raise ApiError(e.code, parsed, path) from None

    def get(self, path, params=None):
        return self.request("GET", path, params=params)[0]

    def post(self, path, body=None):
        return self.request("POST", path, body)[0]

    def put(self, path, body=None):
        return self.request("PUT", path, body)[0]

    def delete(self, path):
        return self.request("DELETE", path)[0]

    def get_raw(self, path) -> bytes:
        return self.request("GET", path, parse=False)[0]

    def upload(self, channel_id: str, filename: str, content: bytes) -> dict:
        """Multipart POST /files; returns the file info. Attach its id to a post via file_ids."""
        boundary = secrets.token_hex(16)
        # quoted-string: backslash-escape \ and ", drop CR/LF (else the server silently drops the part)
        safe = filename.replace("\r", "").replace("\n", "").replace("\\", "\\\\").replace('"', '\\"')
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"channel_id\"\r\n\r\n{channel_id}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{safe}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        res = self.request("POST", "/files", raw_body=body,
                           content_type=f"multipart/form-data; boundary={boundary}")[0]
        if not res or not res.get("file_infos"):
            raise ApiError(400, f"server rejected upload of {filename!r}", "/files")
        return res["file_infos"][0]

    def ping(self) -> bool:
        try:
            return self.get("/system/ping").get("status") == "OK"
        except (ApiError, OSError):
            return False
