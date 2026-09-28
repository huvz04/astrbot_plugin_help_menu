/* Optional browser QA: run the local demo, then use an installed Playwright. */
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const base = process.env.HELP_MENU_DEMO_URL || 'http://127.0.0.1:8767';
  const output = path.resolve(__dirname, '..', 'previews');
  await fs.mkdir(output, {recursive:true});
  const original = await (await fetch(`${base}/demo/api/state`)).json();
  const browser = await chromium.launch({channel:'chrome', headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1040}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const waitPreview = async () => {
    await page.waitForFunction(() => {
      const frame = document.querySelector('#preview');
      return frame.contentDocument?.querySelector('.card') && parseFloat(document.querySelector('#preview-sizing').style.height) > 200;
    });
  };
  try {
    await page.goto(`${base}/demo/`);
    await waitPreview();
    assert.match(await page.locator('#preview-stats').innerText(), /5 个插件/);
    await page.screenshot({path:path.join(output,'editor.png'), fullPage:true});

    await page.getByLabel('显示名称', {exact:true}).fill('生日说明测试');
    await page.waitForFunction(() => document.querySelector('#preview').contentDocument?.body.textContent.includes('生日说明测试'));
    await page.getByLabel('在菜单中隐藏此插件').check();
    await page.waitForFunction(() => document.querySelector('#preview-stats').textContent.startsWith('4 个插件'));
    await page.getByLabel('在菜单中隐藏此插件').uncheck();
    await page.getByRole('button',{name:'+ 手动指令'}).click();
    await page.locator('.command-editor').last().getByLabel('展示用法').fill('/测试说明');
    await page.locator('.command-editor').last().getByLabel('指令介绍').fill('仅作为手动菜单展示。');
    await page.waitForFunction(() => document.querySelector('#preview').contentDocument?.body.textContent.includes('/测试说明'));
    await page.getByRole('button',{name:'菜单外观',exact:true}).click();
    await page.getByRole('button',{name:'夜色',exact:true}).click();
    await page.getByLabel('每页最多条目').fill('4');
    await page.waitForFunction(() => document.querySelector('#page-label').textContent === '1 / 3');
    await page.getByRole('button',{name:'下一页',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('#page-label').textContent === '2 / 3');
    await page.getByLabel('向管理员显示管理指令').check();
    await page.getByLabel('管理员视角').check();
    await page.waitForFunction(() => document.querySelector('#preview-stats').textContent.includes('10 条指令'));
    await page.getByRole('button',{name:'保存菜单',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('#save-status').textContent.startsWith('已保存'));
    await page.reload();
    await waitPreview();
    await page.getByRole('button',{name:'菜单外观',exact:true}).click();
    assert.equal(await page.getByLabel('每页最多条目').inputValue(), '4');
    assert.equal(await page.locator('[data-theme="dark"]').getAttribute('class'), 'selected');
    await page.getByRole('button',{name:'插件与指令',exact:true}).click();
    assert.equal(await page.getByLabel('显示名称',{exact:true}).inputValue(), '生日说明测试');
    await page.locator('.command-editor').last().getByRole('button',{name:'删除',exact:true}).click();
    await page.waitForFunction(() => !document.querySelector('#preview').contentDocument?.body.textContent.includes('/测试说明'));

    const current = await (await fetch(`${base}/demo/api/state`)).json();
    original.state.revision = current.state.revision;
    const reset = await fetch(`${base}/demo/api/settings`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(original.state)});
    assert.equal(reset.status, 200);
    await page.reload(); await waitPreview();
    const previewResponse = await fetch(`${base}/demo/api/preview`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state:original.state,page:1})});
    const preview = await previewResponse.json();
    const menuPage = await browser.newPage({viewport:{width:1080,height:800}});
    await menuPage.setContent(preview.html);
    await menuPage.screenshot({path:path.join(output,'menu.png'),fullPage:true});
    assert.equal(await menuPage.evaluate(() => document.documentElement.scrollWidth),1080);
    for (const theme of ['paper','dark']) {
      const state = structuredClone(original.state);
      state.settings.theme = theme; state.settings.accent = theme === 'dark' ? '#c49ae8' : '#687b55';
      state.settings.columns = 3; state.settings.width = 1200;
      const result = await (await fetch(`${base}/demo/api/preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state,page:1})})).json();
      await menuPage.setViewportSize({width:1200,height:800});
      await menuPage.setContent(result.html);
      await menuPage.screenshot({path:path.join(output,`menu-${theme}.png`),fullPage:true});
      assert.equal(await menuPage.evaluate(() => document.documentElement.scrollWidth),1200);
    }
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,'editor-mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth),390);
    assert.deepEqual(errors,[]);
    console.log('Browser QA passed: editing, hiding, manual entries, themes, pagination, admin preview, persistence, mobile layout.');
    console.log(output);
  } finally {
    const current = await (await fetch(`${base}/demo/api/state`)).json();
    original.state.revision = current.state.revision;
    await fetch(`${base}/demo/api/settings`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(original.state)});
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode=1;});
