# Controller run-through

A guided session on a Mac with a real controller (written for the 8BitDo Micro) to record how Anki sees it and to try the fixes on the `fix/micro-axis-input` branch. It takes about 20 minutes.

## Before you start

On this Mac, build the two packages (they land in `dist/`, which Git ignores):

```bash
uv run --no-project build.py
```

Copy `dist/contanki-diagnostics.ankiaddon` and `dist/contanki-test.ankiaddon` to the other Mac (AirDrop is fine). On the other Mac, note the Anki version (Help → About) and the macOS version.

## Part A: record the controller as it is now

1. In Anki: Tools → Add-ons → Install from file… → choose `contanki-diagnostics.ankiaddon`. Restart Anki.
2. Connect the controller and press a button.
3. Tools → Controller Diagnostics. Follow the window:
   - Step 1: press L2 and R2 once each, then leave the controller untouched for a few seconds. (macOS reports a trigger only after its first press after connecting or switching mode; version 1.0 of the tool missed this and timed out on every step after L2.)
   - Step 2: press and hold each named input, then let go. Use "Skip this one" for anything the controller doesn't have.
   - Step 3: tap A ten times, then D-pad Right ten times, as you would when studying.
   - Step 4 runs by itself.
   - Step 5: write which mode the controller is in and anything that misbehaves, then save.
4. The report is saved to the Desktop as `contanki-diagnostics-<date>-<time>.json`.
5. Repeat for each way you use the controller, closing and reopening the window each time and saying which in the notes:
   - each mode switch position you use (for example D and S);
   - the D-pad set to act as a D-pad, the left stick and the right stick (on the Micro, hold D-pad Left + Minus to switch between them). The test build handles all three.

Keep Contanki enabled during Part A: the report then also records how the installed Contanki identifies the controller and which profile it is using.

## Part B: try the test build

1. Tools → Add-ons: select "Contanki - Controller Support" (the AnkiWeb version) and click Toggle Enabled to disable it. Two copies running at once would both act on every button press.
2. Install from file… → `contanki-test.ankiaddon`. Restart Anki. Its version shows as "v1.0.2 + Micro fixes (…)".
3. Your profiles do not carry over automatically. To keep them, select the old add-on, click View Files, and copy `user_files/profiles/*` into the new add-on's `user_files/profiles/` (View Files on the new one), then restart.
4. Set up the test cards for the scrolling and Click rows (about five minutes; see "Test cards" below).
5. Work through the checklist, noting anything that doesn't match:

| Try | Expected |
| --- | --- |
| Tools → Controller Options, press each D-pad direction | Each lights up once while held and goes out on release; nothing stays lit (issue 154 on macOS 26) |
| Press the same D-pad direction twice in a row on a card | Both presses act (issue 163) |
| Bind a D-pad direction to Scroll Down Smooth; hold, then let go | Scrolls steadily while held and stops on release (issue 119). Bind it on the Review tab (or Question and Answer) to scroll cards while studying: the Micro's default profile gives the D-pad its own actions there, which override the All tab |
| Scroll Up / Scroll Down at Scroll Speed 10, then 4 and 5 | About half a screen per press at 10, a fifth to a quarter at 4–5; never jumps to the end (issue 137) |
| Scroll on a long AnKing card, and inside an AMBOSS pop-up | The card or pop-up scrolls (issue 155) |
| Quick Select: open it, move with the D-pad, close it | The D-pad moves the highlight (issue 104); the Toggle button closes it; a held Show Quick Select closes on release |
| Switch the controller off while smooth-scrolling, then on | "Controller Disconnected" appears, scrolling stops, and it reconnects without scrolling again |
| Bind Click and reveal a one-by-one cloze | One press reveals one gap (issue 153) |
| Answer twenty cards | Note any delay between pressing and the next card (issues 142, 157) |

6. Run Tools → Controller Diagnostics once more with the test build enabled, and write "test build" in the notes.

### Test cards

Issue 155 was reported on AnKing cards, whose styling differs from Anki's built-in note types, and inside AMBOSS pop-ups. You don't need the AnKing deck itself: its note type is free, and a couple of long notes are enough.

1. Download `AnKingOverhaul.apkg` from the AnKing note types release page: https://github.com/AnKing-VIP/AnKing-Note-Types/releases/tag/AnKingOverhaul-3ff1197 (under Assets). It holds the AnKingOverhaul note type and one sample note, "One Card To Rule Them All".
2. In Anki: File → Import → choose the file. It adds a deck containing the sample note.
3. Make a long card. Click Add, choose the **AnKingOverhaul** note type and that deck, and put in the **Text** field `Long card {{c1::test}}`. In **Extra**, paste a few screens of any text (a Wikipedia article is fine), enough that the back of the card needs scrolling. Add it.
4. Make a one-by-one card for the Click row. Add another AnKingOverhaul note with `{{c1::one}} {{c1::two}} {{c1::three}}` in **Text**, and type `y` in the **One by one** field, which turns on revealing the gaps one at a time. Add it.
5. Study that deck. To scroll, use the long card's back. For the Click row, bind Click to a button on the Review tab, show the answer of the one-by-one card, move the mouse over the first hidden gap and press the button: one gap should appear per press.

AMBOSS pop-ups need the AMBOSS add-on and an AMBOSS account. If you don't have one, skip that half of the row and write "no AMBOSS" in your notes; the long AnKing card covers the main part of issue 155.

## Part C: bring the results back

Copy the JSON reports into `controller-reports/` in this repository (Git ignores that folder, so they stay local), along with your checklist notes, and ask Claude to go through them.

## Undo

Tools → Add-ons: delete "Contanki - Controller Support" with the "v1.0.2 + Micro fixes" version and "Contanki Diagnostics", re-enable the AnkiWeb Contanki, and restart Anki.
