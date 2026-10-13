# JEV verification is downgrade-only on the live path

The live JEV verify pass can downgrade an assert to a suggestion or unknown, but never promotes a suggestion to an assert; only the offline/CLI path may promote. The pass is fail-open: with no API key it is skipped and the payload stays byte-identical to the no-JEV run, and per-run failures keep the heuristic values, so a verify outage can never corrupt a parse.
