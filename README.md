# shorts-bot — automated YouTube Shorts on GitHub Actions

Claude writes a script -> edge-tts voices it (free) -> ffmpeg renders a 1080x1920
video with word-by-word captions -> YouTube Data API uploads it. Runs on a cron in
GitHub Actions. No server.

**Cost: $0.** GitHub Actions is free for public repos, edge-tts is free, the YouTube Data
API is free, and scripts are written by Google's Gemini free tier (AI Studio key, no card).
Free-tier prompts may be used by Google to improve its products, so never put private info in prompts.
Free-tier model availability and limits change; the code tries several models in order.
To use paid Claude instead, set `"llm_provider": "anthropic"` and add `ANTHROPIC_API_KEY`.

## Setup (one time, ~30 min)

1. **Create a GitHub repo** and push this folder. Make sure `.github/workflows/shorts.yml` is included.
2. **Google Cloud** (console.cloud.google.com):
   - New project -> enable **YouTube Data API v3**.
   - OAuth consent screen -> External -> add your own Google account as a test user.
   - **Click "Publish app" (status: In production).** If you leave it in "Testing",
     your refresh token expires after 7 days and the bot silently dies. For personal
     use you do not need Google's verification; you'll just see an "unverified app" warning once.
   - Credentials -> Create OAuth client ID -> **Desktop app** -> download JSON -> save as `client_secret.json` next to `get_refresh_token.py`.
3. On your computer: `pip install google-auth-oauthlib` then `python get_refresh_token.py`. Log in with the Google account that owns your YouTube channel. It prints three values.
4. **GitHub -> Settings -> Secrets and variables -> Actions**, add:
   `GEMINI_API_KEY` (from aistudio.google.com/apikey), `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.
5. **Actions tab -> make-and-upload-short -> Run workflow** with dry_run ON. Download the
   `rendered-video` artifact and check it. Then run once with dry_run OFF.
6. Edit the cron lines in `shorts.yml` (UTC) to set how often it posts.

## Things you must know (read before expecting results)

- **Uploads are private by default** (`"privacy": "private"` in `config.json`). Google
  forces videos uploaded by *unaudited* API projects to private anyway. To get public
  uploads you must pass YouTube's API compliance audit, or publish manually in Studio.
  Check the first uploads to see which applies to you.
- **Quota:** default 10,000 units/day, one upload costs 1,600 -> max ~6 uploads/day.
- **Monetization risk:** YouTube's "inauthentic content" policy targets mass-produced,
  repetitive content. Identical template + synthetic voice + no original value is
  exactly what gets channels demonetized or limited. Mitigate: vary formats, add your
  own footage/clips in `assets/backgrounds/`, keep topics genuinely interesting, and
  review output before turning on public mode. Monetization also needs 1,000 subscribers
  + 10M Shorts views in 90 days (or 4,000 watch hours) before ads pay anything.
- **Accuracy:** the model can state wrong facts confidently. Spot-check, and keep the
  default rule of only well-documented claims. `containsSyntheticMedia` is set so the
  AI-voice disclosure is flagged.
- **Backgrounds:** by default a generated gradient is used (no copyright issues). Drop
  your own or properly licensed `.mp4` clips into `assets/backgrounds/`, and licensed
  `.mp3` music into `assets/music/`. Do not scrape Reddit/TikTok/YouTube clips.
- **GitHub disables schedules after 60 days of repo inactivity.** The workflow commits
  `history.json` after every run, which keeps it alive.
- **Untested here:** I verified captions + rendering locally. The edge-tts voice call,
  the Gemini call, and the YouTube upload need your keys/network and have not been
  run end to end. Expect to fix small things on the first dry run.

## Files
`main.py` pipeline - `config.json` niche/voice/privacy - `history.json` dedupe log -
`get_refresh_token.py` one-time OAuth - `.github/workflows/shorts.yml` schedule.
