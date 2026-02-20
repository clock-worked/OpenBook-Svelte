# UI / UX

### Layout
- 3 panels (20-60-20) with sticky conflict toolbar above center.

### Left TOC
- Chapter titles; icons for parsed, complete, audio-ready.

### Center Chapter View
- 80% text width; colored highlights by speaker (50% opacity).
- Hover left margin shows speaker names for paragraph; hovering a name raises opacity of their spans.
- Sticky toolbar: Regenerate, conflicts count, prev/next, estimated cost.
- Text selectable (not editable). Selecting enables assignment.
- Conflicts show gradient between candidate speaker colors.

### Speaker editing
- Click a highlighted span: opens a dropdown/tooltip listing candidate speakers for that line plus known characters. Choosing a name sets that line's `chosenSpeaker`.
- Mouse selection: when you mouse up after selecting text spanning one or more lines, a floating dropdown appears near the selection with the same speaker list; choosing a name assigns that speaker to all covered lines.
- Click the paragraph speaker chip (in the left margin): opens the dropdown; choosing a name assigns that speaker to all lines in that paragraph.
- Immediate feedback: highlight color updates instantly; the sticky toolbar conflict count recalculates.
- Empty candidate sets: still show all known characters with search; unknown names are not allowed here (add characters via the Right panel first).

#### Loading state
- When a chapter is selected, show a vertical stack of paragraph-sized skeletons (grey blocks) while loading.
- Skeletons should pulse top-to-bottom using a subtle gradient animation.
- If no script exists yet, load and display raw text paragraphs until script is generated.

### Right Characters Panel
- Color swatch, name, Apply button (visible when selection active).
- Expand to choose voice per character.
- Bottom button: Generate audio (disabled until conflicts resolved); becomes Regenerate audio if WAVs exist.


