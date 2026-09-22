# jev-demo

A demo app that labels Gmail threads using Jev, TypeSafe's System One model. Jev evaluates typed questions against a state and returns structured values with calibrated probabilities.

The label taxonomy comes from the user's own Gmail account. Every non-system label becomes one `noul` question, "should this label be added to this thread?", and all of them are asked in a single Jev request per thread. Any label scoring at or above the threshold is applied with `threads.modify`.

Scope is deliberately small. No database, no edge case hardening, minimal UX.

## Layout

```
server/     Python 3.13 and FastAPI. Owns Google OAuth, Gmail reads and writes,
            the STATE.json and QUESTIONS.json templates, the Jev request, and the
            label description store.
cli/        Go 1.26, Cobra, and Bubble Tea. A thin HTTP client with no Gmail or
            Jev credentials and no business logic.
.plans/     Planning documents. Ignored by git.
```

State lives in three places, and what a thing is decides which one.

- `credentials.json` at the repo root is the Google OAuth client. Each user supplies their own and `.gitignore` covers it.
- The OS keychain holds the OAuth token, under service `jev-demo` and account `gmail-oauth-token`.
- `~/.jev-demo/` holds `GMAIL_LABEL_DESCRIPTIONS.json` and nothing else.

`TYPESAFE_API_KEY` comes from the environment.

## Writing

- No em-dashes anywhere. Not in plans, code, comments, commit messages, README text, or anything the user sees. Use a period and a new sentence, or parentheses where an aside really is an aside.
- Colons only introduce an enumeration or serve a code purpose. Never use one to join two clauses.
- Clear and concise, with as little jargon as possible. If a plain word works, use the plain word.
- Write for a reader arriving fresh. No document or comment should reference the planning process, earlier drafts, or things that were considered and dropped. The code and its docs describe the app as it is.
- Markdown soft wraps. Write each paragraph and each list item as one long line and let the editor wrap it, rather than hard wrapping at a fixed width. Inside a fenced code block the line breaks are real, so leave those alone.

## Code

- Follow standard Go and Python style. Go code is `gofmt` clean and passes `go vet`. Python code follows PEP 8, uses type hints on anything public, and is formatted with `ruff format`.
- Watch for duplication and stay DRY. Shared behavior belongs in one place. The most likely offenders are Gmail header extraction, the Jinja render plus parse step, and the HTTP retry logic, so each of those lives in exactly one function.
- Write tests, but not for every path. This is a demo app with no outside users. Cover the parts that are easy to get quietly wrong (template rendering, MIME parsing, retry behavior) and skip exhaustive coverage of everything else.

## Things that are easy to get wrong

- **The OAuth token never touches disk.** It is a refresh token for a live mailbox, so it goes through `services/token_store.py` into the OS keychain and nowhere else. There is no file fallback by design. A machine with no keychain gets a startup error naming what to install, and the plaintext `keyrings.alt` backends are refused, because a silent fallback would leak a long lived credential into a file the user does not know about. This project is open source, so that failure would land on other people rather than on us.
- **Question IDs never reach the model.** Questions are keyed by Gmail label ID so answers can be matched back without comparing name strings, but the model only sees the rendered `instructions`. `{{ label.name }}` has to appear there or every question renders identically. The server validates this at startup and refuses to run without it.
- **A missing `noul` is not `0.0`.** Model it as `float | None` on the wire so an absent value stays distinct from a real zero. Conflating them silently applies or skips labels.
- **Labeling is additive.** Only ever send `addLabelIds`. Never create a label and never remove one.
- **Dry run is the default.** Nothing is written to Gmail until the user presses `a` in the TUI or passes `--apply`.
- **`| tojson` in the templates is load bearing.** It emits a fully quoted and escaped JSON value, which is what stops an arbitrary email body from producing invalid JSON. That is why template values carry no surrounding quotes.
- **Retry only on 408, 429, and 5xx.** At most 2 retries, exponential backoff from 0.5s to 5s with 0.25 jitter, honoring `Retry-After` and `retry-after-ms`.
- **Parts of the Jev API are undocumented,** so do not code against the JSON error body shape (capture the raw body instead), the maximum number of questions per request, the request ID header name, or any `X-RateLimit-*` headers.

## Commands

Server, run from `server/`.

```
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
uvicorn jev_demo.main:app --reload
pytest
ruff format .
```

CLI, run from `cli/`.

```
go build ./...
go vet ./...
gofmt -l .
go test ./...
```

`script/setup` builds the server virtual environment and installs the CLI, and
`script/install` does just the CLI. Setup calls install rather than repeating
the build.
The command is named `jev`, not `jev-demo`. Go would name an installed binary
after the last element of the module path, which is `cli`, so the build always
passes an explicit `-o jev`.

## TUI style

Two colors total. Everything is monochrome except two things, which are amber.

Base text sets no color at all. Leaving the foreground and background unset means the terminal paints its own default, which stays legible in both light and dark profiles without any theme detection. An explicit black would break dark terminals and the reverse would break light ones.

Amber is used for the running token and cost totals and for the keybinding hints in the footer. Nothing else. It is `#FFB000` on dark and `#A35A00` on light, which in lipgloss v2 comes from the `LightDark` helper rather than the v1 `AdaptiveColor` type.

Everything else leans on weight, reverse video, and whitespace. Column headers are bold and underlined. Assigned labels are bracketed chips in reverse video, set on their own line under the thread rather than in a column, so a long list wraps instead of being cut. The dry run banner is bold in the base color. Errors are bold with a leading `!`. Snippets and timestamps use `Faint(true)`. The active row in the settings form is marked by a `>` in a two column gutter, with no highlight bar and no color change.

## Charm stack

The CLI uses Charm v2, meaning `charm.land/bubbletea/v2`, `charm.land/bubbles/v2`, and `charm.land/lipgloss/v2`. The module path moved off `github.com/charmbracelet/...` at v2, so v1 examples found online use both the old import path and a different API. Do not copy them.
