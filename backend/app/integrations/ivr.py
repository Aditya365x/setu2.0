"""§35 — the IVR decision tree.

The last rung of the degradation ladder. Someone with no smartphone, no data,
and no ability to type an SMS can still dial a number and press keys. In coastal
Odisha that is not an edge case: it is a large fraction of the population most
exposed to a cyclone.

The tree is deliberately three questions deep and never more:

    hazard  ->  people  ->  severity  ->  incident created

Each step is one TwiML `<Gather>` returning at most a single digit, and the
answers accumulate in the query string rather than in server-side session state.
That is not a shortcut — it means a dropped call that redials mid-tree does not
resume into a half-built record belonging to somebody else, and it means the
worker can be restarted mid-demo without stranding callers.

Location comes from the caller's pincode if they can enter it, and otherwise
from the district centroid, exactly as the SMS path does — stored honestly as
`gps_accuracy_m = 3000`/`25000` rather than pretending a phone tree yields GPS.

`MockGateway` renders the same XML to a simulator page, so the whole thing is
demonstrable with the network unplugged.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

# Menu order is by expected call volume during a cyclone, not alphabetical:
# every extra key a drowning caller has to hear is time.
HAZARD_KEYS: dict[str, str] = {
    "1": "flood",
    "2": "stranded",
    "3": "medical",
    "4": "building_collapse",
    "5": "fire",
    "6": "cyclone_damage",
    "7": "landslide",
    "9": "other",
}

# 1-5 maps straight onto severity_raw, so the IVR and the SMS grammar produce
# identical records and the optimizer cannot tell which channel a call came from.
SEVERITY_KEYS = {str(n): n for n in range(1, 6)}

# Spoken in the caller's language where we have it. English is the fallback,
# never the assumption.
PROMPTS = {
    "en": {
        "welcome": "S E T U emergency reporting.",
        "hazard": (
            "Press 1 for flood. 2 if people are stranded. 3 for a medical emergency. "
            "4 for a building collapse. 5 for fire. 6 for cyclone damage. "
            "7 for landslide. 9 for anything else."
        ),
        "people": "How many people need help? Press the number of people, then hash. Press hash alone if you do not know.",
        "severity": "How urgent is it? Press 1 for low, up to 5 if there is immediate danger to life.",
        "pincode": "Enter your 6 digit PIN code, then hash. Press hash alone to skip.",
        "confirm": "Your report is registered. Your reference is {ref}. Help is being arranged. Do not cross flowing water.",
        "invalid": "Sorry, that was not a valid choice.",
        "failed": "We could not register your report. Please call again, or send an S M S.",
    },
}

LANG_VOICE = {"en": "en-IN"}


def _say(text_: str, lang: str = "en") -> str:
    return f'<Say language="{LANG_VOICE.get(lang, "en-IN")}">{escape(text_)}</Say>'


def _response(*body: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(body) + "</Response>"


def _gather(action: str, prompt: str, lang: str, digits: int | None = 1,
            finish_on_hash: bool = False) -> str:
    """One question. `numDigits` for a single choice, `finishOnKey` when the
    answer is a variable-length number the caller terminates with hash."""
    attrs = [f'action="{escape(action, {chr(34): "&quot;"})}"', 'method="POST"', 'timeout="7"']
    if finish_on_hash:
        attrs.append('finishOnKey="#"')
    elif digits:
        attrs.append(f'numDigits="{digits}"')
    return f"<Gather {' '.join(attrs)}>{_say(prompt, lang)}</Gather>"


def welcome_twiml(base: str = "/api/v1/ivr", lang: str = "en") -> str:
    """Step 1 — what is happening. Entry point for the inbound call webhook."""
    p = PROMPTS.get(lang, PROMPTS["en"])
    return _response(
        _say(p["welcome"], lang),
        _gather(f"{base}/hazard?lang={lang}", p["hazard"], lang, digits=1),
        # Falls through only if the caller pressed nothing. Repeat rather than
        # hang up: silence often means they are busy staying alive.
        _say(p["hazard"], lang),
        f'<Redirect method="POST">{base}/start?lang={lang}</Redirect>',
    )


def hazard_twiml(digit: str, base: str = "/api/v1/ivr", lang: str = "en") -> str:
    """Step 2 — how many people, given a hazard choice."""
    p = PROMPTS.get(lang, PROMPTS["en"])
    hazard = HAZARD_KEYS.get(digit)
    if not hazard:
        return _response(
            _say(p["invalid"], lang),
            f'<Redirect method="POST">{base}/start?lang={lang}</Redirect>',
        )
    return _response(
        _gather(
            f"{base}/people?lang={lang}&hazard={hazard}",
            p["people"], lang, digits=None, finish_on_hash=True,
        ),
        f'<Redirect method="POST">{base}/people?lang={lang}&amp;hazard={hazard}</Redirect>',
    )


def people_twiml(hazard: str, people: str, base: str = "/api/v1/ivr", lang: str = "en") -> str:
    """Step 3 — urgency."""
    p = PROMPTS.get(lang, PROMPTS["en"])
    n = people if people.isdigit() else ""
    return _response(
        _gather(
            f"{base}/severity?lang={lang}&hazard={hazard}&people={n}",
            p["severity"], lang, digits=1,
        ),
        # No answer means we still file the report at the default urgency
        # rather than discarding three questions the caller already answered.
        f'<Redirect method="POST">{base}/severity?lang={lang}&amp;hazard={hazard}'
        f'&amp;people={n}&amp;Digits=3</Redirect>',
    )


def severity_twiml(hazard: str, people: str, severity: str,
                   base: str = "/api/v1/ivr", lang: str = "en") -> str:
    """Step 4 — optional pincode, which is the only location we can get."""
    p = PROMPTS.get(lang, PROMPTS["en"])
    sev = SEVERITY_KEYS.get(severity, 3)
    return _response(
        _gather(
            f"{base}/finish?lang={lang}&hazard={hazard}&people={people}&severity={sev}",
            p["pincode"], lang, digits=None, finish_on_hash=True,
        ),
        f'<Redirect method="POST">{base}/finish?lang={lang}&amp;hazard={hazard}'
        f'&amp;people={people}&amp;severity={sev}</Redirect>',
    )


def finish_twiml(reference_code: str | None, lang: str = "en") -> str:
    """Terminal. Reads the reference back twice — once as a word and once
    digit by digit — because it is being heard over a bad line, possibly in
    wind, and it is the only handle the caller has on their own report."""
    p = PROMPTS.get(lang, PROMPTS["en"])
    if not reference_code:
        return _response(_say(p["failed"], lang), "<Hangup/>")
    spaced = " ".join(reference_code)
    return _response(
        _say(p["confirm"].format(ref=reference_code), lang),
        _say(spaced, lang),
        "<Hangup/>",
    )
