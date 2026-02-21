# windows-cmd-curl

Purpose: Fetch live data on Windows using cmd + curl and parse with PowerShell.

When to use:
- User requests web/API data fetch.
- Need simple HTTP calls from shell.

Steps:
1. Fetch raw JSON with `cmd /c curl -s <url>` or `curl.exe -s <url>`.
2. Parse JSON via PowerShell ConvertFrom-Json when field extraction is needed.
3. Surface key facts in concise bullets.

Examples:
- `cmd /c curl -s https://hacker-news.firebaseio.com/v0/topstories.json`
- `powershell -NoProfile -Command "$ids=(curl.exe -s 'https://hacker-news.firebaseio.com/v0/topstories.json' | ConvertFrom-Json); $ids[0]"`

Constraints:
- Prefer `curl.exe` on Windows to avoid alias ambiguity.
- Validate URLs and keep timeouts reasonable.
