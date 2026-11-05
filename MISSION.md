# Mission: Reading OpenBook's data flow

## Why
I own OpenBook and keep hitting performance surprises (API-call bursts on save). I want to read the app's backend traffic and internal flow well enough to diagnose what is happening, judge whether it's worth fixing, and fix it correctly.

## Success looks like
- Given a burst of backend log lines, I can name the code path that produced it
- I can predict the API calls any UI action will make, before making it
- I can decide which call bursts are by-design (undo snapshots) and which are bugs

## Constraints
- Learning is grounded in this codebase — the code is the primary source
- Short sessions; I review lessons between real work on the app

## Out of scope
- General web-dev refresher material (I already work in this stack)
- The audio/ASR pipeline, except where it touches the save flow
