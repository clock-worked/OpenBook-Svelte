const fs = require("fs");
const path = require("path");

function readJson(filePath) {
  try {
    const raw = fs.readFileSync(filePath, "utf8");
    return JSON.parse(raw);
  } catch (err) {
    return null;
  }
}

function writeJson(filePath, data) {
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf8");
}

function buildNameToIdMap(bookChars) {
  const map = new Map();
  if (!bookChars || !Array.isArray(bookChars.characters)) return map;
  for (const ch of bookChars.characters) {
    if (ch && ch.name && ch.id) {
      map.set(String(ch.name).toLowerCase(), String(ch.id));
    }
  }
  return map;
}

function ensureSpan(span) {
  if (span && typeof span.start === "number" && typeof span.end === "number")
    return span;
  return { start: 0, end: 0 };
}

function toDialogueJson(script, nameToIdMap) {
  const missingSpeakers = new Set();
  const missingCandidates = new Set();
  const characterBreakdown = {};
  const lines = (script.lines || []).map((l) => {
    let characterId = "narrator";
    if (l.chosenSpeaker) {
      const mapped = nameToIdMap.get(String(l.chosenSpeaker).toLowerCase());
      if (mapped) {
        characterId = mapped;
      } else {
        missingSpeakers.add(String(l.chosenSpeaker));
        characterId = "narrator";
      }
    }

    if (characterId && characterId !== "narrator") {
      characterBreakdown[characterId] =
        (characterBreakdown[characterId] || 0) + 1;
    } else if (characterId === "narrator") {
      characterBreakdown[characterId] =
        (characterBreakdown[characterId] || 0) + 1;
    }

    const candidates = Array.isArray(l.candidates)
      ? l.candidates
          .map((c) => {
            const name = c && c.name ? String(c.name) : "";
            const mapped = name ? nameToIdMap.get(name.toLowerCase()) : null;
            if (name && !mapped) missingCandidates.add(name);
            if (!mapped) return null;
            return {
              characterId: mapped,
              confidence:
                c && typeof c.confidence === "number" ? c.confidence : 0,
            };
          })
          .filter((c) => c)
      : [];

    return {
      id: l.id,
      characterId,
      text: l.text,
      span: ensureSpan(l.span),
      metadata: {
        emotion: null,
        intensity: 1.0,
        pacing: null,
        prefix: null,
        customTags: {},
      },
      candidates,
      isConflict: !!l.isConflict,
    };
  });

  const conflicts = lines.filter((l) => l.isConflict).length;

  return {
    formatVersion: "2.0",
    chapterId: script.chapter || "",
    lines,
    stats: {
      totalLines: lines.length,
      conflicts,
      characterBreakdown,
    },
    missingSpeakers,
    missingCandidates,
  };
}

function main() {
  const bookRoot = process.argv[2];
  if (!bookRoot) {
    console.error("Usage: node migrate-v1-to-v2-dialogue.js <bookRoot>");
    process.exit(1);
  }

  const bookCharsPath = path.join(bookRoot, "characters.json");
  const bookChars = readJson(bookCharsPath);
  const nameToIdMap = buildNameToIdMap(bookChars);

  const entries = fs.readdirSync(bookRoot, { withFileTypes: true });
  let migrated = 0;
  const missingSpeakersAll = new Set();
  const missingCandidatesAll = new Set();

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    if (entry.name.startsWith(".")) continue;

    const chapterDir = path.join(bookRoot, entry.name);
    const scriptPath = path.join(chapterDir, `${entry.name}.script.json`);
    if (!fs.existsSync(scriptPath)) continue;

    const script = readJson(scriptPath);
    if (!script || !Array.isArray(script.lines)) continue;

    const {
      formatVersion,
      chapterId,
      lines,
      stats,
      missingSpeakers,
      missingCandidates,
    } = toDialogueJson(script, nameToIdMap);
    const dialoguePath = path.join(chapterDir, "dialogue.json");

    writeJson(dialoguePath, { formatVersion, chapterId, lines, stats });
    migrated += 1;

    for (const name of missingSpeakers) missingSpeakersAll.add(name);
    for (const name of missingCandidates) missingCandidatesAll.add(name);
  }

  console.log(`Migrated chapters: ${migrated}`);
  if (missingSpeakersAll.size) {
    console.log("Missing speaker IDs (defaulted to narrator):");
    for (const name of missingSpeakersAll) console.log(`- ${name}`);
  }
  if (missingCandidatesAll.size) {
    console.log("Missing candidate IDs (left empty):");
    for (const name of missingCandidatesAll) console.log(`- ${name}`);
  }
}

main();
