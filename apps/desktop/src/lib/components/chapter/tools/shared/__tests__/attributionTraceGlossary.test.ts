import { describe, expect, it } from 'vitest';
import { buildTraceExplanation, humanizeReason, isPenaltyReason, signalEntries } from '../attributionTraceGlossary';

describe('buildTraceExplanation', () => {
    it('summarizes the chosen candidate with its top reason', () => {
        const ex = buildTraceExplanation({
            selectedCandidate: 'Ratface',
            selectedReasons: ['recent_named_mention'],
            signals: { recentNamedMentionBeforeQuote: 'Ratface' }
        });
        expect(ex.headline).toContain('Ratface');
        expect(ex.headline).toContain('named just before the quote');
    });

    it('reports unresolved lines with the source candidate when known', () => {
        const ex = buildTraceExplanation({
            selectedCandidate: null,
            selectedReasons: ['closed_world_unresolved'],
            signals: { sourceCandidate: 'Jacob' }
        });
        expect(ex.headline).toContain('No character');
        expect(ex.headline).toContain('Jacob');
    });

    it('returns an empty explanation when there is no trace', () => {
        const ex = buildTraceExplanation(null);
        expect(ex.headline).toBe('');
    });

    it('tells the episode decoder story: original, chosen, and participants', () => {
        const ex = buildTraceExplanation({
            selectedCandidate: 'Ratface',
            selectedReasons: ['parser_match', 'episode_joint_decode'],
            overrideReason: 'episode_joint_decode',
            signals: {
                episodeDecoder: {
                    algorithm: 'two_participant_viterbi',
                    originalCandidate: 'Jacob',
                    selectedCandidate: 'Ratface',
                    participants: ['Jacob', 'Ratface']
                }
            }
        });
        expect(ex.override).toContain('Jacob');
        expect(ex.override).toContain('Ratface');
        expect(ex.override).toContain('participants');
    });

    it('tells the ModernBookNLP story: coref id, nominal mentions, and the decision', () => {
        const ex = buildTraceExplanation({
            selectedCandidate: 'Ratface',
            selectedReasons: ['modernbooknlp_joint_attribution'],
            overrideReason: 'modernbooknlp_joint_attribution',
            signals: {
                modernBookNLP: {
                    corefId: 'c0012',
                    nominalMentions: ['he', 'the dog'],
                    previousSpeaker: 'Jacob',
                    proposedSpeaker: 'Ratface'
                },
                modernBookNLPDecision: 'accepted'
            }
        });
        expect(ex.override).toContain('c0012');
        expect(ex.override).toContain('the dog');
        expect(ex.override).toContain('accepted');
    });
});

describe('signalEntries', () => {
    it('maps known signal keys to readable labels and formats booleans', () => {
        const entries = signalEntries({ nearbySpeechVerb: true, usedFallbackAlignment: false });
        expect(entries).toContainEqual({ label: 'Nearby speech verb', value: 'yes' });
        expect(entries).toContainEqual({ label: 'Used fallback alignment', value: 'no' });
    });

    it('keeps unknown signal keys with a title-cased label', () => {
        const entries = signalEntries({ someNewSignal: 42 });
        expect(entries).toEqual([{ label: 'Some New Signal', value: '42' }]);
    });

    it('returns an empty list when there are no signals', () => {
        expect(signalEntries(undefined)).toEqual([]);
    });
});

describe('humanizeReason', () => {
    it('names the candidate in mention-based reasons', () => {
        expect(humanizeReason('recent_named_mention', 'Ratface')).toContain('Ratface');
    });

    it('quotes the matched descriptor when the signal carries it', () => {
        const phrase = humanizeReason('learned_descriptor_match', 'Ratface', { descriptorMatch: 'the tall man' });
        expect(phrase).toContain('the tall man');
    });

    it('describes gender evidence with the cue when available', () => {
        const phrase = humanizeReason('context_gender_match', 'Ratface', { contextGenderCue: 'he said' });
        expect(phrase).toContain('he said');
    });

    it('falls back to a readable label for unknown codes', () => {
        expect(humanizeReason('some_future_reason')).toBe('Some Future Reason');
    });
});

describe('isPenaltyReason', () => {
    it('flags penalty codes', () => {
        expect(isPenaltyReason('context_gender_conflict')).toBe(true);
        expect(isPenaltyReason('addressed_target_penalty')).toBe(true);
    });

    it('does not flag support codes', () => {
        expect(isPenaltyReason('speech_verb')).toBe(false);
        expect(isPenaltyReason('unknown_new_code')).toBe(false);
    });
});
