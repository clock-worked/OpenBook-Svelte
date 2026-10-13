# Frozen evaluation books

Parser and threshold changes are tuned only on the development books (14–16), validated on Book 17, and Book 18 gets exactly one confirm-only pass. No retuning after seeing held-out results, and no book-specific rules. Headline metric is the steering triple (assert-rate floor, assert precision, guessHit@3) rather than bare accuracy, because accuracy rewards confident asserts and can be gamed in the opposite direction of the false-positive goal.
