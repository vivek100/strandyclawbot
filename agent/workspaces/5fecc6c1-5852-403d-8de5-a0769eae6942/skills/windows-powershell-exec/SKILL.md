# windows-powershell-exec

Purpose: Safely run Windows PowerShell commands for system and project tasks.

When to use:
- User requests shell execution on Windows.
- Command requires object parsing with ConvertFrom-Json or Select-Object.

Steps:
1. Prefer `powershell -NoProfile -Command "..."`.
2. Use native cmdlets (Get-ChildItem, Select-Object, Measure-Object).
3. Return command and concise output.

Examples:
- `powershell -NoProfile -Command "Get-ChildItem -Path . -Recurse -Filter *.py | Measure-Object | Select-Object -ExpandProperty Count"`
- `powershell -NoProfile -Command "$x=(curl.exe -s 'https://wttr.in/?format=j1' | ConvertFrom-Json); $x.current_condition[0].temp_C"`

Constraints:
- Avoid Linux-only tools (`jq`, `grep`, `sed`) unless explicitly requested and installed.
- Avoid destructive commands without confirmation.
