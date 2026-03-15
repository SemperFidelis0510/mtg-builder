#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Interactive script: probability of drawing 2–5 lands in the first 7 cards
of a 60-card deck, using the hypergeometric distribution.
"""

import math

DECK_SIZE = 60
OPENING_HAND_SIZE = 7
LAND_COUNTS = (2, 3, 4, 5)


def hypergeom_pmf(k: int, N: int, K: int, n: int) -> float:
    """
    P(X = k): probability of exactly k successes in n draws from a population
    of N with K successes (without replacement).
    """
    if k > K or k > n or (n - k) > (N - K) or k < 0:
        return 0.0
    return math.comb(K, k) * math.comb(N - K, n - k) / math.comb(N, n)


def main() -> None:
    print("Land draw probability (first 7 cards, 60-card deck)")
    print("=" * 50)

    while True:
        raw = input("Number of lands in the deck (0–60, or 'q' to quit): ").strip().lower()
        if raw == "q":
            print("Bye.")
            break
        try:
            num_lands = int(raw)
        except ValueError:
            print("Please enter an integer between 0 and 60, or 'q' to quit.\n")
            continue
        if not 0 <= num_lands <= DECK_SIZE:
            print(f"Number of lands must be between 0 and {DECK_SIZE}.\n")
            continue

        print(f"\nDeck: {DECK_SIZE} cards, {num_lands} lands. Drawing {OPENING_HAND_SIZE} cards.\n")
        print(f"{'Lands in hand':<16} {'Probability':<14} {'%':<8}")
        print("-" * 40)

        for k in LAND_COUNTS:
            p = hypergeom_pmf(k, DECK_SIZE, num_lands, OPENING_HAND_SIZE)
            pct = 100.0 * p
            print(f"{k:<16} {p:<14.4f} {pct:>6.2f}%")

        print()
        total = sum(
            hypergeom_pmf(k, DECK_SIZE, num_lands, OPENING_HAND_SIZE)
            for k in LAND_COUNTS
        )
        print(f"P(2 ≤ lands ≤ 5): {total:.4f}  ({100.0 * total:.2f}%)")
        print()


if __name__ == "__main__":
    main()
