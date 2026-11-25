// ============================================================================
// Attribution policy spec (attributionPolicy.ts): cross-language parity
// vectors + unit tests.
//
// The vector assertions are driven by the shared file
// tests/attribution_policy_vectors.json, which the Python side asserts
// against in py_services/openbook_parser/test_attribution_policy_vectors.py.
// Keep both green when changing either implementation (ADR-0013).
// ============================================================================

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
  CATEGORY_PRECEDENCE,
  LEGACY_FIRST_PERSON_RULES,
  POLICY_EXEMPTIONS,
  POLICY_RULES,
  POLICY_THRESHOLDS,
  RULE_CATEGORIES,
  evaluatePolicy,
  policyFactsFromConfidences,
  traceIsFirstPerson,
} from '../attributionPolicy';
import type { DecisionTraceCategoryView, PolicyEdge, PolicyFlags, PolicyOutcome } from '../attributionPolicy';

interface PolicyVector {
  name: string;
  edge: PolicyEdge;
  confidences: number[];
  flags: PolicyFlags;
  expected: PolicyOutcome;
}

interface FirstPersonVector {
  name: string;
  trace: DecisionTraceCategoryView;
  expected: boolean;
}

interface PolicyVectors {
  description: string;
  spec: {
    thresholds: Record<string, number>;
    rules: Array<{ name: string; edges: string[] }>;
    exemptions: Record<string, string[]>;
    categories: string[];
    categoryPrecedence: string[];
    legacyFirstPersonRules: string[];
  };
  policy: PolicyVector[];
  firstPerson: FirstPersonVector[];
}

// Repo root is six levels up from __tests__/ (services/lib/src/desktop/apps).
const VECTORS_PATH = fileURLToPath(
  new URL('../../../../../../tests/attribution_policy_vectors.json', import.meta.url),
);

const vectors: PolicyVectors = JSON.parse(readFileSync(VECTORS_PATH, 'utf8'));

describe('attributionPolicy parity vectors (tests/attribution_policy_vectors.json)', () => {
  it('spec constants match the vector file', () => {
    expect({ ...POLICY_THRESHOLDS }).toEqual(vectors.spec.thresholds);
    expect(POLICY_RULES.map((rule) => ({ name: rule.name, edges: [...rule.edges] }))).toEqual(vectors.spec.rules);
    expect({ parser: [...POLICY_EXEMPTIONS.parser], frontend: [...POLICY_EXEMPTIONS.frontend] })
      .toEqual(vectors.spec.exemptions);
    expect([...RULE_CATEGORIES]).toEqual(vectors.spec.categories);
    expect([...CATEGORY_PRECEDENCE]).toEqual(vectors.spec.categoryPrecedence);
    expect([...LEGACY_FIRST_PERSON_RULES].sort()).toEqual([...vectors.spec.legacyFirstPersonRules].sort());
  });

  it.each(vectors.policy)('$name', (vector) => {
    const facts = policyFactsFromConfidences(vector.confidences, vector.flags);
    expect(evaluatePolicy(vector.edge, facts)).toEqual(vector.expected);
  });

  it.each(vectors.firstPerson)('first-person exemption: $name', (vector) => {
    expect(traceIsFirstPerson(vector.trace)).toBe(vector.expected);
  });
});

describe('attributionPolicy unit tests', () => {
  it('a trace without the wire field and without reasons is not first-person', () => {
    expect(traceIsFirstPerson(null)).toBe(false);
    expect(traceIsFirstPerson(undefined)).toBe(false);
    expect(traceIsFirstPerson({ selectedReasons: 'first_person_override' })).toBe(false);
  });

  it('hasNamedCandidates defaults to true', () => {
    const outcome = evaluatePolicy('frontend', policyFactsFromConfidences([0.95]));
    expect(outcome).toEqual({ isSuggestion: false, firedRules: [], exemptedBy: null });
  });
});
