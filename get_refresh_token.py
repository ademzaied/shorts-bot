"""Run ONCE on your own computer to get a YouTube refresh token.

1. Google Cloud Console -> create project -> enable "YouTube Data API v3".
2. OAuth consent screen: External, add yourself as test user, then click
   "PUBLISH APP" (see README, otherwise the token dies after 7 days).
3. Credentials -> Create OAuth client ID -> Desktop app -> download JSON,
   save it here as client_secret.json.
4. pip install google-auth-oauthlib && python get_refresh_token.py
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\n=== Put these in GitHub -> Settings -> Secrets -> Actions ===")
print("YT_CLIENT_ID     =", creds.client_id)
print("YT_CLIENT_SECRET =", creds.client_secret)
print("YT_REFRESH_TOKEN =", creds.refresh_token)
