# Build Prompt for Claude Fable 5

Paste this as the opening instruction in a fresh Claude Code session with Fable selected, run from an empty working directory with network and bash access.

---

## Mission

Build a complete, working, tested job-search automation repo for me. It is a merge of two things:

1. **santifer/career-ops** (https://github.com/santifer/career-ops) — clone it as the base. It provides portal scanning (Greenhouse/Ashby/Lever APIs), CV-aware scoring, ATS-optimized PDF generation, and a tracker.
2. **My original design** — a lightweight Python watcher: GitHub Actions cron every 6 hours, `seen.json` committed back to the repo for dedup, Discord webhook notifications, and a hard rule against scraping Indeed or LinkedIn directly.

Do not just install career-ops as-is. Merge it with my constraints below, strip what I don't want, and tailor it to my actual job search (UK placement year, CS, September 2026 start).

Do not stop and report back until the full repo is built, wired together, and you have run it end to end against test/fixture data and confirmed it works. I want a repo I can start using immediately, not a partially wired scaffold.

## Hard constraints (never violate, no exceptions)

- **No auto-apply.** Delete or fully disable `modes/apply.md` and any Playwright form-filling capability from career-ops. It must not exist as a callable mode in the final repo, not just be "gated by a prompt."
- **No direct scraping of Indeed or LinkedIn.** Portal scanning only against public ATS APIs (Greenhouse, Ashby, Lever) and company career pages that allow it. If Indeed/LinkedIn alerts are wanted later, the correct path is Gmail API parsing of their email alerts, not scraping their sites. Do not build that now unless I ask.
- **Nothing gets submitted anywhere automatically.** The bot notifies and scores. I decide and act.
- **My CV, profile, and personal data stay local.** Only send what's needed for scoring/generation to the Anthropic API. No third-party analytics, no telemetry, no other outbound endpoints. Audit `batch-runner.sh` and any Go dashboard code you keep for anything that phones home, and remove it if found.
- **Keep it auditable.** I should be able to read every file in this repo and understand what it does. Don't leave in career-ops modes/scripts I haven't asked for just because they came with the clone — if it's not part of this spec, remove it.

## My profile 

- CS placement-year student, transferring into University of Kent, BSc Computer Science (Hons) with a Year in Industry, Stage 2, starting 28 September 2026.
- Target roles: cast a reasonably wide net — backend, full-stack, DevOps, AI/ML, and general software engineering. Software placement-year and internship-adjacent listings, UK-based or remote-eligible for a UK-based Year in Industry placement. I'm not fully set on a specialization yet, so scoring should favor breadth of eligible roles over narrow keyword matching, as long as I plausibly qualify. Not senior or full-time roles.
- Relevant experience to weight scoring against: C#/.NET 8 (CubeStone Consulting, Fleet Management API, SAP HANA, Dapper), Next.js/React (Imatic Technologies insurtech dashboard), Go, Python, general full-stack.
- CV lives at `cv.md` in Harvard format. Use my actual CV content (I'll paste it into the repo, or you generate `cv.md` from what's described here plus ask me once for the file if you don't have it — do not fabricate metrics or experience).
- Don't refer to any personal relationship in generated content.

## Architecture

Career-ops pieces worth keeping and adapting into `src/`: the ATS API scanning logic and the PDF CV generation approach (only invoked manually via `tailor.py` or a `pdf` step, never on cron). Everything else from career-ops (Go dashboard, multi-mode slash command system, apply mode) gets dropped unless it directly serves the pieces above — if you think something else is worth keeping, tell me what and why when you report back, don't just include it.

## CLAUDE.md skeleton (fill this in for real once the repo exists)

```markdown
# career-bot

What this is: a personal job-search scanner and scorer, not a job board or an auto-apply tool.

## Constraints (do not change without explicit instruction)
- No auto-apply, ever.
- No direct scraping of Indeed or LinkedIn.
- Discord notification only above the score threshold set in config/profile.yml.
- tailor.py and pdf generation are manual-only, never on cron.

## Architecture
[link to the tree above, kept in sync]

## How to run
[setup steps, env vars needed: DISCORD_WEBHOOK_URL, ANTHROPIC_API_KEY]

## Profile context
[owner's target roles, UK placement year focus, Kent Sept 2026 start — kept in sync with config/profile.yml]
```

## Sub-agent orchestration

Use Claude Code's sub-agent capability to split the build so you're not burning top-tier compute on mechanical work. Suggested split:

- **Sonnet-tier sub-agent(s):** the actual scan/score/notify logic, the GitHub Actions workflow, wiring career-ops' ATS API code into `scan.py`, writing the test fixtures and test harness.
- **Haiku-tier sub-agent(s):** boilerplate — `portals.yml` population from career-ops' existing 45+ company list (filtered down to ones relevant to backend/full-stack/UK placement roles), README generation, `.gitignore`, config file scaffolding, docstrings/comments pass.
- **You (Fable), directly:** the merge decisions — what to keep from career-ops vs. drop, the scoring prompt design (what goes into the Anthropic API call in `score.py` so it correctly weights my CV against a JD), and the final end-to-end test run and verification. Don't delegate the constraint-enforcement checks (no apply mode, no LinkedIn/Indeed scraping, no telemetry) — verify those yourself directly by reading the final files.

## Build and test sequence

1. Clone career-ops. Read its structure. Decide what's being kept per the architecture above.
2. Scaffold the new repo structure.
3. Build `scan.py` against 2-3 real public Greenhouse/Ashby/Lever endpoints as a smoke test (read-only, no side effects) to confirm the API approach actually works before wiring the rest.
4. Build `score.py`, `notify.py`, `seen.py`. Write fixture job listings in `tests/fixtures/` covering: a strong match, a weak match, and a duplicate (already in seen.json) — confirm scoring and dedup both behave correctly against these.
5. Build `tailor.py` as a manual CLI command, confirm it generates output without touching anything automated.
6. Write the GitHub Actions workflow. Do a dry run (you can simulate the cron trigger locally or via `workflow_dispatch`) and confirm the full pipeline runs and `seen.json` gets updated correctly.
7. Delete/confirm-absent: `apply.md`, any Playwright form-filling, any LinkedIn/Indeed scraping code, any telemetry/analytics calls.
8. Write `CLAUDE.md` and `README.md` for real, based on what you actually built, not the skeleton above verbatim.
9. Only after all of the above passes: report back with what you built, what you kept vs. dropped from career-ops and why, what still needs my input (API keys, Discord webhook URL, my actual `cv.md` if you didn't have it), and how to run it for the first time.

Do not report back with a partially working repo or a plan. I want the finished, tested thing.