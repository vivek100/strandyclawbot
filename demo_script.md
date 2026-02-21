# OpenClaw-Style Demo Script (Windows Personal Assistant Flow)

## Goal
Show a realistic assistant flow:
1. Agent attempts a live-data morning brief.
2. It fails on a Linux-style parsing assumption.
3. It self-corrects with Windows-friendly commands.
4. It saves learning to markdown + Neo4j memory.
5. It creates a reusable local skill.
6. Next run is immediate and correct.

## Demo Setup
- Run backend/frontend as usual.
- Use the same conversation thread so memory/skills stay in session context.
- Ensure internet access for public APIs.

## Step 0: Prime Workspace
User prompt:
```text
Provision my workspace for this session and show me available skills.
```

Expected agent behavior:
- Calls `provision_workspace`.
- Calls `list_skills`.
- Confirms workspace path and current skills.

## Step 1: Ask for Personal Assistant Task
User prompt:
```text
Build my morning brief using live data: current time, weather, and one top tech headline. Keep it concise.
```

Expected behavior:
- Calls time tool.
- Uses shell command(s) with `curl`.
- May initially attempt Linux-style parsing (acceptable for demo).

## Step 2: Force Windows Recovery
If first attempt uses Linux-only parsing (for example `jq`), prompt:
```text
You're on Windows. Fix this using cmd/PowerShell-friendly commands only.
```

Expected corrected command style:
```powershell
powershell -NoProfile -Command "$w=(curl.exe -s 'https://wttr.in/?format=j1' | ConvertFrom-Json); $w.current_condition[0].temp_C"
```

Possible headline fetch (raw):
```bat
cmd /c curl -s https://hacker-news.firebaseio.com/v0/topstories.json
```

PowerShell parse example:
```powershell
powershell -NoProfile -Command "$ids=(curl.exe -s 'https://hacker-news.firebaseio.com/v0/topstories.json' | ConvertFrom-Json); $id=$ids[0]; curl.exe -s \"https://hacker-news.firebaseio.com/v0/item/$id.json\""
```

## Step 3: Save Durable Preference (Memory)
User prompt:
```text
Remember this preference: on my machine always use PowerShell/cmd-friendly commands and avoid Linux-only parsers like jq.
Save this as durable memory.
```

Expected behavior:
- Calls `save_memory` with category `preference` or `system`.
- Optionally calls `remember_note` to append markdown memory.

## Step 4: Create Reusable Skill
User prompt:
```text
Create a local skill named morning-brief-windows with SKILL.md for this workflow:
- get time
- get weather with curl
- get one top tech headline
- use PowerShell ConvertFrom-Json for parsing
- output concise brief
```

Expected behavior:
- Writes file under workspace:
  - `workspaces/<thread_id>/skills/morning-brief-windows/SKILL.md`
- Skill appears in `list_skills`.

## Step 5: Prove Reuse
User prompt:
```text
Run my morning brief now.
```

Expected behavior:
- Uses learned preference without Linux-style commands.
- Uses skill flow (loads/uses `morning-brief-windows` instructions as needed).
- Produces concise morning brief.

## Optional Verifications
User prompts:
```text
Show memory entries related to command preferences.
```
```text
List skills and show morning-brief-windows.
```

Expected behavior:
- `search_memory` / `memory_search` returns preference.
- `list_skills` shows `morning-brief-windows`.
- `read_skill` returns full skill text.

## Success Criteria
- Agent recovers from a Windows compatibility miss.
- Durable preference is saved.
- Reusable skill is created and discoverable.
- Subsequent run uses correct command style immediately.
