// Nightly smoke test of the live app (docs/index.html) against real DraftKings and Sleeper data.
// Usage: node research/smoke_test.js <dir containing node_modules/highs>   (npm install highs@1.15.4 there first)
// DraftKings data is fetched with curl (DraftKings rejects headless browsers); everything else is live in the page.
// Prints one line per check and exits 1 if any check fails. Sports with no open slates are skipped, not failed.
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path'), { execFileSync } = require('child_process');
const ROOT = path.join(__dirname, '..', 'docs');
const HIGHS = path.join(process.argv[2] || '.', 'node_modules', 'highs', 'build');
let failed = 0;
const check = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
(async () => {
  const browser = await chromium.launch(process.env.HTTPS_PROXY ? { proxy: { server: process.env.HTTPS_PROXY } } : {});
  const ctx = await browser.newContext({ viewport: { width: 1300, height: 1400 } });
  await ctx.route('https://cdn.jsdelivr.net/npm/highs@1.15.4/build/*', r => {
    const f = path.join(HIGHS, path.basename(new URL(r.request().url()).pathname));
    r.fulfill({ body: fs.readFileSync(f), contentType: f.endsWith('.wasm') ? 'application/wasm' : 'text/javascript' });
  });
  await ctx.route(/fonts\.(googleapis|gstatic)\.com/, r => r.abort());
  // DraftKings rejects headless browsers, so its data is fetched with curl and handed to the page unchanged.
  await ctx.route('https://api.draftkings.com/**', r => {
    try {
      const body = execFileSync('curl', ['-sS', '-m', '60', '--fail', r.request().url()], { maxBuffer: 64 * 1024 * 1024 });
      r.fulfill({ body, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' } });
    } catch (e) { r.fulfill({ status: 502, body: '{}', headers: { 'access-control-allow-origin': '*' } }); }
  });
  await ctx.route('http://local.test/**', r => {
    const name = new URL(r.request().url()).pathname.replace(/^\//, '') || 'index.html';
    const f = path.join(ROOT, name);
    fs.existsSync(f) ? r.fulfill({ body: fs.readFileSync(f), contentType: name.endsWith('.json') ? 'application/json' : 'text/html' }) : r.fulfill({ status: 404, body: '' });
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  for (const sport of ['nfl', 'nba']) {
    await page.goto('http://local.test/');
    await page.evaluate(s => { localStorage.clear(); localStorage.setItem('caproom.sport', JSON.stringify(s)); }, sport);
    await page.reload();
    await page.waitForFunction(() => /projected players|Waiting|not loaded/.test(document.querySelector('#status').textContent), null, { timeout: 120000 });
    const status = await page.textContent('#status');
    const banner = await page.$eval('#banner', b => b.hidden ? '' : b.textContent);
    if (/has no .* slates open/.test(banner)) { console.log(`SKIP ${sport.toUpperCase()}: no Classic slates open on DraftKings right now`); continue; }
    const n = +(status.match(/(\d+) projected players/) || [])[1] || 0;
    check(n >= 40, `${sport.toUpperCase()}: live slate loaded with ${n} projected players (${banner ? 'banner: ' + banner.slice(0, 80) : 'DraftKings live'})`);
    if (n < 40) continue;
    const info = await page.evaluate(() => ({ lines: state.lines ? state.lines.size : -1, zeroStars: state.players.filter(p => p.salary >= 7000 && !p.proj && !['OUT', 'IR', 'D'].includes(p.status)).map(p => p.name) }));
    check(info.lines >= 0, `${sport.toUpperCase()}: Sleeper lines feed read (${info.lines} player-games with lines)`);
    check(info.zeroStars.length <= 2, `${sport.toUpperCase()}: expensive active players all have projections${info.zeroStars.length ? ' (missing: ' + info.zeroStars.join(', ') + ')' : ''}`);
    await page.click('[data-style=cash]').catch(() => {});
    await page.click('[data-build=projected]');
    await page.waitForFunction(() => !document.querySelector('#buildall').disabled && !/Building/.test(document.querySelector('#results').textContent), null, { timeout: 180000 });
    const lus = await page.$$eval('#results .lineup', a => a.map(x => +x.querySelector('.figs span:nth-child(2)').textContent.replace(/\D/g, '')));
    check(lus.length === 5 && lus.every(s => s <= 50000 && s > 40000), `${sport.toUpperCase()}: Top 5 Projected built ${lus.length} lineups under the cap`);
    await page.click('[data-style=gpp]');
    await page.selectOption('#gppcount', '1');
    await page.click('#rungpp');
    await page.waitForFunction(() => !document.querySelector('#rungpp').disabled, null, { timeout: 300000 });
    check(await page.$$eval('#results .lineup .simstats', a => a.length) === 1, `${sport.toUpperCase()}: Tournament simulation built a lineup`);
  }
  check(errors.length === 0, `no page errors${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''}`);
  await browser.close();
  process.exit(failed ? 1 : 0);
})().catch(e => { console.log('FAIL smoke test crashed: ' + e.message); process.exit(1); });
