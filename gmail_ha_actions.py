from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CREDENTIALS_PATH = Path(os.environ.get("GMAIL_OAUTH_CLIENT_FILE", ROOT / "gmail_credentials.json"))
TOKEN_PATH = Path(os.environ.get("GMAIL_OAUTH_TOKEN_FILE", ROOT / "gmail_token.json"))
CALLBACK_LOG_PATH = ROOT / "gmail_auth_callback.log"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: "OAuthCallbackServer"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            self.server.auth_code = params.get("code", [None])[0]
        if "error" in params:
            self.server.auth_error = params.get("error", [None])[0]
        with CALLBACK_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(
                json.dumps(
                    {
                        "time": int(time.time()),
                        "path": parsed.path,
                        "has_code": "code" in params and bool(params.get("code", [None])[0]),
                        "error": params.get("error", [None])[0],
                        "param_keys": sorted(params.keys()),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if self.server.auth_code:
            message = (
                "<h2>Gmail authorization finished.</h2>"
                "<p>You can close this browser tab and return to PowerShell.</p>"
            )
        elif self.server.auth_error:
            message = (
                "<h2>Gmail authorization failed.</h2>"
                f"<p>Google returned: {html.escape(self.server.auth_error)}</p>"
            )
        else:
            message = (
                "<h2>Gmail authorization was not completed.</h2>"
                "<p>No authorization code was received. Please restart the setup and use Chrome or Edge.</p>"
            )
        self.wfile.write(
            f"<html><body>{message}</body></html>".encode("utf-8")
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


class OAuthCallbackServer(HTTPServer):
    auth_code: str | None = None
    auth_error: str | None = None


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def api_post(url: str, data: dict[str, Any]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=encoded)
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=70) as response:
        return json.loads(response.read().decode("utf-8"))


def api_get(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=70) as response:
        return json.loads(response.read().decode("utf-8"))


def load_client_config() -> dict[str, Any]:
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(f"Missing Gmail OAuth client file: {CREDENTIALS_PATH}")
    data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    return data.get("installed") or data.get("web") or data


def load_token() -> dict[str, Any] | None:
    if not TOKEN_PATH.exists():
        return None
    return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))


def save_token(token: dict[str, Any]) -> None:
    TOKEN_PATH.write_text(json.dumps(token, indent=2), encoding="utf-8")


def refresh_access_token(client: dict[str, Any], token: dict[str, Any]) -> dict[str, Any]:
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Gmail token has no refresh token. Please authorize Gmail again.")
    payload = {
        "client_id": client["client_id"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if client.get("client_secret"):
        payload["client_secret"] = client["client_secret"]
    try:
        refreshed = api_post(client.get("token_uri", "https://oauth2.googleapis.com/token"), payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400 and "invalid_grant" in body:
            TOKEN_PATH.unlink(missing_ok=True)
            raise RuntimeError(
                "Gmail authorization expired or was revoked. "
                "Please run Setup-Gmail-For-HA-Bot.ps1 again and allow Gmail access."
            ) from exc
        raise
    token.update(refreshed)
    token["expires_at"] = int(time.time()) + int(refreshed.get("expires_in", 3600)) - 60
    save_token(token)
    return token


def authorize_gmail() -> dict[str, Any]:
    client = load_client_config()
    verifier = b64url(secrets.token_bytes(48))
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(16)

    server = OAuthCallbackServer(("127.0.0.1", 0), OAuthCallbackHandler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    params = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = client.get("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth") + "?" + urllib.parse.urlencode(params)
    print("Opening browser for Gmail authorization...")
    print(auth_url)
    webbrowser.open(auth_url)

    deadline = time.time() + int(os.environ.get("GMAIL_AUTH_WAIT_SECONDS", "1800"))
    while time.time() < deadline and not server.auth_code and not server.auth_error:
        time.sleep(0.25)
    server.shutdown()

    if server.auth_error:
        raise RuntimeError(f"Gmail authorization failed: {server.auth_error}")
    if not server.auth_code:
        print("")
        print("Gmail authorization was not received automatically.")
        print("If the browser shows 'Gmail authorization finished', copy the browser address bar URL")
        print("starting with http://127.0.0.1 and paste it here. Otherwise press Enter to stop.")
        try:
            callback_url = input("Callback URL: ").strip()
        except EOFError as exc:
            raise RuntimeError(
                "Gmail authorization was not completed in PowerShell. "
                "Please run Setup-Gmail-For-HA-Bot.ps1 in your own PowerShell window and keep it open "
                "until it prints the Photo action text."
            ) from exc
        if callback_url:
            parsed = urllib.parse.urlparse(callback_url)
            params = urllib.parse.parse_qs(parsed.query)
            server.auth_code = params.get("code", [None])[0]
            server.auth_error = params.get("error", [None])[0]
        if server.auth_error:
            raise RuntimeError(f"Gmail authorization failed: {server.auth_error}")
        if not server.auth_code:
            raise RuntimeError("Gmail authorization timed out after 10 minutes.")

    print("Gmail callback received. Exchanging token...")
    payload = {
        "client_id": client["client_id"],
        "code": server.auth_code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client.get("client_secret"):
        payload["client_secret"] = client["client_secret"]
    token = api_post(client.get("token_uri", "https://oauth2.googleapis.com/token"), payload)
    token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600)) - 60
    save_token(token)
    print(f"Gmail token saved: {TOKEN_PATH}")
    return token


def access_token() -> str:
    client = load_client_config()
    token = load_token()
    if not token:
        token = authorize_gmail()
    elif int(token.get("expires_at", 0)) <= int(time.time()):
        token = refresh_access_token(client, token)
    return str(token["access_token"])


def decode_body(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii")).decode("utf-8", errors="replace")


def message_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if data and mime in {"text/plain", "text/html"}:
            text = decode_body(data)
            if mime == "text/html":
                text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
                text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", text)
                text = html.unescape(text)
            chunks.append(text)
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return "\n".join(chunks)


def gmail_date(date: str) -> str:
    match = re.fullmatch(r"(20\d{2})(\d{2})(\d{2})", date)
    if not match:
        raise ValueError("Date must be YYYYMMDD.")
    return f"{match.group(3)}/{match.group(2)}/{match.group(1)}"


def search_ha_message(date: str) -> dict[str, Any] | None:
    token = access_token()
    ddmmyyyy = gmail_date(date)
    queries = [
        f'subject:"ACE14 - Weekly Safety and Environmental Walk on {ddmmyyyy}" -in:spam -in:trash',
        f'"Weekly Safety and Environmental Walk" "{ddmmyyyy}" -in:spam -in:trash',
    ]
    for query in queries:
        url = "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urllib.parse.urlencode(
            {"q": query, "maxResults": 10}
        )
        result = api_get(url, token)
        for item in result.get("messages", []) or []:
            message_id = item["id"]
            get_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?" + urllib.parse.urlencode(
                {"format": "full"}
            )
            message = api_get(get_url, token)
            headers = {h.get("name", "").lower(): h.get("value", "") for h in message.get("payload", {}).get("headers", [])}
            subject = headers.get("subject", "")
            from_ = headers.get("from", "")
            if subject.lower().startswith("re:"):
                continue
            if "Weekly Safety and Environmental Walk" in subject and "housingauthority.gov.hk" in from_.lower():
                return message
        if result.get("messages"):
            message_id = result["messages"][0]["id"]
            get_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?" + urllib.parse.urlencode(
                {"format": "full"}
            )
            return api_get(get_url, token)
    return None


def extract_issues(body: str) -> list[str]:
    normalized = re.sub(r"\r\n?", "\n", body)
    normalized = re.sub(
        r"\s+(Photo\s*\d+(?:\s*(?:&|and|-|to)\s*\d+)?\)\s*:)",
        r"\n\1",
        normalized,
        flags=re.IGNORECASE,
    )
    issues: list[tuple[int, str]] = []
    current_number: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_number, current_lines
        if current_number is not None:
            issue = " ".join(" ".join(current_lines).split())
            issue = re.split(r"\bBest Regards\b", issue, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if issue:
                issues.append((current_number, issue))
        current_number = None
        current_lines = []

    for raw_line in normalized.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue

        photo_match = re.match(
            r"^Photo\s*(\d+)(?:\s*(?:&|and|-|to)\s*\d+)?\)\s*:\s*(.*)$",
            line,
            re.IGNORECASE,
        )
        if photo_match:
            flush()
            current_number = int(photo_match.group(1))
            current_lines = [photo_match.group(2).strip()]
            continue

        if re.match(r"^Best Regards\b", line, re.IGNORECASE):
            flush()
            break

        if current_number is not None:
            if line.endswith(":") and not re.match(r"^Photo\s*\d+", line, re.IGNORECASE):
                flush()
                continue
            current_lines.append(line)

    flush()
    return [issue for _, issue in sorted(issues)]


def sentence_case(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    return text[0].upper() + text[1:]


def action_from_issue(issue: str) -> str:
    text = issue.strip().rstrip(".")
    lower = text.lower()
    if lower == "the rear of excavator should be provided enough anti-collision rods":
        return "Enough anti-collision rods have been provided at the rear of the excavator."
    if lower == "concrete pipes for gully works should be placed on ground level & provided proper wedges at each sides of bottom":
        return "The concrete pipes for gully works have been placed on ground level and provided with proper wedges at each side of the bottom."
    if "webbing sling" in lower and "broken" in lower:
        return "The broken webbing sling for the lifting appliance has been removed from site."

    replacements = [
        (" should be provided enough ", " has been provided with enough "),
        (" should be provided beside ", " has been provided beside "),
        (" should be provided between ", " has been provided between "),
        (" should be provided ", " has been provided "),
        (" should be placed on ", " has been placed on "),
        (" should be placed ", " has been placed "),
        (" should be cleared during ", " has been cleared during "),
        (" should be adjusted to ", " has been adjusted to "),
        (" should be properly fixed on ", " has been properly fixed on "),
        (" should be properly covered with ", " has been properly covered with "),
        (" should be covered with ", " has been covered with "),
        (" should be removed from site", " has been removed from site"),
        (" should be removed ", " has been removed "),
    ]
    converted = text
    for old, new in replacements:
        converted = re.sub(re.escape(old), new, converted, flags=re.IGNORECASE)
    converted = re.sub(r"\beach sides\b", "each side", converted, flags=re.IGNORECASE)
    converted = re.sub(r"\bat each side of bottom\b", "at each side of the bottom", converted, flags=re.IGNORECASE)
    converted = re.sub(r"\bon fencing\b", "on the fencing", converted, flags=re.IGNORECASE)
    converted = re.sub(r"\bfor working access\b", "for the working access", converted, flags=re.IGNORECASE)
    converted = re.sub(r"\bequipment of gas welding and flame cutting operations\b", "equipment for gas welding and flame cutting operations", converted, flags=re.IGNORECASE)
    return sentence_case(converted).rstrip(".") + "."


def fetch_action_texts(date: str) -> list[str]:
    message = search_ha_message(date)
    if not message:
        raise RuntimeError(f"No HA Weekly Safety and Environmental Walk email found for {gmail_date(date)}.")
    body = message_text(message.get("payload", {}))
    issues = extract_issues(body)
    if not issues:
        raise RuntimeError(f"No Photo issue text found in HA email for {gmail_date(date)}.")
    return [action_from_issue(issue) for issue in issues]


def fetch_ha_details(date: str) -> dict[str, Any]:
    message = search_ha_message(date)
    if not message:
        raise RuntimeError(f"No HA Weekly Safety and Environmental Walk email found for {gmail_date(date)}.")
    body = message_text(message.get("payload", {}))
    issues = extract_issues(body)
    if not issues:
        raise RuntimeError(f"No Photo issue text found in HA email for {gmail_date(date)}.")

    normalized = re.sub(r"\r\n?", "\n", body)
    normalized = re.sub(
        r"\s+(Photo\s*\d+(?:\s*(?:&|and|-|to)\s*\d+)?\)\s*:)",
        r"\n\1",
        normalized,
        flags=re.IGNORECASE,
    )
    current_location = ""
    locations: list[str] = []
    for raw_line in normalized.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        if line.endswith(":") and not re.match(r"^Photo\s*\d+", line, re.IGNORECASE):
            current_location = line.rstrip(":")
            continue
        if re.match(r"^Photo\s*\d+(?:\s*(?:&|and|-|to)\s*\d+)?\)\s*:", line, re.IGNORECASE):
            locations.append(current_location)

    time_match = re.search(
        r"carried out on\s+\d{2}/\d{2}/\d{4}\s+at\s+([0-9:]+\s*(?:A\.?M\.?|P\.?M\.?)?)",
        body,
        re.IGNORECASE,
    )
    walk_time = time_match.group(1).strip() if time_match else "09:30 A.M."
    unique_locations: list[str] = []
    for location in locations:
        base = re.sub(r"\s*\(Section\s+\d+\)\s*", "", location, flags=re.IGNORECASE).strip()
        if base and base not in unique_locations:
            unique_locations.append(base)

    return {
        "issues": issues,
        "actions": [action_from_issue(issue) for issue in issues],
        "locations": locations[: len(issues)],
        "location_summary": ", ".join(unique_locations),
        "time": walk_time,
    }


def write_actions_file(date: str, job_dir: Path) -> list[str]:
    actions = fetch_action_texts(date)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "actions.json").write_text(json.dumps({"actions": actions}, indent=2, ensure_ascii=False), encoding="utf-8")
    return actions


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python gmail_ha_actions.py YYYYMMDD")
    actions = fetch_action_texts(sys.argv[1])
    for index, action in enumerate(actions, start=1):
        print(f"Photo {index}: {action}")
