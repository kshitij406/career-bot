"""MANUAL, one-time: obtain a Gmail OAuth refresh token for career-bot.

Never run by src/run.py or the CI workflow — same rule as src/tailor.py.

Prerequisite: a Google Cloud project with the Gmail API enabled and an
OAuth client of type "Desktop app" (console.cloud.google.com -> APIs &
Services -> Credentials). Copy its client ID/secret, then run:

    GMAIL_CLIENT_ID=... GMAIL_CLIENT_SECRET=... python -m src.gmail_auth_setup

This opens a browser for consent (read-only gmail.readonly scope), then
prints a refresh token to save as the GMAIL_REFRESH_TOKEN secret.
"""

import http.server
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{PORT}"


class _CodeHandler(http.server.BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CodeHandler.code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Auth complete, you can close this tab.")

    def log_message(self, *args):
        pass


def main():
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    if not (client_id and client_secret):
        print("Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET first.", file=sys.stderr)
        sys.exit(1)

    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    print(f"Opening browser for consent:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server = http.server.HTTPServer(("127.0.0.1", PORT), _CodeHandler)
    server.handle_request()  # blocks until the redirect lands, then stops

    if not _CodeHandler.code:
        print("No code received.", file=sys.stderr)
        sys.exit(1)

    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": _CodeHandler.code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        }
    ).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        tokens = json.loads(resp.read().decode())

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print(
            "No refresh_token in response — revoke prior access at "
            "https://myaccount.google.com/permissions and re-run.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\nSave this as the GMAIL_REFRESH_TOKEN secret:\n")
    print(refresh_token)


if __name__ == "__main__":
    main()
