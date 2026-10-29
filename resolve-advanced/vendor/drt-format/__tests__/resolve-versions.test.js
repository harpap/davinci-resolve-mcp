/**
 * Resolve version registry + retargetDRT + capability gate.
 * Self-contained (synthetic DRTs) — no client material, no fixtures needed.
 * Run: node --test packages/drt-format/__tests__/resolve-versions.test.js
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const JSZip = require('jszip');

const drt = require('..');
const { VERSIONS, resolveVersion, readVersionStamp, retargetDRT } = require('../resolve-versions');
const { collectCapabilities, checkCapabilities } = require('../capabilities');

// Build a minimal real-schema DRT (SeqContainer/<uuid>.xml + MpFolder), stamped to a version.
async function makeDrt({ dbAppVer, dbPrjVer, body = '', uuid = 'd179c230-0000-0000-0000-fafe4faf207d' }) {
  const seq =
    `<!--DbAppVer="${dbAppVer}" DbPrjVer="${dbPrjVer}"-->\n` +
    `<Sm2SequenceContainer DbId="${uuid}"><Name>T</Name>` +
    `<VideoTrackVec><Element><Sm2TiTrack DbId="t1"><Type>0</Type><Items>${body}</Items></Sm2TiTrack></Element></VideoTrackVec>` +
    `</Sm2SequenceContainer>`;
  const mp = `<!--DbAppVer="${dbAppVer}" DbPrjVer="${dbPrjVer}"-->\n<MpFolder/>`;
  const zip = new JSZip();
  zip.file(`SeqContainer/${uuid}.xml`, seq);
  zip.file('MediaPool/Master/MpFolder.xml', mp);
  return zip.generateAsync({ type: 'nodebuffer' });
}

const MEDIA_CLIP =
  '<Element><Sm2TiVideoClip DbId="c1"><MediaRef>m1</MediaRef>' +
  '<EffectFiltersBA>00</EffectFiltersBA></Sm2TiVideoClip></Element>' +
  '<Element><Sm2TiTransition DbId="x1"><PrettyType>Cross Dissolve</PrettyType></Sm2TiTransition></Element>';

async function seqOf(buf) {
  const z = await JSZip.loadAsync(buf);
  const p = Object.keys(z.files).find((n) => /SeqContainer\/.+\.xml$/.test(n));
  return z.file(p).async('string');
}

test('registry: known versions resolve to verified stamps', () => {
  assert.equal(resolveVersion(19).dbPrjVer, '14');
  assert.equal(resolveVersion(20).dbPrjVer, '15');
  assert.equal(resolveVersion(21).dbPrjVer, '17');
  assert.equal(resolveVersion('21.0').dbAppVer, '21.0.0.0048');
});

test('registry: unknown version throws with guidance', () => {
  assert.throws(() => resolveVersion(99), /no registry entry for Resolve 99/);
});

test('readVersionStamp parses the SeqContainer header', () => {
  assert.deepEqual(
    readVersionStamp('<!--DbAppVer="20.3.2.0009" DbPrjVer="15"-->'),
    { dbAppVer: '20.3.2.0009', dbPrjVer: '15' },
  );
});

test('retargetDRT: re-stamps to each target, preserves content, adds project shell', async () => {
  const src = await makeDrt({ dbAppVer: '21.0.0.0048', dbPrjVer: '17', body: MEDIA_CLIP });
  for (const tgt of [19, 20, 21]) {
    const out = await retargetDRT(src, tgt);
    const z = await JSZip.loadAsync(out);
    const seq = await seqOf(out);
    assert.deepEqual(readVersionStamp(seq), { dbAppVer: VERSIONS[tgt].dbAppVer, dbPrjVer: VERSIONS[tgt].dbPrjVer });
    assert.ok(z.file('project.xml'), 'DRT must carry a project shell');
    assert.equal((seq.match(/<Sm2TiVideoClip\b/g) || []).length, 1, 'clip preserved');
    assert.ok(seq.includes('Cross Dissolve'), 'dissolve preserved');
    // no residual source stamp anywhere
    const all = (await Promise.all(Object.keys(z.files).filter((n) => n.endsWith('.xml')).map((n) => z.file(n).async('string')))).join('');
    if (tgt !== 21) assert.ok(!all.includes('21.0.0.0048'), 'source stamp fully replaced');
  }
});

test('retargetDRT: value-based — same-version retarget is content-stable', async () => {
  const src = await makeDrt({ dbAppVer: '19.1.3.0007', dbPrjVer: '14', body: MEDIA_CLIP });
  const out = await retargetDRT(src, 19);
  assert.deepEqual(readVersionStamp(await seqOf(out)), { dbAppVer: '19.1.3.0007', dbPrjVer: '14' });
});

test('retargetDRT: rejects a DRT with no SeqContainer', async () => {
  const zip = new JSZip();
  zip.file('nope.xml', '<x/>');
  const buf = await zip.generateAsync({ type: 'nodebuffer' });
  await assert.rejects(() => retargetDRT(buf, 19), /no SeqContainer/);
});

test('capabilities: collectCapabilities detects universal elements, no unknowns', async () => {
  const seq = await seqOf(await makeDrt({ dbAppVer: '21.0.0.0048', dbPrjVer: '17', body: MEDIA_CLIP }));
  const { used, unknownElements } = collectCapabilities(seq);
  assert.ok(used.includes('clip:media'));
  assert.ok(used.includes('transition:cross-dissolve'));
  assert.ok(used.includes('transform:basic'));
  assert.deepEqual(unknownElements, [], 'no uncatalogued Sm2Ti* elements');
});

test('capabilities: gate blocks when a capability exceeds target DbPrjVer', () => {
  // universal cap (min 14) is fine for 14, blocked for an older 13
  assert.equal(checkCapabilities(['clip:media'], [], 14).ok, true);
  assert.equal(checkCapabilities(['clip:media'], [], 13).ok, false);
  // an uncatalogued/future capability is treated as Infinity → always blocked
  const r = checkCapabilities(['feature:from-the-future'], [], 17);
  assert.equal(r.ok, false);
  assert.equal(r.blocked[0].cap, 'feature:from-the-future');
});

test('retargetDRT: downgrade allowed for universal content; force bypasses the gate', async () => {
  const src = await makeDrt({ dbAppVer: '21.0.0.0048', dbPrjVer: '17', body: MEDIA_CLIP });
  // universal content → downgrade to 19 allowed
  const ok = await retargetDRT(src, 19);
  assert.deepEqual(readVersionStamp(await seqOf(ok)), { dbAppVer: '19.1.3.0007', dbPrjVer: '14' });
  // force is accepted (no throw) even when present
  const forced = await retargetDRT(src, 19, { force: true });
  assert.ok(forced.length > 0);
});

test('index re-exports retargetDRT + capabilities surface', () => {
  assert.equal(typeof drt.retargetDRT, 'function');
  assert.equal(typeof drt.resolveVersions.resolveVersion, 'function');
  assert.equal(typeof drt.capabilities.collectCapabilities, 'function');
});

// ---- the import gate: <ProjectVersion> in project.xml ----
// Resolve decides "a newer version is needed" on this element, not on the comment
// stamp. Verified live 2026-10-13: a 21.0.3 DRT imported into 19.1.3 only once the
// element read 14. Before this fix retargetDRT moved the comment and left the gate.

const { restampVersions } = require('../resolve-versions');

test('restampVersions moves the <ProjectVersion> gate to the target schema, whatever it was', () => {
  const src = { dbAppVer: '21.0.3.0007', dbPrjVer: '17' };
  const tgt = VERSIONS[19];
  const proj = `<!--DbAppVer="21.0.3.0007" DbPrjVer="17"-->\n<SM_Project><ProjectVersion>17</ProjectVersion><Name>T</Name></SM_Project>`;
  const out = restampVersions(proj, src, tgt);
  assert.ok(out.includes('<ProjectVersion>14</ProjectVersion>'), 'gate moved to 14');
  assert.ok(!out.includes('17'), 'no residual 17 in stamp or gate');
  // a gate that disagrees with the comment stamp (the bundled shell: comment 15, element 15,
  // but a hand-edited file could differ) still lands on the target
  const skew = `<!--DbAppVer="21.0.3.0007" DbPrjVer="17"-->\n<SM_Project><ProjectVersion>15</ProjectVersion></SM_Project>`;
  assert.ok(restampVersions(skew, src, tgt).includes('<ProjectVersion>14</ProjectVersion>'));
});

test('restampVersions: null control — XML without the element gains none, other numbers untouched', () => {
  const seq = `<!--DbAppVer="21.0.3.0007" DbPrjVer="17"-->\n<Sm2SequenceContainer><Version>17</Version><Start>17</Start></Sm2SequenceContainer>`;
  const out = restampVersions(seq, { dbAppVer: '21.0.3.0007', dbPrjVer: '17' }, VERSIONS[19]);
  assert.ok(!out.includes('<ProjectVersion>'), 'no element invented');
  assert.ok(out.includes('<Version>17</Version><Start>17</Start>'), 'unrelated 17s kept');
  assert.deepEqual(readVersionStamp(out), { dbAppVer: '19.1.3.0007', dbPrjVer: '14' });
});

test('retargetDRT: the added shell carries the TARGET gate, not the template\'s own', async () => {
  // The bundled shell is a 20.3.2 export (<ProjectVersion>15</ProjectVersion>); a DRT
  // retargeted to 19 must not ship a shell Resolve 19 refuses.
  const src = await makeDrt({ dbAppVer: '21.0.0.0048', dbPrjVer: '17', body: MEDIA_CLIP });
  for (const tgt of [19, 20, 21]) {
    const z = await JSZip.loadAsync(await retargetDRT(src, tgt));
    const proj = await z.file('project.xml').async('string');
    const gates = proj.match(/<ProjectVersion>(\d+)<\/ProjectVersion>/g) || [];
    assert.equal(gates.length, 1, `shell has exactly one gate (target ${tgt})`);
    assert.equal(gates[0], `<ProjectVersion>${VERSIONS[tgt].dbPrjVer}</ProjectVersion>`);
    assert.deepEqual(readVersionStamp(proj), { dbAppVer: VERSIONS[tgt].dbAppVer, dbPrjVer: VERSIONS[tgt].dbPrjVer });
  }
});
