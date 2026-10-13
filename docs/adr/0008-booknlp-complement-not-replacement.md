# BookNLP is a complement, not a replacement parser

ModernBookNLP produces strong speaker labels for the quotes it extracts, but its end-to-end quote-extraction recall remains below OpenBook's frozen baseline, so it is kept as a complementary signal in a dedicated environment (`.venv-booknlp`) rather than integrated as the parser. Explicit local attribution is guarded against its overrides: local speech subjects beat 84 conflicting BookNLP proposals (76 of them correct).
