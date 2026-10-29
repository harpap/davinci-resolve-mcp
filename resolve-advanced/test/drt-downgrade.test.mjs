/**
 * drt.downgrade — stamp <ProjectVersion> down so an older Resolve imports a newer
 *.drt/.drp. Verified version map (live, 2026-06-23): 18.0.4→11, 19.1.x→14, 21.0→17.
 * The GATE is the <ProjectVersion> ELEMENT, not the DbAppVer/DbPrjVer comment.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs/promises';
import JSZip from 'jszip';

import { drtTool } from '../server/tools/drt.mjs';

const tmp = (n) => path.join(os.tmpdir(), `drt-dg-${n}`);

async function makeV17Drt(p) {
  const z = new JSZip();
  z.file('project.xml', '<?xml version="1.0"?>\n<!--DbAppVer="21.0.0.0048" DbPrjVer="17"-->\n<SM_Project><ProjectVersion>17</ProjectVersion></SM_Project>');
  z.file('SeqContainer/abc.xml', '<?xml version="1.0"?>\n<!--DbAppVer="21.0.0.0048" DbPrjVer="17"-->\n<Seq/>');
  await fs.writeFile(p, await z.generateAsync({ type: 'nodebuffer' }));
}

test('downgrade patches the ProjectVersion element + comment stamp (targetAppVersion)', async () => {
  await makeV17Drt(tmp('in .drt'));
  const r = await drtTool.handler({ action: 'downgrade', args: { drtPath: tmp('in .drt'), outputPath: tmp('out.drt'), targetAppVersion: '19.1.3' } });
  assert.equal(r.targetProjectVersion, 14);
  assert.equal(r.projectVersionElementsPatched, 1);
  assert.equal(r.commentStampsPatched, 2); // both members
  const z = await JSZip.loadAsync(await fs.readFile(tmp('out.drt')));
  const proj = await z.file('project.xml').async('string');
  assert.match(proj, /<ProjectVersion>14<\/ProjectVersion>/);
  assert.match(proj, /DbPrjVer="14"/);
  // the other member's stamp is aligned too
  const seq = await z.file('SeqContainer/abc.xml').async('string');
  assert.match(seq, /DbPrjVer="14"/);
});

test('explicit targetProjectVersion overrides the map', async () => {
  await makeV17Drt(tmp('in2.drt'));
  const r = await drtTool.handler({ action: 'downgrade', args: { drtPath: tmp('in2.drt'), outputPath: tmp('out2.drt'), targetProjectVersion: 11 } });
  assert.equal(r.targetProjectVersion, 11);
  const z = await JSZip.loadAsync(await fs.readFile(tmp('out2.drt')));
  assert.match(await z.file('project.xml').async('string'), /<ProjectVersion>11<\/ProjectVersion>/);
});

test('unknown app version is rejected (no silent guess)', async () => {
  await makeV17Drt(tmp('in3.drt'));
  await assert.rejects(
    () => drtTool.handler({ action: 'downgrade', args: { drtPath: tmp('in3.drt'), outputPath: tmp('x'), targetAppVersion: '99.9' } }),
    /unknown targetAppVersion/,
  );
});

test('missing target is rejected', async () => {
  await makeV17Drt(tmp('in4.drt'));
  await assert.rejects(() => drtTool.handler({ action: 'downgrade', args: { drtPath: tmp('in4.drt'), outputPath: tmp('x') } }), /provide targetProjectVersion/);
});

// ── DbAppVer comes from the verified registry (vendor/drt-format/resolve-versions.js) ──
// Before: "19.1.3" was padded to "19.1.3.0" — a build no Resolve ever wrote — while the vendored
// retargetDRT stamps the registry's "19.1.3.0007", so the two re-stamps disagreed in every member.

async function stampsOf(p) {
  const z = await JSZip.loadAsync(await fs.readFile(p));
  const out = [];
  for (const name of Object.keys(z.files)) {
    if (!name.endsWith('.xml')) continue;
    const m = (await z.file(name).async('string')).match(/DbAppVer="([^"]*)" DbPrjVer="([^"]*)"/);
    if (m) out.push({ name, dbAppVer: m[1], dbPrjVer: m[2] });
  }
  return out;
}

test('known major takes DbAppVer + DbPrjVer from the registry row, not padding', async () => {
  await makeV17Drt(tmp('reg19.drt'));
  const r = await drtTool.handler({ action: 'downgrade', args: { drtPath: tmp('reg19.drt'), outputPath: tmp('reg19-out.drt'), targetAppVersion: '19.1.3' } });
  assert.equal(r.appVersion, '19.1.3.0007');
  assert.equal(r.appVersionSource, 'registry');
  assert.equal(r.projectVersionSource, 'registry');
  assert.deepEqual(r.registryRow, { marketing: '19.1.3', dbAppVer: '19.1.3.0007', dbPrjVer: '14' });
  assert.equal(r.warnings, undefined);
  const stamps = await stampsOf(tmp('reg19-out.drt'));
  assert.equal(stamps.length, 2);
  for (const s of stamps) assert.deepEqual([s.dbAppVer, s.dbPrjVer], ['19.1.3.0007', '14'], s.name);
});

test('Resolve 20 (absent from the ProjectVersion map) resolves through the registry: schema 15', async () => {
  await makeV17Drt(tmp('reg20.drt'));
  const r = await drtTool.handler({ action: 'downgrade', args: { drtPath: tmp('reg20.drt'), outputPath: tmp('reg20-out.drt'), targetAppVersion: '20' } });
  assert.equal(r.targetProjectVersion, 15);
  assert.equal(r.appVersion, '20.3.2.0009');
  const z = await JSZip.loadAsync(await fs.readFile(tmp('reg20-out.drt')));
  assert.match(await z.file('project.xml').async('string'), /<ProjectVersion>15<\/ProjectVersion>/);
});

test('appVersionString still overrides the registry', async () => {
  await makeV17Drt(tmp('ovr.drt'));
  const r = await drtTool.handler({ action: 'downgrade', args: { drtPath: tmp('ovr.drt'), outputPath: tmp('ovr-out.drt'), targetAppVersion: '19.1.3', appVersionString: '19.0.2.0003' } });
  assert.equal(r.appVersion, '19.0.2.0003');
  assert.equal(r.appVersionSource, 'appVersionString');
  for (const s of await stampsOf(tmp('ovr-out.drt'))) assert.equal(s.dbAppVer, '19.0.2.0003');
});

test('NULL CONTROL: a major with no registry row gets a LABELLED padded stamp, never a registry build', async () => {
  // 18 has a confirmed gate point (ProjectVersion 11) but no full registry stamp.
  await makeV17Drt(tmp('unk18.drt'));
  const r = await drtTool.handler({ action: 'downgrade', args: { drtPath: tmp('unk18.drt'), outputPath: tmp('unk18-out.drt'), targetAppVersion: '18.0' } });
  assert.equal(r.targetProjectVersion, 11);
  assert.equal(r.appVersion, '18.0.0.0');
  assert.equal(r.appVersionSource, 'padded-fallback');
  assert.equal(r.projectVersionSource, 'version-map');
  assert.equal(r.registryRow, null);
  assert.equal(r.warnings.length, 1);
  assert.match(r.warnings[0], /padded, not a verified build/);
  const registryBuilds = new Set(['19.1.3.0007', '20.3.2.0009', '21.0.0.0048']);
  for (const s of await stampsOf(tmp('unk18-out.drt'))) {
    assert.ok(!registryBuilds.has(s.dbAppVer), `${s.name} borrowed registry build ${s.dbAppVer}`);
    assert.equal(s.dbPrjVer, '11');
  }
});

test('NULL CONTROL: an explicit gate that disagrees with the row does not borrow the row\'s build', async () => {
  await makeV17Drt(tmp('mismatch.drt'));
  const r = await drtTool.handler({ action: 'downgrade', args: { drtPath: tmp('mismatch.drt'), outputPath: tmp('mismatch-out.drt'), targetAppVersion: '19.1.3', targetProjectVersion: 11 } });
  assert.equal(r.targetProjectVersion, 11);
  assert.equal(r.appVersionSource, 'padded-fallback');
  assert.notEqual(r.appVersion, '19.1.3.0007');
  assert.match(r.warnings[0], /schema 14, not the 11/);
});

test('only targetProjectVersion → placeholder stamp, labelled', async () => {
  await makeV17Drt(tmp('ph.drt'));
  const r = await drtTool.handler({ action: 'downgrade', args: { drtPath: tmp('ph.drt'), outputPath: tmp('ph-out.drt'), targetProjectVersion: 14 } });
  assert.equal(r.appVersion, '14.0.0.0000');
  assert.equal(r.appVersionSource, 'placeholder');
  assert.equal(r.warnings.length, 1);
});
