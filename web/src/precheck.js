// Instant, local, free first pass of the pre-submission check.
//
// Two reasons it exists alongside the model-backed check in
// panel/classify.py:
//
// 1. Mock mode has no backend to ask, so POST /check isn't available --
//    and mock mode is precisely where someone pokes at the app without
//    spending credits. Pasting obvious nonsense there used to run a full
//    panel with no warning at all.
// 2. Even in real mode, the blatant cases don't need a model call. This
//    runs in microseconds and catches character soup; the model handles
//    everything subtler.
//
// Conservative by construction. A false "this isn't copy" on someone's
// real landing page is a far worse failure than letting nonsense through,
// so this returns null -- "no opinion" -- for anything it isn't sure of.

// Letter runs this long with no vowel are not English words. "strength"
// manages 4 consonants in a row; 6+ is essentially always mashing.
const CONSONANT_RUN = /[bcdfghjklmnpqrstvwxz]{6,}/i

// A token must look mashed to count, by either test. The second catches
// strings whose consonant runs are broken up by stray vowels --
// "adfkljasdfkaksdfj" has no 6-run but is 17 characters at 24% vowels.
const MIN_GIBBERISH_LENGTH = 6
const LOW_VOWEL_LENGTH = 8
const LOW_VOWEL_RATIO = 0.2

// Long digit runs. A 45-character number is not copy, and the
// letters-only tokenizer below cannot see it at all -- which is how a
// paste ending in "309840394850329458230945823045830294854353454" got
// through. 12 is clear of anything real copy carries: order numbers,
// phone numbers and prices measured in the examples are all shorter.
const DIGIT_RUN = /\d{12,}/g

// Count alone, with no density threshold. Three versions of this check
// tried to be clever about density and each one let through a case it was
// written for, because the measurement says density is the wrong idea:
//
//   pricing_v1  0 garbage tokens of 231      a German page       0 of 16
//   policy_v1   0 of 301                     a rough draft       0 of 23
//   ad_v1       0 of 253                     a product list      0 of 15
//                                            phone nos + prices  0 of 13
//
// Real copy scores exactly ZERO. Not "low" -- zero. So a ratio only ever
// masked real garbage behind whatever amount of genuine prose surrounded
// it: 2 mashed tokens among 26 real words is 7.7% density and still two
// lines of keyboard mashing. Two is the floor purely so that a single
// freak brand name cannot trip the whole check.
const MIN_GARBAGE_TOKENS = 2

function words(text) {
  return text.toLowerCase().match(/[a-z']+/g) ?? []
}

function vowelRatio(word) {
  return (word.match(/[aeiouy]/g)?.length ?? 0) / word.length
}

// Keyboard rows. A walk along one of these is a common way to type
// nonsense, and it defeats both tests above: "qwertyuiop" is 37% vowels
// with no consonant run longer than 5. No English word contains five
// consecutive characters of a keyboard row, so this has essentially no
// false-positive risk.
const KEYBOARD_ROWS = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']
const KEYBOARD_WALK = 5

function hasKeyboardWalk(word) {
  for (const row of KEYBOARD_ROWS) {
    for (let i = 0; i + KEYBOARD_WALK <= row.length; i++) {
      if (word.includes(row.slice(i, i + KEYBOARD_WALK))) return true
    }
  }
  return false
}

function looksMashed(word) {
  if (word.length > MIN_GIBBERISH_LENGTH && CONSONANT_RUN.test(word)) return true
  if (word.length >= KEYBOARD_WALK && hasKeyboardWalk(word)) return true
  return word.length >= LOW_VOWEL_LENGTH && vowelRatio(word) < LOW_VOWEL_RATIO
}

/**
 * Returns an ArtifactCheck-shaped object when the text is almost certainly
 * not prose a reader panel can react to, or null for "no opinion".
 */
export function localPrecheck(text) {
  const trimmed = (text ?? '').trim()
  if (!trimmed) return null

  const tokens = words(trimmed)
  // Nothing to measure a ratio over. The 50-character input floor
  // (MIN_TEXT_LENGTH) guards the short end.
  if (tokens.length < 3) return null

  const mashed = tokens.filter(looksMashed).length
  const digitRuns = (trimmed.match(DIGIT_RUN) ?? []).length
  const garbage = mashed + digitRuns
  const mostlyGarbage = mashed / tokens.length > 0.5

  if (garbage >= MIN_GARBAGE_TOKENS) {
    return {
      kind: mostlyGarbage ? 'random characters' : 'part prose, part random characters',
      is_reviewable: false,
      reason: mostlyGarbage
        ? 'This does not look like prose. A panel would have nothing to react to, so the findings would be meaningless.'
        : 'Part of this looks like random characters rather than copy. The panel reacts to the whole thing, so the findings would partly be about the parts that are not real.',
      source: 'local',
    }
  }

  // Lorem ipsum is Latin: plausible-looking words, no English function
  // words, no mashing. Worth naming explicitly, since it is the single
  // most likely placeholder anyone pastes by accident.
  if (/\blorem ipsum\b/i.test(trimmed)) {
    return {
      kind: 'placeholder text',
      is_reviewable: false,
      reason: 'This looks like lorem ipsum placeholder text rather than real copy.',
      source: 'local',
    }
  }

  return null
}
