# Querying ActivityWatch from Claude (MCP)

[Auriora/activitywatch-mcp](https://github.com/Auriora/activitywatch-mcp) gives Claude tools to read
your ActivityWatch data (period summaries, raw events, AQL queries) and to manage categories.
Notes from setting it up with Claude Code on Windows (version 0.3.2).

## Install

There is no npm package - build from source (Node 18+):

```powershell
git clone https://github.com/auriora/activitywatch-mcp.git
cd activitywatch-mcp
npm install --ignore-scripts
npm run build
```

`--ignore-scripts` is optional; the server's own code only calls `AW_URL` (default `localhost:5600`).

## Register in Claude Code

Add to `~/.claude.json` (user scope, available in every project):

```json
"mcpServers": {
  "activitywatch": {
    "type": "stdio",
    "command": "node",
    "args": ["C:/path/to/activitywatch-mcp/dist/index.js"],
    "env": {
      "AW_URL": "http://localhost:5600",
      "LOG_LEVEL": "WARN",
      "ACTIVITYWATCH_TIMEZONE": "Europe/Prague"
    }
  }
}
```

New sessions pick it up.

## Gotchas

- **Time zone.** Without `ACTIVITYWATCH_TIMEZONE` (or `TZ`) the server uses `config/user-preferences.json`
  from the repo, which says `Europe/Dublin` - daily summaries are shifted by an hour. Set your IANA zone.
- **A log line on stdout.** On start it prints `[UserPreferences] Loaded preferences: …` to stdout,
  which is the MCP channel. The official SDK client skips non-JSON lines, so Claude Code works; a strict
  client of your own will choke on it.
- **Tool names differ from the README.** 0.3.2 exposes `aw_get_capabilities`, `aw_get_activity`,
  `aw_get_period_summary`, `aw_get_raw_events`, `aw_query_events`, `aw_list_categories`,
  `aw_add_category`, `aw_update_category`, `aw_delete_category`. There is no `aw_get_daily_summary`
  - use `aw_get_period_summary` with `period_type: "daily"`.
- **Categories are writable.** `aw_add/update/delete_category` change your ActivityWatch settings;
  worth asking Claude to confirm before using them.
- **Custom buckets.** Summaries are built from window/web/AFK buckets. For the aw-toolkit buckets use
  `aw_get_raw_events` with the bucket id (e.g. `aw-watcher-output_<host>`).
