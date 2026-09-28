# Owner interview protocol v1

The same runner supports a local browser panel, a terminal, and JSON lines.
No AI-provider SDK or browser automation is required to drive the JSON path.
[JSON Schema](schemas/owner-interview-v1.json) defines questions and answers.
Other status events and the final result have a `type` field as shown below.
The panel shows the current test, the next test and an expandable session plan.
That plan comes from the same ordered controls the runner actually executes;
skipped, failed and unrun steps retain their distinct statuses.

## Start

Use `tools/test-refactor --adapter sony --session SESSION --interview web`
for the local panel, or choose `terminal` or `json`. The usual independent
capture and device preparation in OWNER-TESTING.md are still required.
`tools/owner-interview-demo --interview web` exercises a short simulated
comparison without Bluetooth, device settings or persistent owner evidence.

The browser is ephemeral, binds only to loopback on a random port, and requires
its random session token for data and responses. It serves no session files.
Refreshing the page preserves the token within that tab's session storage;
it is cleared at completion. Closing the page does not submit an answer.
After a grace period the runner ends the interview and restores device state.

## JSON relay

Stdout contains one JSON object per line. Stderr contains diagnostics.
Keep the process alive while the owner responds. Handle these event types:

- `interview-status`: display its `message` where useful. The restoration phase
  announces settings changes that are not another test. The final status lists
  `cleanupRequired`: stop capture and restore the previous plugin setting.
- `owner-question`: display `question` and `choices` for its `device`.
  `context.progress` names the current/next step and the ordered plan. Status
  events also carry `progress` during actions, baseline resets and restoration. Forward
  the original ids in the answer. `context` contains the baseline and control;
  an observation question also references the action/reply timeline entries.
- `input-error`: an answer was rejected; the same question is still pending.
- `test-result`: `report` is the adapter result, including protocol checks,
  restoration and individual `ownerObservations`.

Example answer (copy ids from the actual pending question):

```json
{"type":"owner-answer","version":1,"sessionId":"COPY_SESSION_ID","requestId":"COPY_REQUEST_ID","answer":"unsure","text":"I was distracted","channel":"assistant-relay"}
```

These seven fields are required. A repeatable observation may additionally
include `"next":"repeat"` to save the answer and repeat the comparison. Omit
`next` (or use `"continue"`) to save and start the next displayed step. Use
`"next":"pause"` to save and wait for Resume instead. The pending question
advertises these options with `context.repeatAllowed` and identifies the next
step in `context.startsNext`. There is no separate decision question. `text` may be empty for choices such as Ready
or Unsure. Done requires a description of the actual physical action; Observed
requires the actual observation. Comments are limited to 4000 characters.
The runner rejects unknown choices, stale ids and a channel mismatch. It assigns
reception timestamps itself. The channel records that the answer was relayed;
it does not cryptographically prove human authorship. No default answer is sent.

## Step behavior and evidence

Ready → one control → save observation and start the next displayed step.
Ready is required at the start. **Save & start next** records the answer and
starts the next step without another Ready question. At the final comparison,
**Save & finish** proceeds to restoration. **Save & pause** records the answer
and waits; Resume starts the next step directly. Ready, Pause and Resume act
immediately without a second submit click.
**Save & repeat** keeps the observation, restores the comparison baseline,
allows two seconds to listen, then repeats automatically. Stop remains available.
Skip on a readiness question leaves that check incomplete. In the terminal,
`quieter | comment` saves and starts the next step, `repeat:unsure | comment`
saves and repeats, and `pause:quieter | comment` saves and pauses. JSON uses
the optional `next` field above.
The first attempt uses the current reported state as its baseline. If the state
changes while waiting, the runner asks again with the new baseline. A physical
step separately records instruction, completion and independent observation.

Every question and answer is a JSON object inside the description column of
`actions.csv`. Question publication and readiness are notes, never claimed
physical actions. The outer CSV supplies its existing ordered timestamps; the
JSON carries ids, attempt, phase and receipt clock values. An observation keeps
its original comment. It cannot be rewritten by a later attempt. Schema v1
covers these question/answer objects; generic status/action records retain their
own types. The existing seal protects the entire CSV, with no sidecar loophole.

Listening comparisons include **Different** for a change that is neither quieter
nor louder. Physical completion and independent observation both offer **Skip**;
a skipped check remains incomplete. **No change** records an actual observation
without requiring a comment. It does not skip protocol verification or turn a
missing device reply into success. Done and Observed still require a description.

UI answers are distinct from adapter reports. Unsure is inconclusive. Skipped
checks make the run incomplete. Protocol `passed` never grants acoustic approval;
listen comparisons are reported individually and general acoustic performance
remains untested. A command failure goes directly to restoration without another
required question. It stays a failure regardless of earlier owner observations.

## Boundaries

The runner restores and closes its adapter on Stop, failed command, input loss
or owner timeout. The assistant owns btmon and temporary plugin settings and
must clean those up on every exit. Keep btmon in a persistent foreground PTY
and stop it with Ctrl+C through its retained terminal handle; do not launch a
second privileged command to stop it. See OWNER-TESTING.md for the lifecycle.
The panel cannot manage arbitrary processes
or change desktop settings. It prints no install instructions and needs no
additional dependency.

The previous Sony voice-focus timeout was the check's order, not the codec:
Focus on voice was written in ANC, where the headset acknowledges and ignores
it. Ambient-only controls now get an Ambient write first, reported as a
`prerequisite:` check (see REFACTOR-VALIDATION.md). Strict capture timestamp
rejection remains tracked separately. Neither change modifies their packets,
old pins, recordings or failed outcomes.
