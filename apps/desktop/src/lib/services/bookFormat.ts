// The one owner of the book format (ADR 0021): which documents a book has,
// where each lives, which schema each is in, and the read-legacy /
// write-current rule (ADR 0003).
//
//   const book = openBook(root);
//   await book.read('roster', chapter);          // null when absent
//   await book.write('voices', voices);          // records history
//   await book.write('roster', chapter, roster); // refused in a v3 book
//
// Callers name a document, never a path. The layout table, the v2/v3
// decision, the legacy-read fallbacks and "absent means empty" live here.
// File access goes through the fs transport (File System Access or the
// sidecar), so both adapters see the same rules.

import type { CharactersJson, DialogueJson, ScriptJson, VoicesJson } from '$lib/types';
import {
    readCentralCharacters,
    readCharacterFolder,
    readOptionalJsonRelative,
    writeCentralCharacters,
    writeDialogueSave,
    writeEditableJson,
    type WriteOutcome,
} from '$lib/services/fs';

// === Schemas ===

/**
 * The dialogue.json format versions read as a versioned (DialogueJson)
 * document; anything else is a legacy script.
 */
export const DIALOGUE_FORMAT_VERSIONS: readonly string[] = ['2.0', '3.0', '3.1', '3.2'];

export function isVersionedDialogue(data: unknown): data is DialogueJson {
    return (
        !!data
        && typeof data === 'object'
        && 'formatVersion' in data
        && DIALOGUE_FORMAT_VERSIONS.includes(String((data as DialogueJson).formatVersion))
    );
}

/** The book's character format: v3 keeps one file per character in `characters/`. */
export type BookVersion = '2.0' | '3.0';

// === Documents ===

/** Documents that belong to one chapter. */
export type ChapterDoc = 'dialogue' | 'script' | 'roster';
/** Documents that belong to the whole book. */
export type RootDoc = 'voices' | 'settings' | 'characters' | 'speakerBlocklist';
export type BookDoc = ChapterDoc | RootDoc;

export interface BookDocValues {
    /** `<Ch>/dialogue.json`: the current chapter document. */
    dialogue: DialogueJson;
    /** `<Ch>/<Ch>.script.json`: the v1 chapter document (legacy). */
    script: ScriptJson;
    /** `<Ch>/<Ch>.characters.json`: the v2 chapter roster, raw (retired in v3). */
    roster: CharactersJson;
    voices: VoicesJson;
    settings: Record<string, any>;
    /** The book's characters: the v3 `characters/` folder, else the v2 `characters.json`. */
    characters: CharactersJson;
    /** `speaker_blocklist.json`: read-only legacy document. */
    speakerBlocklist: any;
}

const CHAPTER_DOCS: ReadonlySet<string> = new Set<ChapterDoc>(['dialogue', 'script', 'roster']);

/**
 * Where each document lives, relative to the book root. Private: callers
 * name documents. `layoutPath` exposes it for the two jobs that need a raw
 * path (history capture and the migration's file inventory).
 */
const LAYOUT = {
    dialogue: (chapter: string) => `${chapter}/dialogue.json`,
    dialogueOld: (chapter: string) => `${chapter}/dialogue-old.json`,
    script: (chapter: string) => `${chapter}/${chapter}.script.json`,
    roster: (chapter: string) => `${chapter}/${chapter}.characters.json`,
    voices: () => 'voices.json',
    settings: () => 'settings.json',
    characters: () => 'characters.json',
    legacyBookCharacters: () => 'book.characters.json',
    speakerBlocklist: () => 'speaker_blocklist.json',
    characterFolder: () => 'characters',
    characterFile: (fileName: string) => `characters/${fileName}`,
} as const;

export type LayoutEntry = keyof typeof LAYOUT;

/**
 * The raw path of a layout entry. Only for history capture and history
 * change routing, and the migration's file inventory; everything else reads
 * and writes through a `Book`.
 */
export function layoutPath(entry: LayoutEntry, name = ''): string {
    if (entry in CHAPTER_LAYOUT_ENTRIES || entry === 'characterFile') {
        if (!name) throw new Error(`[bookFormat] ${entry} needs a ${entry === 'characterFile' ? 'file name' : 'chapter'}`);
    }
    return (LAYOUT[entry] as (arg: string) => string)(name);
}

const CHAPTER_LAYOUT_ENTRIES: Record<string, true> = { dialogue: true, dialogueOld: true, script: true, roster: true };

/** What a book-relative path is, for routing history changes. */
export type ClassifiedPath =
    | { doc: ChapterDoc; chapter: string }
    | { doc: RootDoc | 'legacyBookCharacters' }
    | { doc: 'characterFile'; fileName: string };

export function classifyBookPath(path: string): ClassifiedPath | null {
    const normalized = String(path ?? '').replace(/\\/g, '/').replace(/^\/+/, '');
    switch (normalized) {
        case 'voices.json': return { doc: 'voices' };
        case 'settings.json': return { doc: 'settings' };
        case 'characters.json': return { doc: 'characters' };
        case 'book.characters.json': return { doc: 'legacyBookCharacters' };
        case 'speaker_blocklist.json': return { doc: 'speakerBlocklist' };
    }
    const parts = normalized.split('/');
    if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
    const [dir, file] = parts;
    if (dir === 'characters') return file.toLowerCase().endsWith('.json') ? { doc: 'characterFile', fileName: file } : null;
    if (file === 'dialogue.json') return { doc: 'dialogue', chapter: dir };
    if (file === `${dir}.script.json`) return { doc: 'script', chapter: dir };
    if (file === `${dir}.characters.json`) return { doc: 'roster', chapter: dir };
    return null;
}

// === Book ===

export interface BookWriteOptions {
    /** Record an undo step (default true). Metadata stamps pass false. */
    history?: boolean;
    /** Write the document into a backup tree instead (`<backupDir>/<path>`); never recorded. */
    backupDir?: string;
    /**
     * The v3 migration (and an explicit user roster add) may write chapter
     * rosters in a v3 book; nothing else may (ADR 0003, issue #2).
     */
    allowRetiredInV3?: boolean;
}

export interface Book {
    readonly root: string;
    /** Memoized per Book: open a new one after a migration. */
    version(): Promise<BookVersion>;

    /** Read a document; absent (or unreadable) means `null`, never an error. */
    read<D extends ChapterDoc>(doc: D, chapter: string): Promise<BookDocValues[D] | null>;
    read<D extends RootDoc>(doc: D): Promise<BookDocValues[D] | null>;

    /**
     * Write a document. Returns false when the write failed or the format
     * refuses it (a chapter roster in a v3 book; the v2 `characters.json`
     * in a v3 book, whose characters are one file each).
     */
    write<D extends ChapterDoc>(doc: D, chapter: string, value: BookDocValues[D], options?: BookWriteOptions): Promise<boolean>;
    write<D extends Exclude<RootDoc, 'speakerBlocklist'>>(doc: D, value: BookDocValues[D], options?: BookWriteOptions): Promise<boolean>;

    /**
     * A chapter's dialogue with the legacy fallbacks: a versioned
     * dialogue.json, else the v1 script, else dialogue.json as a
     * non-versioned script, else dialogue-old.json.
     */
    readChapterDialogue(chapter: string): Promise<DialogueJson | ScriptJson | null>;

    /**
     * The chapter dialogue save (kind `dialogueSave`), for the chapter
     * persistence owner, which runs the stats step from the outcome.
     */
    saveDialogue(chapter: string, value: DialogueJson): Promise<WriteOutcome | null>;
}

async function detectVersion(root: string): Promise<BookVersion> {
    // A populated characters/ folder is the v3 book's live character set.
    try {
        const folder = await readCharacterFolder(root);
        return folder && folder.characters.length > 0 ? '3.0' : '2.0';
    } catch (error) {
        console.warn('[bookFormat] characters/ folder read failed; treating the book as v2:', error);
        return '2.0';
    }
}

function docPath(doc: BookDoc, chapter: string | undefined): string {
    if (CHAPTER_DOCS.has(doc) && !chapter) throw new Error(`[bookFormat] ${doc} needs a chapter`);
    return (LAYOUT[doc] as (arg: string) => string)(chapter ?? '');
}

export function openBook(root: string): Book {
    let versionPromise: Promise<BookVersion> | null = null;
    const version = () => (versionPromise ??= detectVersion(root));

    async function read(doc: BookDoc, chapter?: string): Promise<any> {
        if (doc === 'characters') return readCentralCharacters(root);
        return readOptionalJsonRelative<any>(docPath(doc, chapter));
    }

    async function write(doc: BookDoc, ...args: any[]): Promise<boolean> {
        const chapter: string | undefined = CHAPTER_DOCS.has(doc) ? args.shift() : undefined;
        const [value, options = {}] = args as [unknown, BookWriteOptions | undefined];
        if (doc === 'speakerBlocklist') throw new Error('[bookFormat] speakerBlocklist is read-only');

        const path = docPath(doc, chapter);
        if (options.backupDir) {
            return writeEditableJson(`${options.backupDir.replace(/\/+$/, '')}/${path}`, value, false);
        }
        if ((doc === 'roster' || doc === 'characters') && !options.allowRetiredInV3 && (await version()) === '3.0') {
            console.warn(`[bookFormat] v3 book: ${path} is retired; write refused`);
            return false;
        }
        const recordHistory = options.history ?? true;
        if (doc === 'characters') return writeCentralCharacters(root, value as CharactersJson, recordHistory);
        return writeEditableJson(path, value, recordHistory);
    }

    async function readChapterDialogue(chapter: string): Promise<DialogueJson | ScriptJson | null> {
        try {
            const dialogue = await readOptionalJsonRelative<DialogueJson | ScriptJson>(LAYOUT.dialogue(chapter));
            if (isVersionedDialogue(dialogue)) return dialogue;
            const script = await readOptionalJsonRelative<ScriptJson>(LAYOUT.script(chapter));
            if (script) return script;
            if (dialogue) return dialogue as ScriptJson;
            return await readOptionalJsonRelative<ScriptJson>(LAYOUT.dialogueOld(chapter));
        } catch {
            return null;
        }
    }

    return {
        root,
        version,
        read: read as Book['read'],
        write: write as Book['write'],
        readChapterDialogue,
        saveDialogue: (chapter, value) => writeDialogueSave(LAYOUT.dialogue(chapter), value),
    };
}
