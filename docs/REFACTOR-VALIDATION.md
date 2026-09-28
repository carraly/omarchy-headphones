# Adapter refactor candidate — local validation

Branch: `architecture-v2` (started as `codex/adapter-session-evidence`).

Built from the combined adapter branch at `d1d6e80` and current Omaphones 1.3.3
at `f0f8006`. The merge retains the refactor tests and the WH-CH520 owner test.

## Sony Focus on voice timeout explained (2026-09-28)

The two timeouts on `ambient.focus_on_voice=false` in ANC came from the owner
check's order, not from the Sony codec, which sends the same frame as
`sony-bridge`. `live.run` puts cases that match the initial state last, so from
ANC with voice off it sent voice on (in Ambient, after the level cases), then
ANC, then voice off in ANC. Reproduced on the maintainer's WH-CH720N through the
widget's own IPC (installed `sony-bridge`, no settings changed): from Off, voice
off, level 7 — Ambient, voice on, ANC, then voice off was answered `ok` but the
headset kept reporting voice on, in ANC and again after returning to Ambient.
Restored to Off, voice off, level 7. Restoration had passed in both sessions
because it writes the level first, which is an Ambient SET.

`live.ambient_prerequisite` now sends `noise.mode=ambient` before any
`ambient.*` case on a device that offers Ambient, recorded as a separate
`prerequisite:<case>` check; the panel offers these controls only in Ambient.
`tests/live_ambient_order_test.py` replays the order on a synthetic device that
ignores voice outside Ambient; the old code times out on it. Not yet rerun
through `tools/test-refactor` on hardware.

## Merged 1.3.12 (2026-09-28)

`64c57fa` merged main at `e8546c0` (1.3.12, TOZO NC9 Pro from #23). Conflicts
in `Model.js` (generated registry, `controlBackend`, the adapter API
functions next to main's `modeOptions`) and `DeviceFollower.qml` (the
four-argument `controlBackend`). TOZO is `adapters/tozo/adapter.json`, a
legacy-only package with no protocol entry: it runs `tozo-bridge` unchanged,
claimed by the exact reported name plus an observed UUID (`modelNames`,
`modelUuids`), at priority 80 before the JBL BLE fallback. The registry now
passes a legacy row's `extraModes` and `modeLabels` to the shell, so the
generated row equals main's. `registry.select()` does not take a name and
never returns `tozo`; only the shell routes it. No native TOZO codec exists.

`CHECK_BASE=origin/main tools/check` passes (382 Python tests). Bridges, the
Fast Pair reader, pins and captures are byte for byte main's.

## Rebased onto 1.3.11 (2026-09-25)

`architecture-v2` merged main at `c0d92fc` (1.3.11) in `87e38f7`. Two
conflicts: `DeviceFollower.qml` keeps `ancBackoffKey` and adds main's
`cycleChannel` on a transient JBL exit; `tests/model.test.js` keeps both
sides' tests. After the merge the migration and adapter tests caught four
places where the adapters did not know what main had added since 1.3.3. Each
is ported in its own commit, copying the bridge's row or frames:

- `f19facb` Nothing: CMF Buds 2 / Buds 2 on RFCOMM channel 16 (#14).
- `42d9cbd` Soundcore: Life Q30 (`b302a`, offset 35, four-byte block, #18);
  the codec gained `width` and reports the mode alone from four bytes. New
  codec test on the owner's frozen frames.
- `2f5d73c` Sony native codec: WH-1000XM4 ambient as ncValue 0 with effect
  0x11, and the last reported ambient level on a switch back (#21).
- `d8ce83e` JBL: Wave Buds 2 handles 0xa205 / 0xa202 by Fast Pair model id
  (#17), as model transport fields; the replay takes the pin's `model_id`.

Bose QC45 / QC35 (#13, #22) need no port: Bose runs through its bridge on this
branch and no adapter test replays its pins natively.

`CHECK_BASE=origin/main tools/check` passes (354 Python tests, adapter API and
packages, generated registry, device evidence, Model.js, pins unchanged, QML
lint, plugin validation). Bridges, the Fast Pair reader, pins and captures are
byte for byte main's (`git diff --exit-code origin/main -- '*-bridge'
gfps-reader tests/pins docs/captures`). None of the four ports was run on
hardware: they are replay parity with the bridges, not owner evidence. The
hardware status below is unchanged and refers to the pre-rebase candidates.

## Implemented

- Shared adapter runtime and per-model contribution workflow from the combined
  branch, integrated with 1.3.3.
- Sony WH-CH520's battery-only exclusion in the native codec, without changing
  its original owner pin. Nothing's original model-specific RFCOMM channels and
  Soundcore's observed UUID, offset and query policy in the migration host.
- Independent BTSnoop session archives, an action/observation CSV timeline,
  source checksums and validation, and explicit packet-slice provenance for
  derived replay bytes. Source RX/TX slices cannot be reused or reordered within
  a direction. Original historical owner evidence stays unchanged.
- An explicit `tools/test-refactor` owner workflow for the new adapter host,
  with revision fingerprint, control results, failure logging and restoration.
- Contributor instructions and a reusable GitHub message/agent prompt.
- Structured owner interviews through terminal, JSON lines and an optional local
  browser panel. Initial Ready starts the first control; saving an observation
  starts the displayed next step without another Ready. Save & pause waits for
  Resume; Save & repeat restores a baseline and repeats after two seconds; an ordered plan shows the current and next test. Questions/answers are inside
  the existing source CSV checksum boundary.
- Explicit Pause, Skip, Stop and bounded owner waits; adapter restoration still
  runs when interview logging fails. The assistant remains responsible for its
  separate btmon process and temporary plugin setting.

## Verified locally

`CHECK_BASE=upstream-baseline tools/check` passed:

- 282 Python tests, including transport callbacks, original pins, native codecs,
  source-file corruption/loss, provenance and interrupted control restoration.
- Model.js tests (65 declarations), generated registry/package checks, QML lint
  and Omarchy plugin validation.
- Manifest remains at version 1.3.3; no version bump.
- Original bridges, Fast Pair reader, pins and captures match `f0f8006` byte for
  byte (`git diff --exit-code upstream-baseline -- '*-bridge' gfps-reader
  tests/pins docs/captures`).
- BlueZ btmon 5.87 read a synthetic BTSnoop format fixture as an HCI Reset
  command and Command Complete event with the expected 1 ms interval. This was
  file-format validation, not traffic sent to headphones.

## Hardware and publication status

The independent Astra-low rehearsal on candidate `d9f1b646` confirmed audible
Sony mode changes and return to initial ANC. It also reproduced a timeout when
setting voice focus false in ANC (explained and fixed in the check on
2026-09-28, above), and sealing rejected backward timestamps in
the original BTSnoop captures. Both failures and all original bytes are retained.
The JBL run on `faf94d4` failed before its first owner question. Its independent
capture contains a mode Off notification (packet 8560), but the native GATT
parser discarded BlueZ's `N bytes` header because it required `N data bytes`.
The parser now accepts both spellings; a regression test reproduces the old
failure using that observed reply and a labelled text fixture. The subsequent JBL run on `42a560c` passed adapter control checks, external
mode-change reporting and restoration to Off. Owner uncertainty is retained;
the later TalkThru comment describes a subtle difference rather than a loudness
change, exposing the need for a Different choice. Sealing still rejected a
backward timestamp at packet 3060; original bytes remain intact. No overall
migration or archive-validation pass is claimed.

The new interview was verified with synthetic devices and real stdin/stdout
pipes. Browser QA covered Ready, an uncertain observation with comment, Repeat,
Stop/restoration, and a completed comparison. Refresh preserved the pending
question. The combined save/start path was also verified in the browser: the
next comparison opens directly, Repeat runs without Ready, and Save & pause
waits for Resume. These UI exercises used no Bluetooth or plugin-setting operations.
The new guided path still needs an owner hardware rehearsal; it does not fix
or mask the two earlier Sony-session failures. General acoustic performance,
reconnect, battery, wear, charging, peer isolation and candidate QML integration
remain separate hardware checks.

Existing models continue to use their original bridges in normal plugin
routing. The explicit test runner exercises the native codecs without activating
them in the installed plugin. Migration/release decisions follow returned owner
evidence. The GitHub message is a local draft; substitute a published candidate
SHA and documentation URL before sending. No contributor messages, push,
release or article publication were performed as part of this validation.
