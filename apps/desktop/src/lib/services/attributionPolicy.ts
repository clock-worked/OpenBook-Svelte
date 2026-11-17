// ============================================================================
// Attribution assert/suggestion policy spec — TS mirror (ADR-0013).
//
// The owner of the attribution decision is the Python module
// py_services/openbook_parser/attribution_decision.py. It owns the rule
// registry (every rule in one of four categories) and the policy spec: one
// set of thresholds and named, edge-scoped suggestion rules, applied at each
// edge. The parser applies its edge in rank_candidates; the frontend applies
// its edge here, in convertLineToDialogueLine (parser.ts), to the
// post-collapse candidate set it sees — the frontend may un-assert what the
// parser asserted, never the reverse.
//
// Both implementations are pinned by tests/attribution_policy_vectors.json
// (asserted here in __tests__/attributionPolicy.test.ts and in
// py_services/openbook_parser/test_attribution_policy_vectors.py). Keep both
// green when changing either side.
//
// The frontend keeps no rule-name registry: it reads the category the parser
// put on the wire (decisionTrace.ruleCategory). Only traces written before
// that field existed fall back to LEGACY_FIRST_PERSON_RULES.
//
// Pure module: no imports, so Node `--test` can load it directly.
// ============================================================================

/** The four exhaustive rule categories of the registry. */
export type RuleCategory = 'HARD' | 'CARRYOVER' | 'LOCAL_EVIDENCE' | 'FIRST_PERSON';

export const RULE_CATEGORIES: readonly RuleCategory[] = [
  'HARD',
  'CARRYOVER',
  'LOCAL_EVIDENCE',
  'FIRST_PERSON',
];

/** Strongest first: how the parser collapses selected reasons to `ruleCategory`. */
export const CATEGORY_PRECEDENCE: readonly RuleCategory[] = [
  'FIRST_PERSON',
  'HARD',
  'LOCAL_EVIDENCE',
  'CARRYOVER',
];

export type PolicyEdge = 'parser' | 'frontend';

export const POLICY_THRESHOLDS = {
  assertMinTop: 0.74,
  assertMinMargin: 0.14,
  unsupportedMinTop: 0.8,
  contestedMinTop: 0.86,
} as const;

export type PolicyRuleName =
  | 'low_top'
  | 'tight_margin'
  | 'unsupported_top'
  | 'fallback_alignment'
  | 'question_next_speaker_conflict'
  | 'gender_conflict'
  | 'generic_top_choice'
  | 'parser_suggestion'
  | 'contested_top'
  | 'no_named_candidates'
  | 'speaker_blocked';

export type PolicyExemptionName = 'first_person';

export interface PolicyRule {
  name: PolicyRuleName;
  edges: readonly PolicyEdge[];
}

/** Every suggestion rule of the spec, each scoped to the edge(s) applying it. */
export const POLICY_RULES: readonly PolicyRule[] = [
  { name: 'low_top', edges: ['parser', 'frontend'] },
  { name: 'tight_margin', edges: ['parser'] },
  { name: 'unsupported_top', edges: ['parser'] },
  { name: 'fallback_alignment', edges: ['parser'] },
  { name: 'question_next_speaker_conflict', edges: ['parser'] },
  { name: 'gender_conflict', edges: ['parser'] },
  { name: 'generic_top_choice', edges: ['parser'] },
  { name: 'parser_suggestion', edges: ['frontend'] },
  { name: 'contested_top', edges: ['frontend'] },
  { name: 'no_named_candidates', edges: ['frontend'] },
  { name: 'speaker_blocked', edges: ['frontend'] },
];

/** Exemptions per edge: when one holds, the decision asserts regardless. */
export const POLICY_EXEMPTIONS: Readonly<Record<PolicyEdge, readonly PolicyExemptionName[]>> = {
  parser: [],
  frontend: ['first_person'],
};

/**
 * The first-person rules, for traces written before the parser put
 * `ruleCategory` on the wire. Pinned to the registry's FIRST_PERSON rules by
 * the vector file's spec block.
 */
export const LEGACY_FIRST_PERSON_RULES: ReadonlySet<string> = new Set([
  '0p_protagonist_first_person',
  'first_person_override',
]);

/** Inputs to the policy at one edge (only the edge's rules read theirs). */
export interface PolicyFacts {
  top: number;
  second: number;
  candidateCount: number;
  usedFallbackAlignment?: boolean;
  questionNextSpeakerConflict?: boolean;
  genderConflict?: boolean;
  genericTopChoice?: boolean;
  hasSupportReason?: boolean;
  parserSuggestion?: boolean;
  /** Defaults to true. */
  hasNamedCandidates?: boolean;
  speakerBlocked?: boolean;
  firstPerson?: boolean;
}

export type PolicyFlags = Omit<PolicyFacts, 'top' | 'second' | 'candidateCount'>;

export interface PolicyOutcome {
  isSuggestion: boolean;
  firedRules: PolicyRuleName[];
  exemptedBy: PolicyExemptionName | null;
}

/** Facts for a ranked confidence list (top first) plus edge flags. */
export function policyFactsFromConfidences(
  confidences: readonly number[],
  flags: PolicyFlags = {},
): PolicyFacts {
  return {
    ...flags,
    top: confidences.length > 0 ? confidences[0] : 0,
    second: confidences.length > 1 ? confidences[1] : 0,
    candidateCount: confidences.length,
  };
}

function ruleFires(name: PolicyRuleName, facts: PolicyFacts): boolean {
  switch (name) {
    case 'low_top':
      return facts.top < POLICY_THRESHOLDS.assertMinTop;
    case 'tight_margin':
      return (facts.top - facts.second) < POLICY_THRESHOLDS.assertMinMargin;
    case 'unsupported_top':
      return !facts.hasSupportReason && facts.top < POLICY_THRESHOLDS.unsupportedMinTop;
    case 'fallback_alignment':
      return !!facts.usedFallbackAlignment;
    case 'question_next_speaker_conflict':
      return !!facts.questionNextSpeakerConflict;
    case 'gender_conflict':
      return !!facts.genderConflict;
    case 'generic_top_choice':
      return !!facts.genericTopChoice;
    case 'parser_suggestion':
      return !!facts.parserSuggestion;
    case 'contested_top':
      return facts.candidateCount > 1 && facts.top < POLICY_THRESHOLDS.contestedMinTop;
    case 'no_named_candidates':
      return facts.hasNamedCandidates === false;
    case 'speaker_blocked':
      return !!facts.speakerBlocked;
  }
}

function exemptionHolds(name: PolicyExemptionName, facts: PolicyFacts): boolean {
  switch (name) {
    case 'first_person':
      return !!facts.firstPerson;
  }
}

/** Apply the shared spec's `edge`-scoped rules to `facts`. */
export function evaluatePolicy(edge: PolicyEdge, facts: PolicyFacts): PolicyOutcome {
  const firedRules = POLICY_RULES
    .filter((rule) => rule.edges.includes(edge) && ruleFires(rule.name, facts))
    .map((rule) => rule.name);
  for (const exemption of POLICY_EXEMPTIONS[edge]) {
    if (exemptionHolds(exemption, facts)) {
      return { isSuggestion: false, firedRules, exemptedBy: exemption };
    }
  }
  return { isSuggestion: firedRules.length > 0, firedRules, exemptedBy: null };
}

/** The part of a decision trace the first-person exemption reads. */
export interface DecisionTraceCategoryView {
  ruleCategory?: RuleCategory | string | null;
  selectedReasons?: unknown;
}

/**
 * The frontend edge's first-person exemption test on a decision trace:
 * the wire's `ruleCategory` when the trace carries the field, else (legacy
 * trace) its selected reasons against LEGACY_FIRST_PERSON_RULES.
 */
export function traceIsFirstPerson(trace: DecisionTraceCategoryView | null | undefined): boolean {
  if (!trace || typeof trace !== 'object') return false;
  if ('ruleCategory' in trace) return trace.ruleCategory === 'FIRST_PERSON';
  const reasons = trace.selectedReasons;
  if (!Array.isArray(reasons)) return false;
  return reasons.some((reason) => typeof reason === 'string' && LEGACY_FIRST_PERSON_RULES.has(reason));
}
