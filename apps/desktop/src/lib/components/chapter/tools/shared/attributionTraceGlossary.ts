/**
 * Shared glossary for attribution decision traces.
 *
 * The parser writes machine reason codes (e.g. `recent_named_mention`) and
 * signal keys (e.g. `descriptorMatch`) into `decisionTrace`. This module
 * turns those codes into plain-English phrases with the signal values
 * interpolated in, so the same glossary can serve both the compact hover
 * card (ParagraphText) and the full Trace tab (AttributionTracePanel).
 */

export type TraceSignals = Record<string, any>;

const firstSignal = (signals: TraceSignals, key: string): string | null => {
    const value = signals?.[key];
    if (value == null) return null;
    if (Array.isArray(value)) return value.length > 0 ? String(value[0]) : null;
    return String(value);
};

/**
 * Human-readable phrase for a single reason code.
 *
 * `name` is the candidate the reason applies to; `signals` are the trace
 * signals the phrase interpolates (e.g. the matched descriptor text).
 * Unknown codes fall back to a readable title-case label so new parser
 * reasons never render as raw snake_case.
 */
export const humanizeReason = (code: string, name?: string | null, signals?: TraceSignals): string => {
    const noun = name ?? 'this character';
    switch (code) {
        case 'parser_match':
            return `matches the parser's original assignment of ${noun}`;
        case 'legacy_match':
            return `matches the legacy rule's assignment of ${noun}`;
        case 'legacy_anchor_boost':
            return 'boosted by a legacy rule anchor (e.g. "name said" in nearby text)';
        case 'same_paragraph_continuation':
            return `continues the same paragraph as ${noun}'s previous line`;
        case 'prev_continuity':
            return `the previous dialogue line was spoken by ${noun}`;
        case 'next_continuity':
            return `the next dialogue line was spoken by ${noun}`;
        case 'two_sided_continuation':
            return `sandwiched between dialogue from ${noun} on both sides`;
        case 'coref_support':
            return `pronouns nearby ("he"/"she") point back to ${noun}`;
        case 'alias_strength':
            return `strong alias match for ${noun}`;
        case 'learned_descriptor_match': {
            const descriptor = firstSignal(signals, 'descriptorMatch');
            return descriptor ? `matches the learned descriptor "${descriptor}"` : `matches a learned descriptor of ${noun}`;
        }
        case 'post_quote_attribution':
            return 'a speech tag right after the quote names the speaker';
        case 'recent_named_mention': {
            const mentioned = firstSignal(signals, 'recentNamedMentionBeforeQuote');
            return mentioned ? `named just before the quote: ${mentioned}` : `named near the quote: ${noun}`;
        }
        case 'previous_paragraph_named_mention': {
            const mentioned = firstSignal(signals, 'previousParagraphNamedMention');
            return mentioned ? `named in the previous paragraph: ${mentioned}` : `named in the previous paragraph: ${noun}`;
        }
        case 'speech_verb':
            return 'a speech verb ("said", "asked") anchors the line to a speaker';
        case 'fallback_penalty':
            return 'lowered: no direct evidence — fallback alignment was used';
        case 'generic_label_penalty':
            return `lowered: ${noun} is a generic label, not a specific name`;
        case 'addressed_target_penalty':
            return `lowered: ${noun} appears to be the person being addressed, not the speaker`;
        case 'self_name_mention_penalty':
            return `lowered: ${noun} refers to itself inside the quote, which usually means someone else is speaking`;
        case 'context_gender_match': {
            const cue = firstSignal(signals, 'contextGenderCue');
            return cue ? `context pronouns ("${cue}") match ${noun}'s gender` : `context pronouns match ${noun}'s gender`;
        }
        case 'context_gender_conflict':
            return `lowered: context pronouns conflict with ${noun}'s gender`;
        case 'question_next_speaker_penalty':
            return 'lowered: the line ends with a question, so the next speaker is unlikely to be the same';
        case 'closed_world_unresolved':
            return 'no character in this chapter could be confirmed';
        case 'first_person_override':
            return 'overridden: first-person voice points to the speaker';
        case '0p_protagonist_first_person':
            return 'overridden: first-person speech assigned to the protagonist';
        case 'same_paragraph_context_consistency':
            return 'overridden: kept consistent with the surrounding paragraph context';
        case 'episode_joint_decode':
            return 'overridden: the joint episode decoder re-assigned the speaker';
        case 'modernbooknlp_joint_attribution':
            return 'attributed by the ModernBookNLP joint decoder';
        case 'jev_verification_disagreement':
            return 'flagged: an independent verification pass picked a different speaker';
        case 'jev_verification_confirmed':
            return 'confirmed by an independent verification pass';
        default:
            return titleCase(code);
    }
};

/** Readable labels for the raw trace signals, in display order. */
const SIGNAL_LABELS: Record<string, string> = {
    usedFallbackAlignment: 'Used fallback alignment',
    exactQuoteMatch: 'Exact quote match',
    quoteBoundaryQuality: 'Quote boundary quality',
    nearbySpeechVerb: 'Nearby speech verb',
    hasCorefName: 'Coref name present',
    aliasMatchStrength: 'Alias match strength',
    addressedNamePrevSentence: 'Addressed in previous sentence',
    explicitQuoteMentions: 'Explicit quote mentions',
    nextSentenceAttributedSpeaker: 'Next sentence attributed to',
    recentNamedMentionBeforeQuote: 'Named mention before quote',
    previousParagraphNamedMention: 'Named mention in previous paragraph',
    descriptorMatch: 'Descriptor match',
    isReturning: 'Is returning character',
    continuesParagraphDialogue: 'Continues paragraph dialogue',
    lineEndsWithQuestion: 'Line ends with question',
    nextDialogueLegacy: 'Next dialogue (legacy)',
    contextGender: 'Context gender',
    contextGenderCue: 'Context gender cue',
    sourceCandidate: 'Source candidate'
};

const titleCase = (code: string): string =>
    code.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/\b\w/g, (c) => c.toUpperCase());

const formatSignalValue = (value: any): string => {
    if (typeof value === 'boolean') return value ? 'yes' : 'no';
    if (Array.isArray(value)) return value.join(', ');
    return String(value);
};

/**
 * Ordered {label, value} entries for the raw-signals expander.
 * Known keys come first (in a fixed order); unknown keys are kept with a
 * title-cased label so new signals survive parser upgrades.
 * Null/undefined values are skipped.
 */
export const signalEntries = (signals?: TraceSignals): { label: string; value: string }[] => {
    if (!signals) return [];
    const entries: { label: string; value: string }[] = [];
    const seen = new Set<string>();
    for (const key of Object.keys(SIGNAL_LABELS)) {
        const value = signals[key];
        if (value == null) continue;
        seen.add(key);
        entries.push({ label: SIGNAL_LABELS[key], value: formatSignalValue(value) });
    }
    for (const [key, value] of Object.entries(signals)) {
        if (seen.has(key) || value == null) continue;
        entries.push({ label: titleCase(key), value: formatSignalValue(value) });
    }
    return entries;
};

const PENALTY_REASONS = new Set([
    'fallback_penalty',
    'generic_label_penalty',
    'addressed_target_penalty',
    'self_name_mention_penalty',
    'context_gender_conflict',
    'question_next_speaker_penalty'
]);

/** True when the reason code represents a score penalty rather than support. */
export const isPenaltyReason = (code: string): boolean => PENALTY_REASONS.has(code);

export interface TraceExplanation {
    /** One-line summary: who was chosen and the top reason. Empty when there is no trace. */
    headline: string;
    /** Longer plain-English body (all reasons, as readable sentences). May be ''. */
    detail: string;
    /** Humanized override story (episode decoder, first-person, …). Null when no override. */
    override: string | null;
}

/**
 * Plain-English "why was this line attributed this way" narrative for a
 * single line's decision trace. Shared by the compact hover card (headline)
 * and the full Trace tab (headline + detail + override).
 */
export const buildTraceExplanation = (trace: {
    selectedCandidate?: string | null;
    selectedReasons?: string[];
    overrideReason?: string | null;
    signals?: TraceSignals;
} | null): TraceExplanation => {
    if (!trace) return { headline: '', detail: '', override: null };
    const signals = trace.signals ?? {};
    let override = trace.overrideReason
        ? humanizeReason(trace.overrideReason, trace.selectedCandidate, signals)
        : null;
    const episode = signals.episodeDecoder as
        | { originalCandidate?: string; selectedCandidate?: string; participants?: string[] }
        | undefined;
    if (override && episode) {
        const participants = (episode.participants ?? []).join(', ');
        const original = episode.originalCandidate ? ` (originally "${episode.originalCandidate}")` : '';
        const chosen =
            episode.selectedCandidate && episode.selectedCandidate !== trace.selectedCandidate
                ? `, chosen "${episode.selectedCandidate}"`
                : '';
        override = `${override}${original}${chosen}${participants ? `, episode participants: ${participants}` : ''}`;
    }
    const mbnlp = signals.modernBookNLP as
        | { corefId?: string; nominalMentions?: string[]; previousSpeaker?: string | null; proposedSpeaker?: string }
        | undefined;
    if (override && mbnlp) {
        const mentions = (mbnlp.nominalMentions ?? []).join(', ');
        const coref = mbnlp.corefId ? `coref ${mbnlp.corefId}` : '';
        const parts = [coref, mentions ? `mentions: ${mentions}` : '', mbnlp.proposedSpeaker ? `proposed "${mbnlp.proposedSpeaker}"` : '']
            .filter(Boolean)
            .join(', ');
        const decision = signals.modernBookNLPDecision ? `, decision: ${signals.modernBookNLPDecision}` : '';
        override = override + (parts ? ` (${parts}${decision})` : decision);
    }

    if (!trace.selectedCandidate) {
        const source = signals.sourceCandidate ? ` (source: "${signals.sourceCandidate}")` : '';
        return { headline: `No character confirmed${source}`, detail: '', override };
    }

    const reasons = trace.selectedReasons ?? [];
    const top = reasons[0];
    const headline = top
        ? `"${trace.selectedCandidate}" — ${humanizeReason(top, trace.selectedCandidate, signals)}`
        : `"${trace.selectedCandidate}"`;
    const detail = reasons
        .map((reason) => humanizeReason(reason, trace.selectedCandidate, signals))
        .join('; ');
    return { headline, detail, override };
};
