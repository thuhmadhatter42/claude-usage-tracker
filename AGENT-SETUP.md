# Set up jusage for me

*Paste this whole file to Claude Code (or any agent with a terminal on this Mac).*

You are installing **jusage**, a local tool that shows my Claude 5-hour and 7-day limits per account
in the menu bar and, if I share, shows the people I split a plan with who used what. Source and
README: https://github.com/thuhmadhatter42/claude-usage-tracker

It needs macOS 13 or newer and a network connection. On macOS 12 or Linux only the command line runs;
see "From source" in the README instead of step 2.

## 1. Tell me what I am agreeing to, before anything runs

Say these points to me in plain words. Do not skip this because the install looks easy.

1. It reads my claude.ai login from the macOS Keychain, read-only, and macOS may show a permission
   prompt. It never writes, renews or refreshes that login, and never prints it.
2. It sends that token to Anthropic's own usage service (the endpoint Claude Code's `/usage` uses) to
   read my meters. Nothing else carries my login anywhere, and nothing leaves this Mac unless I share.
3. If I share, a file in the synced folder shows my name, my Mac's name, day totals by model and
   account, Claude's meters, and the start and end of every 5-hour window I used Claude in. That is a
   working-hours log. It also carries the email address and claude.ai account id of every account on
   this Mac it tracks, which is how the same account lines up across people's files. Project folder
   names go only if I ask for `--projects`.
4. Cost figures are an API-rate equivalent, a relative scale for splitting a plan. They are not my bill.
5. It is a third-party tool using a subscription login; Anthropic's terms on that are theirs to
   change. My account, my call.
6. Optional and off by default: a status-line backup that reads usage from Claude Code's status line
   so it calls Anthropic less. Only if I ask for it.

Then ask me two things:
- **My name** as the others should see it. If I do not say, the installer uses the first word of this
  Mac account's full name.
- **The shared folder**, if I want to share: a folder I already sync with the others (iCloud Drive,
  Dropbox, Syncthing, a shared drive). Use `<that folder>/claude-usage/reports`. If I say no or have
  none, install without sharing; it can be added later.

## 2. Install (one command, no questions)

```sh
curl -fsSL https://raw.githubusercontent.com/thuhmadhatter42/claude-usage-tracker/main/install.sh \
  | bash -s -- --user "<my name>" --reports "<shared folder>/claude-usage/reports"
```

Drop `--user` to take the default name and drop `--reports …` if I am not sharing. Add `--projects`
only if I asked for project names (it is remembered even without `--reports`). Add `--no-launch` to
leave the app closed. `install.sh -h` lists the options; an option without its value stops with a
message.

It downloads the latest signed, notarized release, verifies it, and copies `jusage.app` to
`/Applications` (to `~/Applications` if `/Applications` is not writable; it says where). It needs
Apple's Command Line Tools: if they are missing it opens Apple's install window and waits for me to
click Install, and gives up after 15 minutes if I cancel. It then runs the first scan, adds the
`jusage` command to my shell, and opens the app.

## 3. Prove it works, then stop

```sh
jusage report --since 7d      # a table with numbers, not zeros (if I have used Claude Code)
jusage live                   # one line per account: 5-hour and 7-day meters
```

(Open a new Terminal window first so the `jusage` command exists; before that the command is
`jusage` inside the install.)

- The menu bar shows my meters. Click it. Bright fill is used, dark fill is the forecast at the reset,
  the white line is where being on pace would put me, and grey means the reading is 15 minutes old or
  older.
- If sharing: `<shared folder>/claude-usage/reports/` now holds a `<name>@<mac>-<id>.json`, and
  `~/.claude-usage/dashboard.html` on this Mac (built by `jusage dashboard` or the app) opens in a
  browser and shows me.
- If `live` reports a login problem for an account, read me the line. The login-error text:
  - "Open Claude Code on this account, then refresh": the login expired. Open a Claude Code window on
    that account once, then run `jusage live` again.
  - "no claude.ai login found; run `claude /login` in this config folder": that account has no login.
  - A Keychain prompt may be waiting on screen: click Always Allow.
  Never try to refresh or rewrite the login yourself. `jusage doctor` prints, per account, which
  Keychain items were tried and which is in use.

Report the three results in three lines and stop. Do not schedule anything: the app refreshes itself
and rewrites the shared report while it runs.

## Later

- `jusage upgrade` checks for the latest signed release (a source copy runs `git pull`).
- `jusage share --reports <folder>` to start sharing later; `jusage set-user <name>` to rename.
- `jusage statusline on` turns on the status-line backup, only if I ask; `jusage statusline off`
  restores my original status line exactly.
- `jusage uninstall` restores status-line commands and moves jusage to the Trash.
- Open at login is a checkbox in the app's settings (⌘,). Other keys: ⌘R refresh, ⌘D dashboard, ⌥← / ⌥→ previous / next month.
