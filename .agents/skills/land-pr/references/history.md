# Decisions behind land-pr

Derived on 2026-09-12 from ten PR-review conversation threads covering PRs
#1 through #12, plus the canonical-test/release thread. Original messages
were checked alongside local Git history, current project instructions and
the old Claude skill. These are historical decisions, not live PR status.
No raw private transcript is included.

PRs: `https://github.com/ncr/omarchy-headphones/pull/<number>`.

| Review | Lesson retained |
| --- | --- |
| #1 Xiaomi; first #2 Nothing review | Resolve manifest conflicts using current descriptions and retain credit. The maintainer explicitly deferred Xiaomi's missing screenshot; that was a specific exception. Nothing's keyboard handling and repeating UUID probe needed review despite passing tests. |
| #3 Soundcore; second #2 review | Integrate into the current shared bridge architecture and preserve per-brand ambient ranges/labels. Nothing was implemented separately; useful evidence and an image from #2 were credited without importing obsolete code. Do not label a superseded PR merged without evidence. |
| #4 Sony XM5 | Startup ordering also exposed a separate Fast Pair reader race. A guessed delay for a daemon crash proved ineffective and was reverted. Diagnose before broadening a PR. A failed revert followed by a version/push sequence showed why release steps must stop on failure. |
| #5 Soundcore One Pro | The user rejected new queries to existing Space 2 merely because the risk seemed low. Model rows preserved both startup and delayed confirmation behavior; new/unknown models received wider discovery. Synthetic historical tests had explicit limits. |
| #6 Sony XM4 | Handshake assumptions, parser acceptance and unobserved variants endangered existing Sony support. Old/new replay comparison and a CH720N live test distinguished the behaviors. Later audit found a missing version bump and stale probe/docs. The user repeatedly shortened the public thank-you. |
| #7 Sony XM6 and #8 Galaxy Buds2 | A new wear query went to all Sony devices and existing tests were adjusted to fit. Repairs froze older rows, used the shell media service and called out a default affecting everyone. Samsung's unobserved alternate status layout was removed and silence/parking corrected. The user chose patch-only bumps and requested the original skill. |
| #9 Collexion/gallery | Claims were limited to visible controls; a screenshot did not establish hidden controls or wear behavior. The user requested the full, short public comment independently of merge approval. |
| #10 Collexion image update | A stale branch restored old image names and an earlier version. Only the new image and supported claims were retained against current main. No shell restart was needed for documentation, and unrelated development work stayed intact. |
| #11 OPPO | Review covered existing bridge isolation, backend priority and parser/lifecycle tests. No CI runs were attached at that time; this was disclosed. The user prompted a README/manifest audit and version bump. The approved comment was brief and warm. |
| Canonical tests / 1.3.0 | Owner tests are substantively immutable. Expanded real-capture, UUID, fault and live coverage applies to new models; existing models were grandfathered. Harness semantics still need review. Mode persistence across refresh does not establish fresh battery telemetry. Preserve settings and bar placement. |
| #12 CMF / 1.3.1 | Local checks passed while shared channel fallback changed legacy Nothing retries. Per-model repairs and full captured-frame tests preserved original pins. CI-only socket constants were fixed in test doubles. The author tested repaired head 22497ef before landing, confirming controls/reconnect/restoration but not peer isolation, charging or acoustic effects. A cropped owner image was retained with an honest description. |

## Added 2026-09-21

From the session that handled #16, #18, #19 and #21 together. Same caveat:
decisions then, not live status.

| Review | Lesson retained |
| --- | --- |
| #18 Soundcore Life Q30 / 1.3.6 | GitHub does not run the `check` workflow on a head that conflicts with main, so a green `pin owners` alone said nothing; the merged tree was checked locally. The pin's frames came from an earlier session than the capture file and differed in two bytes (battery, noise-cancelling grade); that was accepted and written into the merge message rather than reconstructed. The owner's hardware test stood because the later commit only removed an explicit default. Review added a test the contribution lacked: commands for controls the model does not have must write nothing. |
| #19 JBL channel reopen / 1.3.7, 1.3.9 | A change for every JBL model, inside a follow-up about one. The author's post-change evidence did not show the fix working: five reconnects kept one address, and eight reopens re-announced a dead one. The maintainer tested on the TUNE230NC instead: killing the bridge's `btgatt-client` forces exit 1, and the reader's socket inodes in `/proc/<pid>/fd` show whether the channel was reopened. Ten trials, with music and PipeWire's error count, went into `docs/captures/`. An unexplained shell restart during one trial was recorded, not dropped. The side effect found in review (exit 1 cancelling the reader's backoff) was fixed separately, measured the same way, and released on its own. |
| #21 Sony WH-1000XM4 ambient | A second owner of the same model edited the first owner's pin, whose own notes say it was not recorded on hardware. The pin check stays red by design; the first owner was asked to test, and whether a second owner's confirmation is enough is the maintainer's decision, not the reviewer's. `action_required` on `check` needs the maintainer's click and is not a result. A usability repair (ambient level after ANC) used only frames already in the owner's capture and went to the contributor's branch as one `Review:` commit for the owner to test; it was not run on hardware by the reviewer. |
| #16 Soundcore R60i NC | A claim in the description (ambient level 1-5 works) had no frame behind it: the capture's level byte was 0 throughout. Asking for one named byte got further than asking for "more captures". The probe the owner was about to rerun wrote fixed bytes after the mode; it was fixed first (1.3.8) so that gathering evidence could not overwrite the owner's settings. |

## Source index

Conversation IDs locate original Claude project records or Codex archived
rollouts without embedding local paths or unrelated conversation content.
The #12 archive includes the later September 11 completion, superseding an
older summary that still called it pending.

| Thread | Conversation ID | Corroborating commits |
| --- | --- | --- |
| #1 / first #2 | `13cde884-122b-4f94-a278-a80a4a002951` | `1c39e9e` |
| #3 / second #2 | `5daf90a8-5f47-4420-88d5-2ecf61065905` | `d4fedbc`, `b903188` |
| #4 | `a7ce2db1-5b9e-4b5b-a965-49f3a6fa7835` | `809f5de`, `3dbe468` |
| #5 | `278f3e0f-d2e4-487c-9be4-27e5599d6059` | `deaffa2`, `f93b543` |
| #6 | `7c524436-59d5-4ee5-b6ef-dcb97ba2bc76` | `7730f43`, `7efc4b8` |
| #7 / #8; original skill | `26d49b3c-0f68-4efa-a607-6b44728f794e` | `e9e9fbf`, `eb2aa96`, `a65b9eb` |
| #9 | `0b97b4d4-810e-439c-854c-efb299b01aea` | `e8a016d`, `eb9e8ae` |
| #10 | `7bc2fa81-ad15-42e1-91d1-7dae138bb667` | `467c575`, `71f928f` |
| #11 | `01a07ff7-9890-7771-a751-8d17bdc68c00` | `4416725`, `7873436` |
| Canonical tests | `01a08019-5657-7e03-a1c4-4e26114b9cb9` | `033c2ca`, `63a7704` |
| #12 | `01a086e2-51d4-7062-963b-b045ee5d0298` | `acadbc9`, `22497ef`, `6bf8460`, `417985c` |
| #16, #18, #19, #21 | `72dffa6e-572a-4798-b972-bb7ef5ed0825` | `70f3094`, `06bd3fb`, `2b4ac59`, `ad321c4` |

## Deliberate updates to the old skill

- Repair permission follows session instructions. The old repair-before-report
  sequence cannot override a request to approve scope first.
- Remember approval already given; do not ask again for the same action.
- Validate repaired hardware behavior on the repaired revision before landing.
  Earlier speculative merges were specific decisions under older requirements.
- Prepare merge/version changes outside the live plugin; deployment is separate.
- Inspect actual CI; never assume a workflow passed or that absence means success.
- Explicit patch bumps and remote checks prevent forgotten/incomplete releases.
- Keep public thanks brief; protocol detail belongs in the internal review.
- Do not inherit fabricated agent/session trailers, automatic monitoring,
  marketplace posts, GitHub Releases or mandatory subagents from old sessions.
