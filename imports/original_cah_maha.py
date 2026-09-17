#!/usr/bin/env python3

from dataclasses import dataclass
import argparse
import random
import time


# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BlackCard:
    repr: str
    template: str
    slots: int

    def render(self, *answers: str) -> str:
        if len(answers) != self.slots:
            raise ValueError(
                f"Expected {self.slots} answer(s), got {len(answers)}"
            )

        return self.template.format(*answers)


# ──────────────────────────────────────────────────────────────────────────────
# Black Cards
# ──────────────────────────────────────────────────────────────────────────────

BLACK_CARDS = [
    BlackCard(
        repr="The FDA has announced that _ is now approved to treat _.",
        template="The FDA has announced that {} is now approved to treat {}.",
        slots=2,
    ),
    BlackCard(
        repr="RFK Jr. has promised to remove _ from the American food supply by 2028.",
        template="RFK Jr. has promised to remove {} from the American food supply by 2028.",
        slots=1,
    ),
    BlackCard(
        repr="Doctors hate this one weird trick involving _.",
        template="Doctors hate this one weird trick involving {}.",
        slots=1,
    ),
    BlackCard(
        repr="The CDC is investigating a multistate outbreak linked to _.",
        template="The CDC is investigating a multistate outbreak linked to {}.",
        slots=1,
    ),
    BlackCard(
        repr="MAHA: Make America _ Again.",
        template="MAHA: Make America {} Again.",
        slots=1,
    ),
    BlackCard(
        repr="Ask your doctor if _ is right for you.",
        template="Ask your doctor if {} is right for you.",
        slots=1,
    ),
    BlackCard(
        repr="The Department of Health and Human Services recommends at least 30 minutes of _ every day.",
        template="The Department of Health and Human Services recommends at least 30 minutes of {} every day.",
        slots=1,
    ),
    BlackCard(
        repr="After extensive clinical trials, researchers found that _ significantly reduces the risk of _.",
        template="After extensive clinical trials, researchers found that {} significantly reduces the risk of {}.",
        slots=2,
    ),
    BlackCard(
        repr="The FDA would like to remind Americans that _ is not, technically speaking, medicine.",
        template="The FDA would like to remind Americans that {} is not, technically speaking, medicine.",
        slots=1,
    ),
    BlackCard(
        repr="NEW STUDY: Scientists find a surprising connection between _ and _.",
        template="NEW STUDY: Scientists find a surprising connection between {} and {}.",
        slots=2,
    ),
    BlackCard(
        repr="The Surgeon General has issued a warning about teenagers doing _ on TikTok.",
        template="The Surgeon General has issued a warning about teenagers doing {} on TikTok.",
        slots=1,
    ),
    BlackCard(
        repr="Nine out of ten doctors recommend _. The tenth doctor recommends _.",
        template="Nine out of ten doctors recommend {}. The tenth doctor recommends {}.",
        slots=2,
    ),
    BlackCard(
        repr="Side effects may include nausea, dizziness, and _.",
        template="Side effects may include nausea, dizziness, and {}.",
        slots=1,
    ),
    BlackCard(
        repr="If _ lasts longer than four hours, seek immediate medical attention.",
        template="If {} lasts longer than four hours, seek immediate medical attention.",
        slots=1,
    ),
    BlackCard(
        repr='Due to recent regulatory changes, _ may now legally be called "cheese."',
        template='Due to recent regulatory changes, {} may now legally be called "cheese."',
        slots=1,
    ),
    BlackCard(
        repr='The wellness influencer assured me that _ would "reset my mitochondria."',
        template='The wellness influencer assured me that {} would "reset my mitochondria."',
        slots=1,
    ),
    BlackCard(
        repr="My insurance denied _, but somehow approved _.",
        template="My insurance denied {}, but somehow approved {}.",
        slots=2,
    ),
    BlackCard(
        repr="In lieu of universal healthcare, America offers you _.",
        template="In lieu of universal healthcare, America offers you {}.",
        slots=1,
    ),
    BlackCard(
        repr="The clinical trial was terminated early after participants began _.",
        template="The clinical trial was terminated early after participants began {}.",
        slots=1,
    ),
    BlackCard(
        repr="Health Canada has politely asked Americans to stop putting _ in _.",
        template="Health Canada has politely asked Americans to stop putting {} in {}.",
        slots=2,
    ),
    BlackCard(
        repr='The label says "all natural," so I\'m choosing not to ask about _.',
        template='The label says "all natural," so I\'m choosing not to ask about {}.',
        slots=1,
    ),
    BlackCard(
        repr="The placebo group received a sugar pill. The treatment group received _.",
        template="The placebo group received a sugar pill. The treatment group received {}.",
        slots=1,
    ),
    BlackCard(
        repr="BREAKING: The food pyramid has been replaced with _.",
        template="BREAKING: The food pyramid has been replaced with {}.",
        slots=1,
    ),
    BlackCard(
        repr="According to this podcast, the real cause of chronic disease is _.",
        template="According to this podcast, the real cause of chronic disease is {}.",
        slots=1,
    ),
    BlackCard(
        repr="Talk to your healthcare provider before attempting _.",
        template="Talk to your healthcare provider before attempting {}.",
        slots=1,
    ),
    BlackCard(
        repr='The FDA inspection went pretty well until they opened the door marked "_."',
        template='The FDA inspection went pretty well until they opened the door marked "{}."',
        slots=1,
    ),
    BlackCard(
        repr="After eliminating _ from his diet, Brian was finally cured of _.",
        template="After eliminating {} from his diet, Brian was finally cured of {}.",
        slots=2,
    ),
]


# ──────────────────────────────────────────────────────────────────────────────
# White Cards
# ──────────────────────────────────────────────────────────────────────────────

WHITE_CARDS = [
    "RFK's raspy voice",
    "The worm that ate part of RFK Jr.'s brain",
    "An irresponsible quantity of raw milk",
    "Beef tallow in places beef tallow should never be",
    "A suspiciously jacked 71-year-old",
    "One beautiful, glistening spoonful of cod liver oil",
    "Seed oils",
    "The healing power of being extremely online",
    "A podcast host with no relevant qualifications",
    "Doing your own research",
    "A Facebook post your aunt swears was written by a doctor",
    "An immune system with main-character syndrome",
    "Measles: the original essential oil",
    "A completely preventable Victorian disease",
    "Polio making an unexpected comeback tour",
    "A raw-milk cheese with a body count",
    "An artisanal case of listeria",
    "Farm-to-table tuberculosis",
    "Locally sourced E. coli",
    "A probiotic that has become self-aware",
    "Microplastics in my balls",
    "Macroplastics in my balls",
    "An amount of mercury usually associated with thermometers",
    "A single molecule of Red 40",
    "The sinister machinations of Big Vegetable",
    "The deep state putting canola oil in my mayonnaise",
    "A child absolutely fucking vibrating from food dye",
    "The forbidden pleasures of Yellow 5",
    "A pancreas fighting for its fucking life",
    "My last functioning mitochondrion",
    "Three supplements in a trench coat pretending to be medicine",
    "A supplement manufactured in a facility that also processes drywall",
    "An unregulated powder called ALPHA PATRIOT MAX",
    "A $79 bottle of capsules containing mostly optimism",
    "A peptide purchased from a website with a wolf logo",
    "A wellness influencer explaining endocrinology from a sauna",
    'Joe Rogan asking, "But have they actually studied that?"',
    "Six hours of podcast-based medical education",
    "A chiropractor getting dangerously ambitious",
    'The phrase "toxins" with absolutely no further clarification',
    "Detoxing something the liver was already handling",
    "Putting butter in coffee and calling it healthcare",
    "Sunlight directly on the perineum",
    "The ancestral human urge to lick a salt lamp",
    "Rejecting modernity; contracting dysentery",
    "The naturalistic fallacy, now available in gummy form",
    "The FDA's saddest employee",
    "A double-blind, placebo-controlled vibes check",
    "Statistically significant bullshit",
    "Peer review by three guys on X",
    "The placebo effect, but fucking committed",
    "A randomized controlled trial conducted entirely in a group chat",
]


# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

HAND_SIZE = 10
RAPID_FIRE_DELAY = 0.75


# ──────────────────────────────────────────────────────────────────────────────
# Random Generator
# ──────────────────────────────────────────────────────────────────────────────

def generate_random_round() -> tuple[BlackCard, list[str], str]:
    black = random.choice(BLACK_CARDS)
    answers = random.sample(WHITE_CARDS, black.slots)
    result = black.render(*answers)

    return black, answers, result


# ──────────────────────────────────────────────────────────────────────────────
# One-Shot Mode
# ──────────────────────────────────────────────────────────────────────────────

def oneshot() -> None:
    """
    Generate exactly one completed card.

    Intentionally prints no decoration so the output can be piped,
    captured, or otherwise abused by Unix.
    """
    _, _, result = generate_random_round()
    print(result)


# ──────────────────────────────────────────────────────────────────────────────
# Normal Mode
# ──────────────────────────────────────────────────────────────────────────────

def deal() -> tuple[BlackCard, list[str]]:
    black = random.choice(BLACK_CARDS)

    hand = random.sample(
        WHITE_CARDS,
        min(HAND_SIZE, len(WHITE_CARDS)),
    )

    return black, hand


def display_hand(black: BlackCard, hand: list[str]) -> None:
    print()
    print("═" * 72)
    print("⬛ MAHA PACK")
    print("═" * 72)
    print()
    print(black.repr)
    print()
    print(f"PICK {black.slots}")
    print()
    print("─" * 72)

    for i, card in enumerate(hand, start=1):
        print(f"{i:>2}. {card}")

    print("─" * 72)


def get_choices(hand: list[str], slots: int) -> list[str]:
    while True:
        raw = input(
            f"\nChoose {slots} card"
            f"{'s' if slots != 1 else ''}"
            f"{', comma-separated' if slots > 1 else ''}: "
        )

        try:
            indexes = [
                int(value.strip()) - 1
                for value in raw.split(",")
            ]

            if len(indexes) != slots:
                raise ValueError

            if len(set(indexes)) != len(indexes):
                print("You can't play the same card twice.")
                continue

            if any(index < 0 or index >= len(hand) for index in indexes):
                raise ValueError

            return [hand[index] for index in indexes]

        except ValueError:
            print(
                f"Enter exactly {slots} valid card number"
                f"{'s' if slots != 1 else ''}."
            )


def play_round() -> None:
    black, hand = deal()

    display_hand(black, hand)

    answers = get_choices(hand, black.slots)
    result = black.render(*answers)

    print()
    print("🔥" * 36)
    print()
    print(result)
    print()
    print("🔥" * 36)


def normal_mode() -> None:
    while True:
        play_round()

        again = input("\nAnother round? [Y/n] ").strip().lower()

        if again in {"n", "no"}:
            print("\nThe FDA has not evaluated this game.")
            return


# ──────────────────────────────────────────────────────────────────────────────
# Rapid-Fire Mode
# ──────────────────────────────────────────────────────────────────────────────

def rapid_fire(delay: float = RAPID_FIRE_DELAY) -> None:
    if delay < 0:
        raise ValueError("Rapid-fire delay cannot be negative.")

    print()
    print("═" * 72)
    print("🔥 RAPID FIRE MODE 🔥")
    print("═" * 72)
    print()
    print(f"Generating a fresh atrocity every {delay:g} seconds.")
    print("Press Ctrl+C to stop.")
    print()

    round_number = 1

    try:
        while True:
            black, answers, result = generate_random_round()

            print(f"ROUND {round_number}")
            print()
            print("⬛", black.repr)

            for answer in answers:
                print("⬜", answer)

            print()
            print("→", result)
            print()
            print("─" * 72)

            round_number += 1
            time.sleep(delay)

    except KeyboardInterrupt:
        print()
        print()
        print("Rapid fire stopped.")
        print("The FDA has been notified.")


# ──────────────────────────────────────────────────────────────────────────────
# Interactive Menu
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("╔" + "═" * 70 + "╗")
    print("║" + " MAHA PACK ".center(70) + "║")
    print(
        "║"
        + "A deeply irresponsible Cards Against Humanity generator.".center(70)
        + "║"
    )
    print("╚" + "═" * 70 + "╝")

    while True:
        print()
        print("[1] Normal mode")
        print("[2] Rapid fire")
        print("[q] Quit")
        print()

        mode = input("> ").strip().lower()

        if mode in {"1", "normal", "n"}:
            normal_mode()

        elif mode in {"2", "rapid", "r"}:
            rapid_fire()

        elif mode in {"q", "quit", "exit"}:
            print("\nThe FDA has not evaluated this game.")
            return

        else:
            print("Please select 1, 2, or q.")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "MAHA Pack: a deeply irresponsible "
            "Cards Against Humanity generator."
        )
    )

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(
        "--oneshot",
        action="store_true",
        help="Generate one completed random card and exit.",
    )

    mode.add_argument(
        "--rapid",
        action="store_true",
        help="Start directly in rapid-fire mode.",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=RAPID_FIRE_DELAY,
        help=(
            "Delay between cards in rapid-fire mode "
            f"(default: {RAPID_FIRE_DELAY} seconds)."
        ),
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.delay < 0:
        raise SystemExit("--delay must be zero or greater.")

    if args.oneshot:
        oneshot()

    elif args.rapid:
        rapid_fire(args.delay)

    else:
        main()
