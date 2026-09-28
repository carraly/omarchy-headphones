# Working on Omaphones

Read this before changing anything. It is short because the details live in
the files it names.

## What this is

A plugin for the Omarchy bar: one icon per connected pair of headphones,
filling with the battery, and a panel that switches Off / ANC / Ambient.
`Service.qml` follows the connected devices, `DeviceFollower.qml` holds one
device — its Fast Pair battery reading and its mode bridge — `Panel.qml`
draws, `Model.js` decides (no QML in it, so it runs under Deno in the
tests). The battery comes from `gfps-reader` over Fast Pair; the listening
mode comes from one `<brand>-bridge` process per device, a Python script
that holds the device's own channel. `PROTOCOL.md` records what every
device was seen to say; `BRIDGE.md` is what every bridge promises the shell.

## Adapter API

For a new model, read [docs/ADAPTER-API.md](docs/ADAPTER-API.md).
Use `tools/add-device` in an isolated checkout: one package under `devices/`
contains exact identity, model parameters, capture, replay, fault tests, owner
checks and screenshot. Reuse a shared pure codec, or write local `protocol.py`.
Transport, timers, state and capture belong to the host in `omaphones/`.
`tools/add-device sync` generates the single registry and README section.

Drafts are inert. Active packages require reviewed replies and current owner
hardware/integration evidence. Existing supported models still use their
original bridges and retain every original pin and capture. The legacy workflows
below remain applicable to those models. New packages may not take their known
identities. A green replay does not constitute deployment or hardware approval.

## The fact that shapes everything

Nobody has more than their own headphones. The maintainer cannot test your
model, you cannot test anybody else's, and every model in README's table
works today on frames that only its owner can retest. Two rules follow, and
a pull request that breaks either is sent back:

1. **A new model may not change what an existing one is sent.** A model is
   a row — in `MODELS` in its bridge where the bridge has one, in `BACKENDS`
   in `Model.js` for a brand — and a pin file, `tests/pins/<brand>/<model>.json`,
   the frozen session of that owner's headphones. Adding yours adds a row
   and a file. It does not edit another owner's row or pin, and it does not
   turn something that was always sent into something now decided. Where a
   decision is unavoidable, widen: `UNKNOWN` gets the wider behaviour,
   known models keep theirs.
2. **Ship only what you saw the headphones answer.** No bytes from a vendor
   table your headset never answered — not in the code, not in
   `PROTOCOL.md`. Keep the probe's output as `docs/captures/<brand>-<model>.txt`
   and name it from your pin (`"capture": ...`); a frame the bridge parses
   should be in there.

## Canonical owner examples

JBL TUNE230NC TWS and Sony WH-CH720N are the canonical reference models,
prepared and hardware-tested by the maintainer @ncr on his own headphones.
Read `docs/CANONICAL-TESTS.md` before adding support. Their `*-canonical.json`
pins, packet evidence, bridge fault tests, Fast Pair tests and live check show
the expected coverage. Match coverage for the capabilities your model has;
use its own replies, never borrowed bytes. Keep original pins intact and label
synthetic damage separately from observed protocol evidence. Other owners'
pins remain equally binding. A capability not tested is documented as such.

The expanded coverage requirement applies to new models and brands only.
Existing supported models keep their current tests and evidence; owners do
not have to fill historical gaps to remain supported. A change to an existing
model must test the changed behaviour and be confirmed by its owner, without
requiring a complete coverage retrofit. Existing pins remain binding.

## One command

```bash
tools/check
```

Runs everything a pull request is held to: the bridge tests and pins, the
`Model.js` tests, "no pin edited or removed since main", the grep for
install-command words, README's gallery against `docs/gallery/`,
`manifest.json`, `qmllint` and `omarchy plugin validate` where the shell is.
CI runs the same script on every pull request. Run it until it passes; a
skipped line names the tool this machine lacks.

## Adding a model to a brand that has a bridge

1. Find what the device answers with the brand's probe in `tools/`
   (`sony_probe.py`, `soundcore_probe.py`, …). Turn `useModeControl` off
   first, or the running bridge holds the channel.
2. If the bridge has a `MODELS` table, add a row keyed by what the row
   comment says (Sony: the reported name; Soundcore: the vendor UUID
   suffix). Do not edit another row.
3. Add `tests/pins/<brand>/<model>.json` — copy a sibling, replace the
   frames with yours, name yourself as `owner`. The format is the docstring
   of `tests/harness.py`.
4. `PROTOCOL.md`: a subsection under the brand with what the device
   answered. `docs/captures/`: the probe output.
5. `README.md`: a row in the table, a gallery cell with the screenshot
   (`tools/gallery-shot`, see `.claude/skills/gallery-screenshot/`).
6. `tools/check`.

## Adding a brand

1. Find the channel and the frames: `PROTOCOL.md` says how each existing
   one was found; the probes in `tools/` are the pattern.
2. Write `<brand>-bridge` to `BRIDGE.md`. `sony-bridge` (D-Bus Profile1,
   the most complete) or `samsung-bridge` (the shortest) is the one to copy.
3. Add its row to `BACKENDS` in `Model.js` — what it claims, the file, the
   arguments, the Ambient row's shape. Put the row where its claim cannot
   take another brand's device; `tests/model.test.js` pins that for the
   devices that work today, and needs your device's full UUID list added.
   Modes beyond Off / ANC / Ambient / TalkThru go in the row's `extraModes`,
   and names the device's own app uses in its `modeLabels` (the `tozo` row
   is the example) — not in a check on the brand's name in `Model.js`.
4. `tests/<brand>_bridge_test.py` on `tests/harness.py` — a Session that
   captures the bridge's writes and says what "device" and "sent" mean for
   this protocol — and the first pin under `tests/pins/<brand>/`.
5. `PROTOCOL.md` section, `docs/captures/` file, README row, gallery cell,
   `manifest.json` aliases and description.
6. `tools/check`.

## Never

- Edit or delete a pin that is not yours. `tools/check` fails on it and CI
  comments on the pull request naming the owner.
- Add an install command, a package name to install, or a dependency
  Omarchy does not ship — not in code, not in README, not in a comment.
  The marketplace scanner greps for the words; the shell's own services
  (`Quickshell.Services.Mpris`, `.Bluetooth`, `.Notifications`) are there
  for what a binary would otherwise do.
- Write into the plugin directory while the shell runs it: every file
  written there reloads the plugin. Work in a clone or a worktree, as the
  tools in `tools/` do.
- Change what applies to every device or every user (a new default-on
  setting, a new action on disconnect) inside a pull request about one
  model. Say it separately; the maintainer decides it.

## Where things are

| | |
|:--|:--|
| `Model.js` | the decisions: device picking, `BACKENDS`, parsing, formatting. Deno tests in `tests/model.test.js` |
| `Service.qml` | the followed devices, the Fast Pair reader, parking and backoff, the IPC methods |
| `DeviceFollower.qml` | one device: reading, bridge process, the state the panel reads |
| `Panel.qml` | the panel |
| `<brand>-bridge` | one process per protocol; `BRIDGE.md` is their contract |
| `gfps-reader` | the Fast Pair Message Stream reader, one for every device |
| `tests/harness.py`, `tests/pins/` | how a bridge is tested; the frozen sessions |
| `tools/check` | the one list |
| `tools/*_probe.py` | how a protocol is read off a device |
| `PROTOCOL.md` | what every device said, and how it was found |
| `docs/captures/` | the raw evidence behind a pin |
| `.agents/skills/land-pr/` | shared PR review and landing workflow; Claude entry point refers here |

For a guided contribution, use `/add-new-bridge` or read
[the shared skill](.agents/skills/add-new-bridge/SKILL.md) before probing or implementing.
