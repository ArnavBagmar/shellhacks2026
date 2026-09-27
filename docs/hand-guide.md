# Guide my hand (phone + Apple Watch)

"Hey Cane, guide my hand to the Rubik's cube." Cane finds the thing, then
follows it and the wearer's fingertips live in the chest camera. The watch
taps faster as the hand gets closer, and "You've got it." ends the guide.

## What happens

1. **Find.** The phrase becomes `VoiceIntent.guideHand("rubik's cube")`, which
   is sent to the laptop as a normal `find` question. Gemini answers with the
   thing's box in the phone photo (`phone_box`). The laptop prompt already
   prefers the phone image whenever it shows the thing. The laptop side is unchanged.
   - If only a 360 view shows the thing, Cane says where it is and adds
     "Turn to face it, then ask me again to guide your hand."
   - "Help me grab it", "guide my hand" or "guide me to it" with no name means
     the last thing found.
   - "Guide me to ..." is always the hand. Map routes need the word
     "navigate": "navigate me to the library".
2. **Track** (`CaneAssist/HandGuide.swift`, iOS). This uses no detection model,
   only what is built into iOS:
   - Apple Vision's object tracker, seeded with Gemini's box, follows the thing.
   - Vision's hand-pose request finds every fingertip, and the fingertip nearest
     the thing is used.
   - TrueDepth measures both the thing and a small patch around the fingertip.
   - While the hand covers the thing, its box is held still. Otherwise the
     tracker would follow the hand.
3. **Decide** (`CaneGuide/GuideSession.swift`, pure and tested on macOS):
   - The gap is measured from the fingertip to the thing's surface, in metres.
   - The gap goes to the watch about 10 times a second.
   - Cane says a direction word ("Left.", "Down.", "Forward.") every few
     seconds, but not within 8 cm of the thing.
   - A fingertip within 4 cm of the thing for 0.3 s means it was reached.
   - The guide ends if the thing is out of sight for 2.5 s, after 60 s, or
     when the wearer says anything.
4. **Tap** (`ios/CaneWatch`). The watch has no intensity control, so closeness
   is carried by the tap rate, like a parking sensor:
   - One light tap a second at 60 cm or more, getting faster down to one tap
     every 0.15 s near touching (`GuidePulse`).
   - Firm taps within 10 cm, and a success tap at the end.
   - An extended runtime session (physical therapy) keeps the taps going with
     the wrist lowered.

If the watch app isn't open, Cane says so and guides by voice instead, with
more frequent words plus "Close." and "Almost there."

## Running it

1. `./startcane phone` builds and installs the phone app. The watch app is
   embedded in it:
   - iOS installs it on the paired watch if "Automatic App Install" is on.
   - If not, open the iPhone's Watch app, go to Available Apps, and tap
     Install next to Cane.
2. Open **Cane** on the watch. It shows "Phone connected" once the phone app
   is running. The phone's setup notes show `watch: connected`.
3. Put the thing on a table in front of you, 30 cm to 1 m from your chest.
   Your hand must stay in view of the chest camera.
4. Say "Hey Cane, guide my hand to the Rubik's cube." Hold still while Cane
   looks, then reach out slowly.

Black-box events: `guide_start` (target, watch status, box) and `guide_end`
(`reached` / `lost` / `timedOut` / `stopped`).

## Limits

- TrueDepth reads from about 0.2 m to 1.2 m in practice. Beyond that, or when
  the reading fails, the gap is estimated from the photo alone. The taps still
  work, but the watch shows no centimetres.
- The tracker starts from the photo taken when the question was asked. If the
  wearer turns a lot during the ~3 s answer, it can start on the wrong thing.
  Ask again facing it.
- A free (personal) Apple developer team must also sign the watch app. If
  signing fails, open `ios/CaneApp/CaneApp.xcodeproj` once with the watch
  paired so Xcode can register it.
