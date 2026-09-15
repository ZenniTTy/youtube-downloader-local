# Security Policy

## Scope

This app is designed to run **only on the machine of whoever starts it**. The
server refuses any request that does not come from loopback (`127.0.0.1`,
`::1`), and `test_loopback.py` verifies that on every push.

That lock is the security model. There is no authentication, no rate limiting
and no session handling, and none of it is planned — see
[CLAUDE.md](CLAUDE.md) for why.

## What counts as a vulnerability here

Anything that lets a request from outside the machine reach the app, or that
lets a crafted input escape the intended behavior. For example:

- A way to bypass the loopback check (header spoofing, proxy handling, a route
  registered outside the middleware).
- Command injection through a URL, filename or transcription parameter reaching
  `yt-dlp` or `ffmpeg`.
- Path traversal that writes or reads files outside the expected directory.

## What does not count

- **Exposing the app on the internet on purpose** (reverse proxy, tunnel,
  binding to `0.0.0.0`). The project states that this is unsupported and unsafe;
  doing it anyway is a configuration choice, not a flaw in the code.
- The absence of authentication or rate limiting. That is documented, deliberate
  and out of scope.
- Vulnerabilities in `yt-dlp`, `ffmpeg` or `faster-whisper` themselves. Report
  those upstream; if a fix requires a change here, an issue is fine.

## Reporting

Use GitHub's private reporting: **Security → Report a vulnerability** on this
repository. Please do not open a public issue for something that lets an
attacker reach the app from outside.

Include the version or commit, your OS and Python version, and the steps to
reproduce.

This is a personal project with no support commitment or SLA. I will look at
reports as time allows and credit reporters in the fix unless asked otherwise.

## Supported versions

Only the latest commit on `main` receives fixes. There are no maintenance
branches.
