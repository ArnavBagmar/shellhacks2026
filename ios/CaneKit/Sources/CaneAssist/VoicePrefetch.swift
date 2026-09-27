import CaneGuide
import Foundation

/// Every fixed line Cane speaks in the natural voice, fetched from the laptop
/// as soon as the phone connects, so each one plays at once and in the same
/// (ElevenLabs) voice -- the first "Hmm, let me think." of a walk was missing
/// its budget and coming out in the phone's voice (2026-09-27).
///
/// Keep `COMMON_PHRASES` in `backend/src/cane/scene/voice.py` a superset of
/// this list (a test checks): the laptop voices those at start-up.
public enum VoicePrefetch {
    public static var phrases: [String] {
        PromptPhrases.all + guide + replies
    }

    /// "Guide my hand": the start, the direction words, the ending.
    static let guide: [String] = [
        GuideSession.startPhrase(watch: true), GuideSession.startPhrase(watch: false),
        GuideSession.noHandPrompt, GuideSession.reachedPhrase, GuideSession.refindPhrase,
        GuideSession.timedOutPhrase,
    ] + [GuideDirection.left, .right, .up, .down, .forward, .back].map(\.spoken)

    static let replies: [String] = [
        AnswerPhrasing.noLink, AnswerPhrasing.timedOut, AnswerPhrasing.cancelled, AnswerPhrasing.faceItToGuide,
        AnswerPhrasing.guideWhat,
    ]
}
