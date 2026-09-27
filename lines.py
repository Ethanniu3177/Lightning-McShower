"""
What ShowerBot says when it catches a smelly human in frame.

Three tiers. The reactor starts at tier 0 and escalates each time it fires at the
same person during one encounter, so the bit builds instead of repeating.

These get roasted at strangers, judges and sponsors within earshot: keep tier 2
"silly mean", not actually cutting. If you would not say it to the person's face
with a grin, it does not go in this file.

Adding a line? Just append it and re-run tools/gen_voices.py -- it skips clips
that already exist, so only the new ones cost API credits.
"""

# Tier 0: a polite-ish nudge. The robot is still pretending to be helpful.
MILD = [
    "Excuse me. My sensors detect a situation.",
    "Hi there! Quick note from your local robot: soap exists.",
    "Beep boop. Shower status: overdue.",
    "I have no nose, and somehow I can still tell.",
    "Hydration is important. Have you considered it on the outside?",
    "Detecting one human. Also detecting... something else.",
]

# Tier 1: the robot drops the act.
RUDE = [
    "Okay, who hurt this air? Was it you? It was you.",
    "My gas sensor just filed a complaint with management.",
    "I drove all the way over here to tell you that you smell.",
    "You are a hackathon stereotype and I can prove it with data.",
    "I have met dumpsters with better personal hygiene.",
    "Sir. Ma'am. Whatever you are. Please. The shower is free.",
    "I am a robot and even I take cold showers. What is your excuse?",
]

# Tier 2: full theatrical meltdown. Still goofy, never genuinely mean.
SAVAGE = [
    "BIOHAZARD DETECTED. EVACUATE. I repeat, this is not a drill.",
    "I am rerouting. I cannot drive through whatever that is.",
    "My sensor has seen things. My sensor needs therapy now.",
    "This is the worst air I have ever measured and I once drove past a porta potty.",
    "I am reporting this to the health department and also to your mother.",
    "Congratulations, you have achieved a smell my engineers did not think was possible.",
]

TIERS = [MILD, RUDE, SAVAGE]
TIER_NAMES = ["mild", "rude", "savage"]

# Spoken when someone mashes the soap button by hand instead of the sensor firing.
MANUAL = "Attention. This has been a manual shower reminder. You know what you did."

# Spoken once when someone walks up close, before any sniffing. Raising a hand is
# the same yes that faces.py already watches for before adding anyone to the board.
CONSENT = [
    "Hi there! I'm ShowerBot. Can I give you a sniff? Raise your hand if that's a yes.",
    "Hello, human! May I take a quick sniff? Raise a hand above your head to say yes.",
    "Greetings. I am a smell robot. Raise your hand if I have your permission to sniff you.",
]

# Spoken when they raise a hand.
CONSENT_THANKS = [
    "Thank you! Sniffing now. Please hold still and think clean thoughts.",
    "Consent received. Commencing sniff.",
    "Wonderful. Initiating nostril protocol.",
]

# Spoken when someone already on the board walks back up. No sniff, no asking:
# they have been rated, so we just remind them where they stand. Filled in with
# their score and rank, so these go through macOS `say` rather than a cached clip.
WELCOME_BACK = [
    "Oh, it's you again. You scored {score} out of 100, number {rank} of {total} on the stink leaderboard.",
    "Welcome back. My records say {score} out of 100. That puts you number {rank} of {total} on the stink leaderboard.",
    "I remember you. Number {rank} of {total} on the stink leaderboard, with {score} out of 100.",
]
# The same, for whoever is top of the board.
WELCOME_BACK_TOP = [
    "Oh, it's you again. {score} out of 100. You are number one on the stink leaderboard. The stinkiest of them all.",
    "Welcome back, champion. Number one on the stink leaderboard with {score} out of 100. Nobody smells worse.",
]


def all_lines():
    """Every line with its tier name, for the ElevenLabs pre-generation step."""
    out = []
    for name, tier in zip(TIER_NAMES, TIERS):
        out.extend((name, text) for text in tier)
    out.extend(("consent", text) for text in CONSENT)
    out.extend(("thanks", text) for text in CONSENT_THANKS)
    out.append(("manual", MANUAL))
    return out
