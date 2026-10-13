# Keep the explainable episode-level joint decoder

The episode-level joint decoder (Viterbi over short two-participant dialogue stretches with alternation transition weights) is retained because the same fixed parameters improved all four evaluation books, including the held-out one, and every assignment carries visible provenance. The role-descriptor and backpropagation experiments were rejected: a role noun can describe an occupation, addressee, observer, or incidental concept, and candidate confidence is not calibrated strongly enough for safe propagation.
