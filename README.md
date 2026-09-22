# jev-demo

THIS IS A DEMONSTRATION OF HOW TO USE JEV TO LABEL EMAILS

## What it does

Labels Gmail threads with [Jev, TypeSafe's System One model](https://typesafe.ai).

The label taxonomy comes from your own Gmail account. Every label you made becomes one question, "should this label be added to this thread?", and all of them are asked in a single Jev request per thread. Anything scoring at or above the threshold is applied. Nothing is written to Gmail unless you ask for it.

## Quickstart

Do the Google setup below once and put your `TYPESAFE_API_KEY` in `.env`. After that, getting going is two commands and a run is three more.

```
script/setup          # once, builds the server and installs the CLI
script/server         # leave this running

jev auth              # once, opens a browser
jev settings          # say what your labels mean, ctrl+s to save
jev run --last 10     # watch the rows land, press a to apply
```

The rest of this file covers each of those in turn.

## One time Google setup

You supply your own Google OAuth client, because the app talks to your mailbox with your credentials and nobody else's.

1. Open the [Google Cloud console](https://console.cloud.google.com/) and create a project, or pick one you already have.
2. Enable the Gmail API **for that same project**. Under APIs and services, Library, search for Gmail API and press Enable.
3. Set the audience on the consent screen, which the console now calls Google Auth Platform. Pick **Internal** if this is a Google Workspace account and the project sits in your organization. Otherwise pick **External** and add your own address under Test users, because without that Google refuses consent.
4. Under Clients, create an OAuth client ID of type **Desktop app**.
5. Download the JSON and save it as `credentials.json` at the root of this repo. The top level key should be `installed`. If it is `web`, the application type was wrong in step 4.

The app asks for one scope, `gmail.modify`. That covers listing labels, reading threads, and adding a label to a thread. It does not allow deleting mail.

### External apps expire their tokens weekly

An External app left in Testing status gets refresh tokens that Google expires after seven days. When that happens the server drops the dead token and `/auth/status` reports `authenticated: false`, so you run the login again. An Internal app has no such limit.

### A warning about `credentials.json`

`.gitignore` covers `credentials.json`, so a normal `git add` will not pick it up. It is still a real file sitting inside a working copy, so `git add -f` would commit it anyway, and zipping or copying the directory carries it along. Treat it like any other secret when you move this directory around.

## Where things are kept

- `credentials.json` at the repo root is the Google OAuth client. For a Desktop app client Google does not treat the client secret as confidential, and it grants nothing until someone completes consent.
- **The OAuth token lives in the OS keychain**, under service `jev-demo` and account `gmail-oauth-token`. It is a refresh token for a live mailbox, so it never touches the disk and there is no file fallback. A machine with no keychain gets a startup error naming what to install. On macOS and Windows this works out of the box. On Linux, install a Secret Service provider such as gnome-keyring or KeePassXC.
- `~/.jev-demo/` holds the label descriptions you write in the settings screen.

`TYPESAFE_API_KEY` comes from the environment, read out of a `.env` file at the repo root. Create it with your own key and keep it to yourself, the same as `credentials.json`.

```
TYPESAFE_API_KEY=ts-your-key-here
```

## Setting up

```
script/setup
```

That builds the server's virtual environment at `server/.venv` with everything `pyproject.toml` asks for, including the test dependencies, then builds the CLI and installs it as `jev`. It needs Python 3.13 and Go on your PATH and says so plainly if either is missing.

Run it again whenever the dependencies or the CLI change. Building over an existing virtual environment leaves it in place.

## Running the server

```
script/server
```

That activates the virtual environment, loads `.env`, and starts uvicorn with reload on at http://127.0.0.1:8000. The script works from any directory, since it finds the repo through its own path.

`GET /health` reports whether Gmail is connected, whether the Jev key is set, and which keychain backend was chosen. A missing or misnamed `.env` does not stop the server, so `jev_key_present` there is the way to catch it.

Everything the CLI does is an HTTP call, and the endpoints are browsable at http://127.0.0.1:8000/docs if you want to drive the server directly.

## The CLI

`script/setup` already installed it, as `jev` in your Go bin directory, so you can run it from anywhere. It tells you if that directory is not on your PATH and what to add.

To rebuild it on its own after a change, without redoing the server:

```
script/install
```

To build it in place instead, without installing:

```
cd cli
go build -o jev .
```

`jev` is a thin client over the server, so the server has to be running. Every command takes `--server` if it listens somewhere other than http://127.0.0.1:8000, and `--json` to print JSON instead of prose.

## Connecting Gmail

```
jev auth
```

That reports the mailbox if Gmail is already connected, and otherwise runs the consent flow. A browser window opens on Google's consent screen. Approve it, and the token goes into the keychain. The command answers with the mailbox it connected to.

You only do this once. Later restarts refresh the token in place, with no browser. To check where the token ended up on macOS:

```
security find-generic-password -s jev-demo
```

To disconnect, delete that keychain entry and run the login again.

## Saying what your labels mean

```
jev settings
```

A label name is not often descriptive enough on its own. "Ops" could be anything. The description you write here is the sentence that goes into the question Jev is asked about that label, so this is where you steer what a label actually catches.

```
  Invoices      receipts, bills, and payment requests
> Recruiting    job applications and candidate outreach_
  Ops
  Newsletters   marketing email I want filed away

  ↑/↓ move · tab next field · ctrl+s save · esc discard
```

The arrow keys or tab move between rows, `ctrl+s` saves, and `esc` leaves without saving. Descriptions are kept by label ID, so renaming a label in Gmail keeps what you wrote for it, and clearing a field takes the description away. They are stored in `~/.jev-demo/GMAIL_LABEL_DESCRIPTIONS.json`.

Descriptions are optional. A label with nothing written for it is still asked about, just on its name alone.

## Running it

Get the most recent emails from the connected Gmail inbox and assign labels to them.


```
jev run --last 10
```

Rows appear as the server answers, one per thread, with the labels Jev picked on their own line underneath. The footer counts progress and totals what the run has spent.

```
  Your billing documents are attached   Sep 18  billing@acme.com  Attached are…
    [Billing] [Receipts]

  14/25 classified · DRY RUN, nothing written to Gmail
  1,284 tokens · $0.00005      a apply all · q quit
```

Nothing has reached Gmail at this point. Press `a` to add every label on the list to the email in your inbox, or `q` to leave without writing anything.

Which threads to look at is up to you. `--last N` takes the most recent N and defaults to 25, `--since 2026-01-01` and `--between 2026-01-01..2026-02-01` select by day, and `--query` adds raw Gmail search terms on top of whatever the rest selected. `--threshold` moves the bar a label has to clear to be assigned to an email and defaults to 0.5.

`--apply` skips the list and writes as the answers arrive, which is there for scripts rather than for a person watching. `--json` prints the raw stream instead, one line per thread as it is answered and a totals line at the end.

```json
{"thread_id":"1a06…","subject":"Your billing documents are attached","labels":[{"id":"Label_38…","name":"Billing","noul":0.96,"apply":true}],"applied":false,"usage":{"input_tokens":1182},"error":null}
{"totals":{"threads":10,"input_tokens":11544,"cost_usd":0.00048485}}
```

Labeling only ever adds. No label is created and none is removed, so a second run over the same threads does nothing.

A thread that fails carries the reason in its own `error` field and the rest of the run continues.

## License

[MIT](LICENSE).
