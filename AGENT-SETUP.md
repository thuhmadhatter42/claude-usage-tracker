# Set up jusage for me

*Paste this whole file to Claude Code (or any agent with a terminal on this Mac).*

You are installing **jusage**, a small local tool that tracks my Claude Code token usage per account
and, if I share, shows the people I split a plan with who used what. Source and README:
https://github.com/thuhmadhatter42/claude-usage-tracker

## 1. Tell me what I am agreeing to, before anything runs

Say these four things to me in plain words. Do not skip this because the install looks easy.

1. It reads my claude.ai login from the macOS Keychain, and macOS may show a permission prompt.
   It never renews or writes that login.
2. It sends that token to Anthropic's own usage endpoint (the same one Claude Code's `/usage`
   uses) to read my 5-hour and 7-day meters. Nothing else leaves this Mac unless I share.
3. If I share, a file in the synced folder shows my name, my Mac's name, day totals by model and
   account, Claude's meters, and the start and end of every 5-hour window I used Claude in.
   That is a working-hours log. Project folder names go only if I ask for `--projects`.
4. It is a third-party tool using a subscription login; Anthropic's terms on that are theirs to
   change. My account, my call.

Then ask me two things:
- **My name** as the others should see it (or use this Mac's account full name).
- **The shared folder**, if I want to share: a folder I already sync with the others (iCloud
  Drive, Dropbox, Syncthing, a shared drive). Use `<that folder>/claude-usage/reports`. If I say
  no or do not have one, install without sharing; it can be added later.

## 2. Install (one command, no questions)

```sh
curl -fsSL https://raw.githubusercontent.com/thuhmadhatter42/claude-usage-tracker/main/install.sh \
  | bash -s -- --user "<my name>" --reports "<shared folder>/claude-usage/reports"
```

Drop `--reports …` if I am not sharing. Add `--projects` only if I asked for project names.
Without `--user` it uses this Mac's account full name.

It needs Apple's Command Line Tools; if they are missing it opens Apple's install window and waits
for me to click Install. The script installs to `~/jusage`, runs the first scan, builds the menu
bar app into `/Applications/jusage.app`, opens it, and adds the `jusage` command to my shell.

## 3. Prove it works, then stop

```sh
~/jusage/jusage report --since 7d      # a table with numbers, not zeros (if I have used Claude Code)
~/jusage/jusage live                   # one line per account: 5-hour and 7-day meters
```

- The menu bar shows a percentage (or today's tokens) at the right of the menu bar. Click it.
- If sharing: `<shared folder>/claude-usage/reports/` now holds a `<name>@<mac>-<id>.json` and
  `dashboard.html` next to it opens in a browser and shows me.
- If `live` says `expired` for an account: open a Claude Code window on that account once, then
  `~/jusage/jusage live` again. Never try to refresh or rewrite the login yourself.

Report the three results in three lines and stop. Do not schedule anything: the menu bar app
refreshes itself and rewrites the shared report while it runs.

## Later

- `jusage upgrade` pulls the latest version and rebuilds the app.
- `jusage share --reports <folder>` to start sharing later; `jusage set-user <name>` to rename.
- Open at login is a checkbox inside the app's Customize panel (⌘,).
