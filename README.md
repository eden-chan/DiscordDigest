# Python Discord Bot Template

<p align="center">
  <a href="https://discord.gg/xj6y5ZaTMr"><img src="https://img.shields.io/discord/1358456011316396295?logo=discord"></a>
  <a href="https://github.com/kkrypt0nn/Python-Discord-Bot-Template/releases"><img src="https://img.shields.io/github/v/release/kkrypt0nn/Python-Discord-Bot-Template"></a>
  <a href="https://github.com/kkrypt0nn/Python-Discord-Bot-Template/commits/main"><img src="https://img.shields.io/github/last-commit/kkrypt0nn/Python-Discord-Bot-Template"></a>
  <a href="https://github.com/kkrypt0nn/Python-Discord-Bot-Template/blob/main/LICENSE.md"><img src="https://img.shields.io/github/license/kkrypt0nn/Python-Discord-Bot-Template"></a>
  <a href="https://github.com/kkrypt0nn/Python-Discord-Bot-Template"><img src="https://img.shields.io/github/languages/code-size/kkrypt0nn/Python-Discord-Bot-Template"></a>
  <a href="https://conventionalcommits.org/en/v1.0.0/"><img src="https://img.shields.io/badge/Conventional%20Commits-1.0.0-%23FE5196?logo=conventionalcommits&logoColor=white"></a>
  <a href="https://github.com/psf/black"><img src="https://img.shields.io/badge/code%20style-black-000000.svg"></a>
</p>

> [!NOTE]
> This project is in a **feature-freeze mode**, please read more about it [here](https://github.com/kkrypt0nn/Python-Discord-Bot-Template/issues/112). It can be summed up in a few bullet points:
> 
> * The project **will** receive bug fixes
> * The project **will** be updated to make sure it works with the **latest** discord.py version
> * The project **will not** receive any new features, **unless one of the following applies**:
>   * A new feature is added to Discord and it would be beneficial to have it in the template
>   * A feature got a breaking change, this fits with the same point that the project will **always** support the latest discord.py version

This repository is a template that everyone can use for the start of their Discord bot.

When I first started creating my Discord bot it took me a while to get everything setup and working with cogs and more.
I would've been happy if there were any template existing. However, there wasn't any existing template. That's why I
decided to create my own template to let **you** guys create your Discord bot easily.

Please note that this template is not supposed to be the best template, but a good template to start learning how
discord.py works and to make your own bot easily.

If you plan to use this template to make your own template or bot, you **have to**:

- Keep the credits, and a link to this repository in all the files that contains my code
- Keep the same license for unchanged code

See [the license file](https://github.com/kkrypt0nn/Python-Discord-Bot-Template/blob/master/LICENSE.md) for more
information, I reserve the right to take down any repository that does not meet these requirements.

## Support

Before requesting support, you should know that this template requires you to have at least a **basic knowledge** of
Python and the library is made for **advanced users**. Do not use this template if you don't know the
basics or some advanced topics such as OOP or async. [Here's](https://pythondiscord.com/pages/resources) a link for resources to learn python.

If you need some help for something, do not hesitate to create an issue over [here](https://github.com/kkrypt0nn/Python-Discord-Bot-Template/issues), but don't forget the read the [frequently asked questions](https://github.com/kkrypt0nn/Python-Discord-Bot-Template/wiki/Frequently-Asked-Questions) before.

All the updates of the template are available [here](UPDATES.md).

## Disclaimer

Slash commands can take some time to get registered globally, so if you want to test a command you should use
the `@app_commands.guilds()` decorator so that it gets registered instantly. Example:

```py
@commands.hybrid_command(
  name="command",
  description="Command description",
)
@app_commands.guilds(discord.Object(id=GUILD_ID)) # Place your guild ID here
```

When using the template you confirm that you have read the [license](LICENSE.md) and comprehend that I can take down
your repository if you do not meet these requirements.

## How to download it

This repository is now a template, on the top left you can simply click on "**Use this template**" to create a GitHub
repository based on this template.

Alternatively you can do the following:

- Clone/Download the repository
  - To clone it and get the updates you can definitely use the command
    `git clone`
- Create a Discord bot [here](https://discord.com/developers/applications)
- Get your bot token
- Invite your bot on servers using the following invite:
  https://discord.com/oauth2/authorize?&client_id=YOUR_APPLICATION_ID_HERE&scope=bot+applications.commands&permissions=PERMISSIONS (
  Replace `YOUR_APPLICATION_ID_HERE` with the application ID and replace `PERMISSIONS` with the required permissions
  your bot needs that it can be get at the bottom of a this
  page https://discord.com/developers/applications/YOUR_APPLICATION_ID_HERE/bot)

## How to set up

To set up the token you will have to make use of the [`.env.example`](.env.example) file; you should rename it to `.env` and replace the `YOUR_BOT...` content with your actual values that match for your bot.

Alternatively you can simply create a system environment variable with the same names and their respective value.

## How to start

### The _"usual"_ way

To start the bot you simply need to launch, either your terminal (Linux, Mac & Windows), or your Command Prompt (
Windows)
.

Before running the bot you will need to install all the requirements with this command:

```
python -m pip install -r requirements.txt
```

After that you can start it with

```
python bot.py
```

> **Note**: You may need to replace `python` with `py`, `python3`, `python3.11`, etc. depending on what Python versions you have installed on the machine.

### Docker

Support to start the bot in a Docker container has been added. After having [Docker](https://docker.com) installed on your machine, you can simply execute:

```
docker compose up -d --build
```

> **Note**: `-d` will make the container run in detached mode, so in the background.

## Hikari Digest (What’s Happening)

An optional Hikari-based utility is included to summarize recent conversations into a digest using Gemini.

- Configure env:
  - `DIGEST_CHANNEL_ID` — channel to post the digest
  - `DISCORD_TOKEN_TYPE` — `Bot` (default) or `Bearer`
  - `TIME_WINDOW_HOURS` — lookback window (default 72)
  - `GEMINI_API_KEY` — optional; enables Gemini summaries

Install requirements and run a one-off preview:

```
python -m pip install -r requirements.txt
python -m digest --list-channels   # optional helper to get channel IDs
python -m digest --dry-run         # print digest to terminal (no posting)
python -m digest                   # post a preview digest

## TUI Tester (Textual)

Run a simple Textual-based TUI to validate read access and summaries without posting. The TUI reads channels from SQLite by default.

```
python -m pip install -r requirements.txt
python -m prisma generate && python -m prisma db push   # one-time setup
python -m digest --sync-channels                        # Bot token required
python -m tui
```

Keys:
- `r` refresh channel list
- `space/enter` select channels in the left pane
- `h` cycle lookback hours (24/48/72)
- `d` dry-run: fetch, score, and summarize; prints results in the right pane
- `q` quit

Exporting selections
- Press `e` to export selected channels:
  - Upserts the selection into SQLite for the configured `GUILD_ID`.
  - Also writes a helper file at `data/selected_channels.json`.

Seed DB from JSON
```
python -m digest --seed-channels-from-json --json-path data/channels.json   # uses guild_id from JSON or --guild
```

## Export Channels to JSON (optional)

Scrape available channels (via Hikari REST) and write them to a JSON file. You can seed the database from this if desired, but the TUI/CLI prefer SQLite.

```
python -m digest.scrape --out data/channels.json
```

Notes:
- With `DISCORD_TOKEN_TYPE=Bot` and `GUILD_ID` set, this exports all guild channels with names/types.
- Bearer tokens may not list all channels due to permissions; prefer a Bot token or use a pre-generated `data/channels.json`.
- Do not commit `data/channels.json` or `data/oauth_token.json`. Use `data/channels.example.json` as a template and keep real files local.

### OAuth Helper (optional)

If you authorize with `messages.read` and need a Bearer token, you can exchange or refresh via env-driven helpers:

```
# Set OAUTH_* in your environment (.env)
# OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_REDIRECT_URI
# Optionally OAUTH_CODE (otherwise you will be prompted)
# or OAUTH_REFRESH_TOKEN (for refresh)

python -m digest --oauth-login --out data/oauth_token.json      # spins up local server, opens browser, captures code
# or
python -m digest --oauth-exchange --out data/oauth_token.json   # prompts for code if not set
# or
python -m digest --oauth-refresh --out data/oauth_token.json

# You can then set TOKEN (or OAUTH_ACCESS_TOKEN) from the output.
# Tokens are also saved to SQLite automatically for centralized storage.
# Probe the current token and scope:
python -m digest --oauth-probe
```

```

This posts a simple digest to the configured channel using Hikari REST.

## Issues or Questions

If you have any issues or questions of how to code a specific command, you can:

- Join my Discord server [here](https://discord.gg/xj6y5ZaTMr)
- Post them [here](https://github.com/kkrypt0nn/Python-Discord-Bot-Template/issues)

Me or other people will take their time to answer and help you.

## Versioning

We use [SemVer](http://semver.org) for versioning. For the versions available, see
the [tags on this repository](https://github.com/kkrypt0nn/Python-Discord-Bot-Template/tags).

## Built With

- [Python 3.12.9](https://www.python.org/)

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE.md](LICENSE.md) file for details
## Setup Script

For macOS/Linux:
```
bash scripts/setup.sh
# then in the same shell session
source .venv/bin/activate
```

For Windows (PowerShell):
```
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
# then in the same shell
.\.venv\Scripts\activate
```

The setup script:
- Creates `.venv`, bootstraps `pip` even if missing
- Installs requirements
- Generates Prisma client and pushes the SQLite schema
