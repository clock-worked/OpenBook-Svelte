// @vitest-environment jsdom
/**
 * Regression test: dragging a book character onto the chapter character list
 * must be accepted as a drop target.
 *
 * Browser DnD semantics (the part that bit us):
 *   - dragover:  dataTransfer.getData() ALWAYS returns '' (browser blocks
 *                reading payload mid-drag). Only dataTransfer.types is
 *                available to inspect what is being dragged.
 *   - drop:      getData() returns the real payload.
 *   - A drop is only delivered to an element whose dragover handler called
 *     event.preventDefault(). If the handler gates preventDefault() on a
 *     getData() read, the gate never passes and the browser silently
 *     cancels the drop ("nothing happens").
 *
 * BrowserDataTransfer below models exactly those semantics, so a handler
 * that relies on getData() during dragover goes RED here, and a handler
 * that inspects .types goes GREEN.
 */
import { mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ChapterCharacterList from '../ChapterCharacterList.svelte';

const BOOK_MIME = 'application/x-openbook-book-character-name';

type DragPhase = 'dragover' | 'drop';

class BrowserDataTransfer {
  payload = new Map<string, string>();
  effectAllowed = '';
  dropEffect = '';

  constructor(private phase: DragPhase) {}

  get types(): string[] {
    return Array.from(this.payload.keys());
  }

  setData(type: string, data: string): void {
    this.payload.set(type, String(data));
  }

  getData(type: string): string {
    // Mid-drag the browser hides the payload from the handler.
    return this.phase === 'drop' ? (this.payload.get(type) ?? '') : '';
  }
}

// jsdom has no DragEvent global; a MouseEvent with a dataTransfer property
// is all the component handler touches.
function makeDragEvent(type: DragPhase, dt: BrowserDataTransfer): MouseEvent {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: dt });
  return event;
}

function seedBookDrag(name: string): { dragover: BrowserDataTransfer; drop: BrowserDataTransfer } {
  // What the book-pill dragstart writes.
  const seed = new Map<string, string>([
    [BOOK_MIME, name],
    ['text/plain', name],
  ]);
  const dragover = new BrowserDataTransfer('dragover');
  const drop = new BrowserDataTransfer('drop');
  for (const [type, value] of seed) {
    dragover.payload.set(type, value);
    drop.payload.set(type, value);
  }
  return { dragover, drop };
}

function mountList(props: Record<string, unknown>) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(ChapterCharacterList, {
    target,
    props: {
      items: [],
      openColorIndex: null,
      selectionActive: false,
      onSelect: vi.fn(),
      onToggleColor: vi.fn(),
      onSetColor: vi.fn(),
      onApplyToSelection: vi.fn(),
      onMerge: vi.fn(),
      onDropBookCharacter: vi.fn(),
      onDelete: vi.fn(),
      onJumpToNext: vi.fn(),
      ...props,
    },
  });
  return { target, component, onDropBookCharacter: props.onDropBookCharacter as ReturnType<typeof vi.fn> };
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('chapter character list as book-character drop target', () => {
  it('allows drop over the empty list container (dragover must preventDefault)', () => {
    const onDropBookCharacter = vi.fn();
    const { target, component } = mountList({ items: [], onDropBookCharacter });

    const container = target.querySelector('.chapter-list-drop');
    expect(container).not.toBeNull();

    const { dragover, drop } = seedBookDrag('Jacob');

    const over = makeDragEvent('dragover', dragover);
    container!.dispatchEvent(over);
    // A dragover that is not prevented means the browser refuses the drop.
    expect(over.defaultPrevented).toBe(true);

    const dropEvent = makeDragEvent('drop', drop);
    container!.dispatchEvent(dropEvent);
    expect(dropEvent.defaultPrevented).toBe(true);
    expect(onDropBookCharacter).toHaveBeenCalledWith('Jacob');

    unmount(component);
  });

  it('allows drop onto an existing chapter row', () => {
    const onDropBookCharacter = vi.fn();
    const { target, component } = mountList({
      items: [{ character: { name: 'Alice', color: null }, origIndex: 0 }],
      onDropBookCharacter,
    });

    const row = target.querySelector('.character-row');
    expect(row).not.toBeNull();

    const { dragover, drop } = seedBookDrag('Jacob');

    const over = makeDragEvent('dragover', dragover);
    row!.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(true);

    const dropEvent = makeDragEvent('drop', drop);
    row!.dispatchEvent(dropEvent);
    expect(onDropBookCharacter).toHaveBeenCalledWith('Jacob');

    unmount(component);
  });

  it('allows drop over the container gap when rows exist', () => {
    const onDropBookCharacter = vi.fn();
    const { target, component } = mountList({
      items: [{ character: { name: 'Alice', color: null }, origIndex: 0 }],
      onDropBookCharacter,
    });

    const container = target.querySelector('.chapter-list-drop');
    const { dragover, drop } = seedBookDrag('Jacob');

    const over = makeDragEvent('dragover', dragover);
    container!.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(true);

    const dropEvent = makeDragEvent('drop', drop);
    container!.dispatchEvent(dropEvent);
    expect(onDropBookCharacter).toHaveBeenCalledWith('Jacob');

    unmount(component);
  });
});
