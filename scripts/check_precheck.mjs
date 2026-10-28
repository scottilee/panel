// Cases for web/src/precheck.js, the local half of the pre-submission
// check. Run with:  node scripts/check_precheck.mjs
//
// There is no JS test runner in this project, so this stands in for one
// over the single piece of frontend logic where a false positive has a
// real cost: wrongly telling someone their own copy is not copy. Both
// reported cases below came from a user pasting into mock mode.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const { localPrecheck } = await import(join(here, '../web/src/precheck.js'))
const read = (f) => readFileSync(join(here, '../data/examples', f), 'utf8')
const CASES = [
  // [name, text, should it pass without a warning?]
  ['reported case 1', 'GIBBY BOO KLJSADKJjhasdfkjhkjh\n\nadfkljasdfkaksdfj\nalsdfasldkfj', false],
  ['reported case 2', 'Stop letting your bank keep the upside.\n\nIN STEAD OGSDFJFKS\nDSLKFJSDKFJKSDF', false],
  // Most of a real example retained, soup appended, plus a 45-digit run
  // that the letters-only tokenizer could not see at all.
  ['reported case 3', 'PlateUp — Meal Planning Made Simple\n\nStop staring at the fridge wondering what to cook. PlateUp builds your\nentire week of dinners around what you LKSDJDKSFDSKFJSDKFJ\nLSKDFJSDLKFJ\n\n309840394850329458230945823045830294854353454', false],
  ['long digit run alone', 'Order confirmed. 309840394850329458230945823045830294854353454 and 112233445566778899001', false],
  ['phone numbers and prices', 'Call 0800 555 0199 or pay $29.99 today. Order number 4471829301 ships within 2 business days.', true],
  ['keyboard mash', 'asdkjh asd kjhasd kjhsdfkjh kjhsdfkjh kjhdfgkjh zxcvzxcv qwerqwer mnbvmnbv', false],
  ['lorem ipsum', 'Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut.', false],
  ['one real line + soup', 'Get your first week free, no card needed.\nqwertyuiopasdfgh zxcvbnmasdfghjkl', false],
  ['real pricing page', read('pricing_v1.txt'), true],
  ['real policy notice', read('policy_v1.txt'), true],
  ['real ad', read('ad_v1.txt'), true],
  ['rough lowercase draft', 'we built a thing that helps you find a plumber fast. no app needed. text us and we call you back in 5 min', true],
  ['German landing page', 'Jetzt kostenlos testen. Wir helfen Ihnen dabei, Ihre Rechnungen schneller zu bezahlen und Zeit zu sparen.', true],
  ['verbless product list', 'Blue Widget Pro. Blue Widget Max. Blue Widget Ultra. Red Widget Pro. Red Widget Max.', true],
  ['one typo in a real page', read('pricing_v1.txt') + '\nqwertyuiopasdfghjkl', true],
  ['ALL CAPS real notice', 'NOTICE TO ALL RESIDENTS. PERMIT APPLICATIONS OPEN ON THE FIRST OF THE MONTH AND CLOSE AFTER TEN DAYS.', true],
  ['copy with a URL', 'Verify your block at https://riverton.gov/parking/zone-lookup?addr=main-st before you apply.', true],
]

let bad = 0
for (const [name, text, shouldPass] of CASES) {
  const v = localPrecheck(text)
  const passed = v === null
  const ok = passed === shouldPass
  if (!ok) bad++
  console.log(
    (ok ? 'OK   ' : 'WRONG'),
    name.padEnd(24),
    passed ? 'no opinion' : 'warns: ' + v.kind)
}
console.log(bad ? `\n${bad} WRONG` : '\nall correct')
process.exit(bad ? 1 : 0)
