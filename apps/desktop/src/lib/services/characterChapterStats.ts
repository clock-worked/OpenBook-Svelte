// Superseded by `characterStats.ts` (candidate 5): the per-chapter count now
// lives beside the book-wide answer. This shim is removed once the call
// sites are rewired.
export { chapterLineCounts as computeChapterLineCounts } from './characterStats';
export type { ChapterLineIdentity } from './characterStats';
