/**
 * Unified line type that works with both v1.0 and v2.0 dialogue formats
 */
export type UnifiedLine = {
  id: number;
  text: string;
  span: { start: number; end: number } | null;
  characterName: string | null;  // Normalized field name
  candidates: { name: string; confidence: number }[];
  isConflict: boolean;
  attribution?: {
    confidence: number;
    topCandidateConfidence: number;
    marginToSecond: number;
    misattributionRisk: number;
    resolutionStatus: 'auto' | 'unknown' | 'user_confirmed';
    thresholdUsed: number;
    sourceAlias: string | null;
    sourceCandidates?: string[];
    candidates: { name: string; characterId?: string | null; confidence: number; reasons?: string[] }[];
  };
};

/**
 * Context for character menu operations
 */
export type MenuContext = 
  | { kind: 'line'; lineIds: number[] }
  | { kind: 'paragraph'; lineIds: number[] }
  | { kind: 'selection'; lineIds: number[] };


