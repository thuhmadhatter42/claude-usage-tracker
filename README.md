# claude-usage-tracker (jusage)

A menu bar app and a command line that show how much of Claude's 5-hour and 7-day limits you have
used, per account, and, if you share a plan with other people, who used what. The app is native
(Swift, macOS 13 or newer); the engine behind it is Python 3.9+ standard library only. No daemon, no
third-party packages, no server of its own.

## Reading the meters

Every meter is one bar. The key:

| Part of the bar | Meaning |
|---|---|
| **Bright fill** | What you have used now, as Claude reports it. |
| **Dark fill** (beyond the bright) | Where you land at the reset if you keep the pace you have had so far in this period. A straight-line forecast from Claude's own percentage and the clock; there is no forecast in the first 3% of a period. |
| **White line** | Where you would be if you were exactly on pace: the share of the period that has gone. |
| **Grey** (whole bar and number) | The reading is 15 minutes old or older. The last value stays visible; hover for its age and the reason. |

Colour: **red** when used is 97% or more; **amber** when used is past the white line (ahead of pace),
never in the first 3% of a period; **green** otherwise. The forecast never changes the colour, it
only draws the dark fill and the "runs out" line in the hover.

Hover a row for the sentence: what each part means with that meter's own numbers, the reset time, the
source and age of the reading ("from Claude Code, 40 s ago" or "from Anthropic, 2 min ago"), and the
forecast in words.

## What it does, and what it refuses to do

- Reads every `~/.claude*/projects/**/*.jsonl` (all accounts on the Mac, plus `$CLAUDE_CONFIG_DIR`)
  for token counts, de-duplicated by message and request id, into `~/.claude-usage/usage.db`.
  Message text is never stored.
- Reports tokens and cost by day, model, account, project and 5-hour window. Local time.
- **Cost is an API-rate equivalent, not your bill.** Each call is priced at Anthropic's first-party
  API list rates (`pricing.json`) as a relative scale for splitting a plan. Nobody on a Max or Pro
  plan is charged that number.
- Reads Claude's **own** 5-hour, 7-day and per-model meters from one place, Anthropic's usage
  service (`api.anthropic.com/api/oauth/usage`, the endpoint Claude Code's `/usage` uses), for every
  account on the Mac. The percentages and reset times are Claude's; only the forecast is jusage's
  estimate. If a reading can't be had the number stays at its last good value and greys, never
  invented.
- Labels each account by its email, read from Claude Code's own `<config dir>/.claude.json`, and
  checks that identity before and after every read. A reading is thrown away if the account changed
  under it, or if its weekly reset matches another account's.
- Reads each account no more often than every 60 seconds by default (15 s to 10 min,
  `jusage config read-every <seconds>` or the app's settings). A rate limit (`Retry-After`) or
  backoff always wins over that cadence.
- `share` writes `<you>@<host>-<id>.json` into a folder you sync with the others. `<id>` is four
  random characters made once per install. `dashboard` builds one HTML page from every file there.
  What the file holds is listed under **Security and privacy**.

## Install

Needs macOS 13 or newer, a network connection, and your home folder. Paste into Terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/thuhmadhatter42/claude-usage-tracker/main/install.sh | bash
```

No questions. It downloads the latest signed and notarized release, checks it with `spctl`, copies
`jusage.app` to `/Applications` (to `~/Applications` when `/Applications` is not writable, and it
says so), runs the first scan, adds the `jusage` command to your shell and opens the app. It needs
Apple's Command Line Tools (Python 3.9+ ships with them); if they are missing Apple's install window
opens, you click Install, and the script continues by itself, giving up after 15 minutes if the
dialog is cancelled. Re-running it upgrades in place; so does `jusage upgrade`.

Options, after `bash -s --`:

| Option | Meaning |
|---|---|
| `--reports <folder>` | Share into this synced folder (remembered). |
| `--user <name>` | Your name as the others see it. Default: the first word of this Mac account's full name. |
| `--projects` | Also share project folder names (remembered, with or without `--reports`). |
| `--from-source <dir>` | Install from a clone of this repo instead of downloading a release (see below). |
| `--no-launch` | Do not open the app afterwards. |
| `-h` | List the options. |

An option given without its value stops with a message. Example:

```sh
curl -fsSL https://raw.githubusercontent.com/thuhmadhatter42/claude-usage-tracker/main/install.sh \
  | bash -s -- --user "Sam" --reports "<synced folder>/claude-usage/reports"
```

You can also share later: `jusage share --reports "<synced folder>/claude-usage/reports"`. A
downloaded copy of the repo can be installed by double-clicking `install.command`.

### From source (macOS 12, command line only)

The app needs macOS 13. On macOS 12 the installer says so and points here; the command line and the
dashboard run from a git checkout. `install.sh --from-source ~/jusage` does all of it, or by hand:

```sh
git clone https://github.com/thuhmadhatter42/claude-usage-tracker ~/jusage
python3 ~/jusage/engine/claude_usage.py config python "$(command -v python3)"
python3 ~/jusage/engine/claude_usage.py scan
```

`config python` records the interpreter the `jusage` command runs; link `~/jusage/engine/bin/jusage`
somewhere on your `PATH` to use it. A source copy upgrades with `jusage upgrade` (a `git pull`).
`install.sh --from-source <dir>` does the same on a Mac and builds nothing; the folder must sit
inside your home folder, and a non-empty folder that is not a jusage install is refused.
To build the app yourself: `app/build-app.sh dist`, then copy `dist/jusage.app` into `/Applications`.

### If an agent is installing this for you

Hand your agent **[AGENT-SETUP.md](AGENT-SETUP.md)**. It tells the agent what to say to you first
(the privacy points below), what to ask (your name, the shared folder), the one install command,
and how to prove the install works before it stops.

## Use

```sh
jusage                          # scan, live meters, last 7 days, 5-hour windows; then share + open the dashboard if a folder is set
jusage live                     # Claude's own meters, per account   (--force: read now; --raw: also the stored replies)
jusage report --since 30d --by model
jusage report --by blocks --last 6     # 5-hour windows, newest last
jusage report --by account      # per ~/.claude* folder   (also --by day | project; --account <folder> for one)
jusage scan [--full]            # read new transcripts (--full: re-read every file)
jusage share --reports "<synced folder>/claude-usage/reports" --user <you>   # once, remembered
jusage share --projects         # also share project folder names (--no-projects stops it)
jusage set-user <name>          # rename yourself in shared reports
jusage dashboard [--out <file>] [--open]    # build the page from every shared report
jusage update                   # scan + share + dashboard, no window: for a cron / launchd job
jusage statusline on|off|status # the status-line backup, below
jusage doctor                   # per account: Keychain items tried, identity, fetch gate, status line
jusage menu-json                # the app's feed on stdout (schema 2)
jusage config get [key]         # also: config read-every <seconds>, config python <path>
jusage app                      # open the menu bar app
jusage upgrade                  # app: checks for a signed update; source copy: git pull
jusage uninstall                # restore status-line commands, open at login off, alias removed, then jusage to the Trash
jusage --version                # also: jusage help
```

Every command takes `--state-dir <folder>` before it to use a state folder other than
`~/.claude-usage`. Without the alias, the command is `engine/bin/jusage` in the install, and the
engine is `python3 engine/claude_usage.py`. A refused start prints one line,
`jusage: <file>: <problem>`, and exits 2; any other failure prints one line and exits 1, with the full
trace in `~/.claude-usage/logs/engine.log`.

## The menu bar app

It sits in the menu bar showing your meters and drops down a panel: Claude's own limits as bars
(key above), how much of the week each session uses, today's tokens and spend, the busiest models and
projects, and everyone else who shares into the same folder. Refresh with ⌘R, open the dashboard with
⌘D, settings with ⌘,, and step the month in the history views with ⌥← and ⌥→. While it runs it also rewrites your shared report and the dashboard when they
are stale, so everybody's page stays current. If that write fails (the synced folder is offline), the
panel keeps its numbers and shows the reason on one line.

Settings: theme, which people, accounts and sections to show, how often to read (15 s to 10 min),
colouring the menu bar number by the account's worst meter (off by default), notifications, open at
login, and the status-line backup.

## The status-line backup (opt-in, off by default)

Claude Code can hand a status-line command the account's current 5-hour and weekly usage on every
response. With the backup on for an account, jusage takes those numbers as that account's **primary**
reading: each capture that passes the identity checks and is newer than the stored reading becomes the
reading, so while you are using Claude Code jusage barely needs to call Anthropic at all. The usage
service is read only when no accepted capture arrived within the cadence (Claude Code idle), on a
manual refresh, and about every 10 minutes to keep the per-model weeklies fresh, because the status
line carries only session and weekly. A capture that fails the checks (wrong account, weekly reset
matching another account's) is ignored.

Turn it on from the app's settings or with `jusage statusline on` (`--folder <key>` for one account,
`--all` for every one; `status` shows where it stands). It adds to your existing status line and never
replaces it: the original command is recorded exactly, `settings.json` is copied to
`~/.claude-usage/archive/`, and the command becomes the wrapper followed by `-- <your original>`. The
wrapper saves the rate limits (never the model, prompt or any text), then runs your command with the
same input and passes its output and exit code through unchanged. `jusage statusline off` and
`jusage uninstall` restore the original string exactly. An unparsable `settings.json` is refused and
left untouched, as is turning off when someone else has since changed the command.

## Who used it

Claude's meters belong to an **account**, not a person: two people on one plan see one percentage
and no clue whose it is. Under each meter jusage shows the split, like `this period: Sam 71% ($412)
· Lee 29% ($168)`. It is each person's API-equivalent spend inside that meter's own period (its
reset time, back five hours or seven days), added up from everybody's shared 5-hour windows; a
window the period boundary cuts counts by the fraction inside it. It is a share of *spend*, never of
the percentage: how Anthropic weights the percentage is nobody's to guess. Someone on an older
jusage whose report cannot be lined up shows as `not reported yet (update jusage)` rather than being
counted as zero. Each session's share of the week is shown too: "This session: 4% of the week".

## Themes and what's on screen

`themes.json` holds the themes (System, Paper, Graphite, Console, Midnight, Sand, Mono) for both the
app and the dashboard. The dashboard's **Customize** button picks a theme and shows or hides people,
accounts, sections and single charts; it is saved in that browser only. Each Mac writes its own
`~/.claude-usage/dashboard.html` from the shared reports folder. Add a theme by adding a block to
`themes.json` and its id to `order`.

## Security and privacy

Read this before pointing it at your own login.

- **The Keychain is read-only, and that is all it ever is.** jusage finds each account's login in the
  macOS Keychain item `Claude Code-credentials-<first 8 hex of sha256 of the config folder path>`,
  the name Claude Code itself uses, trying the folder's spellings in a fixed order; the first item
  that holds a login wins. The unsuffixed `Claude Code-credentials` item names no account, so it is
  read only for the default `~/.claude`, only when no hashed item holds a login, and its readings
  must pass both identity checks or are discarded; when it is used, the hover and `jusage doctor`
  warn. jusage never writes, edits, renews or deletes a Keychain item, never refreshes a token
  (rotating it would log Claude Code out), and never prints or logs one. An expired login is
  reported ("Open Claude Code on this account, then refresh") and comes back on its own once Claude
  Code renews it. macOS may ask you to allow jusage to read an item; that prompt is the Keychain
  doing its job, and a read that waits more than 30 seconds is stopped with a message saying a prompt
  may be waiting. Never want it near your login? Skip `live`, the app and the status-line backup;
  `scan`, `report`, `share` and `dashboard` never read it.
- **What it reads.** Your Claude Code transcripts, for token counts only. The login above, or
  `~/.claude*/.credentials.json` where Claude Code keeps one, only to read the meters.
- **Where the token goes.** One place: `api.anthropic.com/api/oauth/usage` over HTTPS with
  certificate checks and redirects refused. Nothing else is contacted, apart from the update check
  and the release download, which carry no login.
- **What stays on this Mac.** `~/.claude-usage/` (the SQLite DB, `config.json`, `live.json`, logs,
  status-line captures) is created `0700` and every file in it `0600`. No token is ever written there.
- **What `share` sends to the synced folder.** `<you>@<host>-<id>.json`: your chosen name, this Mac's
  short hostname, the install id and tool version, your account labels, which are **the email
  address and account id of each claude.ai account you are logged into**, 90 days of per-day totals
  (tokens, calls, API-equivalent cost) by model and account, the last 50 five-hour windows per
  account with their local start and end times (that is a working-hours log; know that before sharing
  with people who should not have it), and Claude's current meters with their reset times. **Project
  folder names are not included unless you run `jusage share --projects`.** Never paths, session
  titles, prompts, file names or content. Day totals are the reporter's own local calendar days.
- **Your own account, at your own risk.** This uses your subscription login from outside Claude
  Code. It reads only the usage endpoint with the token Claude Code already holds, but Anthropic's
  terms for third-party use of subscription logins are theirs to change; run it on your own account
  only.
- **Install location.** `install.sh --from-source` refuses a folder outside your home (`..` and
  symbolic links are resolved first), or a non-empty folder that holds something else, because the
  `jusage` command runs code from there.

Found something? Open an issue, or for anything sensitive email the address on the GitHub profile.

## Versioning

`VERSION` is the only place the number lives. Every ship (a release, not every edit) bumps the last
digit; major and minor bumps are the maintainer's call. Tags are `CUT-<version>`.
