/* 깨비지지 공개 덱 빌더가 사용하는 원본 1분 DPS 계산기로 보유 도술을 교차검증한다. */
const base = "https://kkaebigg.com";
const owned = new Set((process.argv[2] || "").split(",").filter(Boolean).map(Number));
globalThis.self = globalThis;
globalThis.window = globalThis;
const noop = () => {};
globalThis.document = {
  currentScript: null, addEventListener: noop, querySelector: () => null,
  querySelectorAll: () => [], dispatchEvent: noop,
};
globalThis.CustomEvent = function CustomEvent() {};

const page = await fetch(`${base}/deck/`).then((response) => response.text());
const version = /deck-rec-worker\.js\?v=([\w-]+)/.exec(page)?.[1] || "";
const query = version ? `?v=${version}` : "";
const [numbersCode, coreCode, skills, recommendations] = await Promise.all([
  fetch(`${base}/static/js/skill-numbers.js${query}`).then((response) => response.text()),
  fetch(`${base}/static/js/deck-rec-core.js${query}`).then((response) => response.text()),
  fetch(`${base}/data/skills.json`).then((response) => response.json()),
  fetch(`${base}/data/deck_rec.json`).then((response) => response.json()),
]);
(0, eval)(numbersCode);
(0, eval)(coreCode);
globalThis.KKSkillNumbers.useData(skills);
const context = globalThis.KKDeckRecCore.create(globalThis.KKSkillNumbers, skills, recommendations);
const ownedIds = [...owned];
const rows = [];
for (let style = 0; style < skills.styles.length; style += 1) {
  const count = ownedIds.filter((id) => skills.skills.find((entry) => entry.id === id)?.st === style).length;
  if (count < 3) continue;
  const deck = context.searchDps(style, { require: ownedIds, levels: 0, steps: null });
  if (deck.fail) continue;
  rows.push({
    main: deck.main, dps: Number(deck.dps.toFixed(2)), core: deck.core,
    ext: deck.ext, cast_order: deck.orders[8] || [], ultimate: deck.ult,
    core_main_count: deck.n5[8],
  });
}
rows.sort((left, right) => right.dps - left.dps);
process.stdout.write(JSON.stringify({ version, rows }));
