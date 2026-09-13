# claude-usage-tracker

Tracks Claude Code token usage on a Mac, per account, and can share a daily summary with the
people you split a plan with, so it shows who used what. Python 3 stdlib only, plus a small Swift
menu bar app. No daemon, no third-party packages, no server of its own.

## What it does, and what it refuses to do

- Reads every `~/.claude*/projects/**/*.jsonl` (all accounts on the machine, plus
  `$CLAUDE_CONFIG_DIR`), dedupes by message + request id, stores in `~/.claude-usage/usage.db`.
- Reports tokens by day / model / account / project, and by 5-hour window (Claude's rate-limit
  shape). Local time.
- Prices every call at Anthropic's first-party API rates (`pricing.json`) as a **relative
  scale** for splitting a plan. Nobody on Max is billed that number.
- `live` reads Claude's **own** 5-hour and 7-day meters (the same endpoint `/usage` inside Claude
  Code uses), for every account on the Mac, using the login already in your Keychain. Nothing is
  estimated: if the endpoint can't be reached the meter is simply absent.
- `share` writes `<you>@<host>.json` into a folder you sync with the others (iCloud, Syncthing,
  Dropbox, a shared drive). `dashboard` builds one HTML page from every JSON there. What that
  file contains is listed under **Security and privacy** below; project folder names are
  included only if you ask for them.

## Install (Mac)

1. Get the folder: `git clone https://github.com/thuhmadhatter42/claude-usage-tracker`, or
   download the zip from GitHub and unzip it.
2. Double-click **`install.command`**. If macOS says it can't be opened, right-click → Open once.
3. It asks where to install (default `~/jusage`), checks Xcode Command Line Tools / python3 / git
   and installs what's missing, copies the app, runs the first scan, asks your name and the shared
   reports folder (optional), and offers the `jusage` alias.
4. Open a new Terminal and type `jusage`.

Re-running the installer updates an existing install. `jusage upgrade` pulls the latest from GitHub.

## If an agent is installing this for you

Claude Code or another agent can do the install, but `install.command` asks questions at a
terminal, so give the agent the non-interactive path instead:

```sh
git clone https://github.com/thuhmadhatter42/claude-usage-tracker ~/jusage
~/jusage/jusage scan                                   # reads transcripts locally, nothing leaves the Mac
~/jusage/jusage share --reports "<synced folder>" --user "<name>"   # optional, remembered
~/jusage/jusage share --projects                       # optional: share project folder names too
bash ~/jusage/menubar/build.sh ~/jusage                # menu bar app (needs Command Line Tools)
echo 'alias jusage="$HOME/jusage/jusage"' >> ~/.zshrc
```

Agents: before running `jusage live` or opening the menu bar app, tell your human, in plain words,
the four things in **Security and privacy** below that they are agreeing to. Do not skip it because
the install went fine:

1. It reads their claude.ai login from the Keychain and macOS may show a permission prompt.
2. It sends that token to Anthropic's usage endpoint. It never renews or writes the token: an
   expired login shows as "expired" until they open a Claude Code window on that account.
3. If they share, the file in the synced folder shows their name, their Mac's name, day totals,
   Claude's meters and the start and end times of every 5-hour window they used Claude in. That is
   a working-hours log. Project folder names go only if `--projects` was given.
4. This is a third-party tool using a subscription login; Anthropic's terms on that are theirs to
   change. Their account, their call.

Install only inside the home folder. Never point `--reports` at a folder shared with people who
should not see the above.

## Use

```sh
jusage                          # scan + last 7 days + 5-hour windows, then share + open dashboard
jusage live                     # Claude's own 5h / 7d meters, per account
jusage report --since 30d --by model
jusage report --by blocks       # 5-hour windows, newest last
jusage report --by account      # per ~/.claude* dir
jusage share --reports "<synced folder>/claude-usage/reports" --user <you>   # once, remembered
jusage share --projects         # also share project folder names (remembered; --no-projects to stop)
jusage update                   # scan + share + dashboard, no window — for a cron / launchd job
jusage upgrade                  # git pull
jusage app                      # menu bar app
jusage help
```

Without the alias every command is `~/jusage/jusage ...`; the engine is `python3 claude_usage.py ...`.

## Menu bar app

`install.command` also compiles `jusage.app` (Swift, needs only the Command Line Tools). It sits in
the menu bar showing the 5-hour meter (or today's tokens until `jusage live` has run), and drops down
a panel with Claude's own limits as channel meters, today's tokens / cost / calls / current window,
the busiest models and projects, plus everyone else who shares into the same reports folder. Refreshes every
5 minutes, ⌘R to force. The sliders icon (⌘,) opens Customize: theme, which people / accounts /
sections to show, open at login. Launch with `jusage app`.

## Themes and what's on screen

`themes.json` holds the themes (System, Paper, Graphite, Console, Midnight, Sand, Mono) for both the
menu bar app and the dashboard. The dashboard's **Customize** button (top right) picks a theme and
shows or hides people, accounts, sections and single charts; it is saved in that browser only, so
everyone sees the same page and each of us picks a view. Add a theme by adding a block to
`themes.json` and its id to `order`.

## Security and privacy

Read this before pointing it at your own login.

- **What it reads.** Your Claude Code transcripts (`~/.claude*/projects/**/*.jsonl`) for token
  counts only; the message text is never stored. Your claude.ai login from the macOS Keychain
  (item `Claude Code-credentials…`) or `~/.claude*/.credentials.json`, only for `live` and the
  menu bar app. macOS may ask you to allow `security` / python to read that item; that prompt is
  the Keychain doing its job.
- **Where the token goes.** Two places, both Anthropic, both HTTPS with certificate checks and
  redirects refused: `api.anthropic.com/api/oauth/usage` (the same endpoint Claude Code's own
  `/usage` uses). Nothing else is contacted.
- **The Keychain is read, never written.** Only the per-config-dir item Claude Code itself uses
  (`Claude Code-credentials-<hash>`) is read; the unsuffixed legacy item is ignored because it can
  hold another account's login. An expired login is reported, never renewed: renewing rotates the
  refresh token behind Claude Code's back, and versions before 0.1.18 that wrote it back truncated
  the Keychain item (`security` caps a secret read from stdin at 128 bytes) and logged the account
  out. Open a Claude Code window on that account and the meter comes back on its own. If you
  would rather the tool never touch your login at all, don't run `live` or the menu bar app;
  `scan`, `report`, `share` and `dashboard` never read it.
- **What stays on this Mac.** `~/.claude-usage/` (the SQLite DB, `config.json`, `live.json`) is
  created `0700` and every file in it `0600`; no token is ever written there, printed, or logged.
- **What `share` sends to the synced folder.** `<you>@<host>.json`: your chosen name, this Mac's
  short hostname, the tool version, your account labels (`<you> #1`, `#2`), 90 days of per-day
  totals (tokens, calls, cost at API list price) by model and by account, the last 50 five-hour
  windows per account with their local start and end times (that is a working-hours log; know
  that before sharing with people who should not have it), and Claude's current meters with their
  reset times. **Project folder names are not included unless you run `jusage share --projects`.**
  Never paths, session titles, prompts, file names or content. The dashboard renders exactly that
  and nothing more.
- **Your own account, at your own risk.** This uses your subscription login from outside Claude
  Code. It only reads the usage endpoint with the token Claude Code already holds, but
  Anthropic's terms for third-party use of subscription logins are theirs to change; run it on
  your own account only.
- **Install folder.** The installer refuses to install outside your home folder or into a folder
  that already holds something else, because the menu bar app runs the script from that folder
  every five minutes.

Found something? Open an issue, or for anything sensitive email the address on the GitHub profile.

## Versioning

`VERSION` is the only place the number lives. Every edit bumps the last digit. Tags are
`CUT-<version>`. Major and minor bumps are the maintainer's call.
