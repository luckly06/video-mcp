const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appSource = fs.readFileSync(
  path.resolve(__dirname, '../../../../archive/web/app.js'),
  'utf8',
);

test('去重产物打开按钮传递当前任务产物路径', () => {
  assert.match(
    appSource,
    /openSubdirFolder\("去重", currentDedupArtifact\)/,
  );
  assert.match(
    appSource,
    /JSON\.stringify\(\{ subdir, filename, open_parent: true \}\)/,
  );
});

test('无当前产物时不回退打开根目录，且不污染目录配置流程', () => {
  const currentArtifactWarnings = appSource.match(/当前暂无" \+ subdir/g) || [];
  assert.equal(currentArtifactWarnings.length, 1);

  const configureBlock = appSource.slice(
    appSource.indexOf('async function configureOutputDir(kind)'),
    appSource.indexOf('async function ensureOutputDir(kind)'),
  );
  assert.doesNotMatch(configureBlock, /filename|subdir/);
});