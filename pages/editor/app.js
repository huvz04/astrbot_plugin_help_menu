const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let bridge, state, catalog = [], selectedId, savedJSON, page = 1, pageCount = 1;
let previewSequence = 0, previewTimer, toastTimer, busy = false, demo = false;
const clone = value => structuredClone(value);
const dirty = () => state && JSON.stringify(state) !== savedJSON;

function notify(message, error = false) {
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error', error);
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').hidden = true, error ? 6500 : 3000);
}

function updateSaveStatus() {
  $('#save-status').textContent = busy ? '正在保存…' : dirty() ? '有未保存的修改' : `已保存 · v${state?.revision ?? 0}`;
  $('#save').disabled = busy || !state || !dirty();
}

function changed(resetPage = true) {
  if (resetPage) page = 1;
  updateSaveStatus();
  clearTimeout(previewTimer);
  previewSequence += 1;
  previewTimer = setTimeout(updatePreview, 260);
}

function pluginOverride(id) {
  return state.plugins[id] ||= {name:'', description:'', category:'', order:1000, hidden:false, commands:{}, manual_commands:[]};
}

function commandOverride(plugin, command) {
  if (command.manual) return plugin.manual_commands.find(item => item.id === command.id);
  return plugin.commands[command.id] ||= {usage:'', description:'', order:1000, hidden:false};
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function field(labelText, value, placeholder, callback, options = {}) {
  const label = element('label', 'field', labelText);
  const input = element(options.textarea ? 'textarea' : 'input');
  if (!options.textarea) input.type = options.number ? 'number' : 'text';
  else input.rows = options.rows || 2;
  if (options.number) { input.min = '-10000'; input.max = '10000'; }
  input.value = value ?? '';
  input.placeholder = placeholder || '';
  input.maxLength = options.limit || 1200;
  input.addEventListener('input', () => {
    if (options.number && (!input.value || !input.checkValidity())) return;
    callback(options.number ? Number(input.value) : input.value);
    changed();
  });
  label.append(input);
  return label;
}

function checkbox(labelText, value, callback) {
  const label = element('label', 'check');
  const input = element('input');
  input.type = 'checkbox'; input.checked = Boolean(value);
  input.addEventListener('change', () => { callback(input.checked); changed(); });
  label.append(input, document.createTextNode(labelText));
  return label;
}

function renderList() {
  const search = $('#plugin-search').value.trim().toLocaleLowerCase();
  const list = $('#plugin-list'); list.replaceChildren();
  $('#plugin-count').textContent = String(catalog.length);
  const sorted = [...catalog].sort((a, b) => (state.plugins[a.id]?.order ?? 1000) - (state.plugins[b.id]?.order ?? 1000));
  for (const plugin of sorted) {
    const override = state.plugins[plugin.id] || {};
    const text = [plugin.id, override.name || plugin.name, override.description || plugin.description, ...plugin.commands.map(command => command.usage)].join(' ').toLocaleLowerCase();
    if (search && !text.includes(search)) continue;
    const button = element('button', 'plugin-item' + (plugin.id === selectedId ? ' active' : ''));
    const title = element('span', 'plugin-item-title', override.name || plugin.name);
    if (override.hidden) title.append(element('span', 'hidden-label', '已隐藏'));
    button.append(title, element('span', 'plugin-item-meta', `${plugin.commands.length} 条自动指令${plugin.system ? ' · 系统' : ''}${override.manual_commands?.length ? ` · ${override.manual_commands.length} 条手动指令` : ''}`));
    button.addEventListener('click', () => { selectedId = plugin.id; renderList(); renderDetail(); });
    list.append(button);
  }
  if (!list.children.length) list.append(element('p', 'muted-text', '没有匹配的插件。'));
}

function renderDetail() {
  const detail = $('#detail'); detail.replaceChildren();
  const plugin = catalog.find(item => item.id === selectedId);
  if (!plugin) { detail.append(element('div', 'placeholder', '选择一个插件，编辑它在菜单中的介绍。')); return; }
  const override = state.plugins[plugin.id] || {commands:{}, manual_commands:[]};
  const getOverride = () => pluginOverride(plugin.id);
  const heading = element('div', 'detail-title');
  const title = element('div'); title.append(element('h2', '', override.name || plugin.name), element('p', '', `${plugin.id}${plugin.version ? ' · v' + plugin.version : ''}`));
  heading.append(title, element('span', 'detail-badge', '自动发现')); detail.append(heading);
  const fields = element('div', 'plugin-fields');
  fields.append(
    field('显示名称', override.name, plugin.name, value => {getOverride().name = value; renderList();}, {limit:100}),
    field('插件介绍', override.description, plugin.description, value => getOverride().description = value, {textarea:true, rows:3}),
    field('分类标签', override.category, plugin.system ? '系统功能' : '插件功能', value => getOverride().category = value, {limit:50}),
  );
  const options = element('div', 'detail-options');
  options.append(checkbox('在菜单中隐藏此插件', override.hidden, value => {getOverride().hidden = value; renderList();}), field('显示顺序', override.order ?? 1000, '', value => {getOverride().order = value; renderList();}, {number:true}));
  fields.append(options); detail.append(fields);
  const commandHeading = element('div', 'commands-heading');
  commandHeading.append(element('h3', '', '指令说明'));
  const add = element('button', '', '+ 手动指令');
  add.addEventListener('click', () => {
    getOverride().manual_commands.push({id:crypto.randomUUID(), usage:'新指令', description:'', admin_only:false, hidden:false, order:1000});
    changed(); renderList(); renderDetail();
    const inputs = $$('.command-editor input[type=text]', detail); inputs.at(-1)?.focus();
  });
  commandHeading.append(add); detail.append(commandHeading);
  const manual = (override.manual_commands || []).map(item => ({...item, manual:true, name:item.usage, aliases:[]}));
  for (const command of [...plugin.commands, ...manual]) {
    const change = command.manual ? command : override.commands?.[command.id] || {};
    const getChange = () => commandOverride(getOverride(), command);
    const card = element('article', 'command-editor');
    const top = element('div', 'command-top');
    top.append(element('code', '', command.usage));
    top.append(element('span', 'detail-badge', command.manual ? '手动' : command.admin_only ? '管理指令' : command.is_group ? '指令组' : '自动'));
    card.append(top,
      field('展示用法', change.usage, command.usage, value => getChange().usage = value, {limit:300}),
      field('指令介绍', change.description, command.description || '写一段易懂的使用说明', value => getChange().description = value, {textarea:true}),
    );
    const bottom = element('div', 'command-bottom');
    bottom.append(checkbox('隐藏', change.hidden, value => getChange().hidden = value));
    if (command.manual) bottom.append(checkbox('仅管理员', change.admin_only, value => getChange().admin_only = value));
    const order = field('顺序', change.order ?? 1000, '', value => getChange().order = value, {number:true});
    order.className = 'order-field'; bottom.append(order);
    if (command.manual) {
      const remove = element('button', 'remove-command', '删除');
      remove.addEventListener('click', () => {
        getOverride().manual_commands = getOverride().manual_commands.filter(item => item.id !== command.id);
        changed(); renderList(); renderDetail();
      });
      bottom.append(remove);
    }
    card.append(bottom);
    if (command.aliases?.length) card.append(element('p', 'command-note', `别名：${command.aliases.join('、')}`));
    if (command.manual) card.append(element('p', 'command-note', '手动条目仅展示用法，请填写已有功能的真实触发方式。'));
    detail.append(card);
  }
  if (!plugin.commands.length && !manual.length) detail.append(element('p', 'muted-text', '此插件没有注册标准指令，可能通过消息事件或定时任务运行。可以手动补充使用说明。'));
  detail.append(element('p', 'sidebar-hint', '留空会恢复插件的原始名称、用法或描述。数字越小，排列越靠前。'));
}

function syncAppearance() {
  $$('[data-setting]').forEach(input => {
    const value = state.settings[input.dataset.setting];
    if (input.type === 'checkbox') input.checked = value;
    else input.value = value;
  });
  $$('[data-theme]').forEach(button => button.classList.toggle('selected', button.dataset.theme === state.settings.theme));
}

function scalePreview() {
  const iframe = $('#preview'), viewport = $('#preview-viewport');
  if (!state) return;
  const width = state.settings.width;
  const height = iframe.contentDocument?.documentElement.scrollHeight || 800;
  const scale = Math.min(1, viewport.clientWidth / width);
  iframe.style.width = `${width}px`; iframe.style.height = `${height}px`;
  iframe.style.transform = `scale(${scale})`;
  $('#preview-sizing').style.height = `${Math.ceil(height * scale)}px`;
}

async function updatePreview() {
  if (!state) return;
  const sequence = ++previewSequence;
  try {
    const result = await bridge.apiPost('preview', {state:clone(state), page, query:$('#preview-search').value, as_admin:$('#preview-admin').checked});
    if (sequence !== previewSequence) return;
    page = result.page; pageCount = result.pages;
    $('#page-label').textContent = `${page} / ${pageCount}`;
    $('#preview-stats').textContent = `${result.plugin_count} 个插件 · ${result.command_count} 条指令`;
    $('#prev-page').disabled = page <= 1;
    $('#next-page').disabled = page >= pageCount;
    $('#preview').srcdoc = result.html;
    $('#screenshot').disabled = demo;
  } catch (error) {
    if (sequence === previewSequence) notify(`预览失败：${error.message}`, true);
  }
}

function confirmAction(title, message) {
  return new Promise(resolve => {
    const dialog = $('#confirm-dialog');
    $('#confirm-title').textContent = title; $('#confirm-message').textContent = message;
    const finish = value => { dialog.close(); resolve(value); };
    $('#confirm-ok').onclick = () => finish(true);
    $('#confirm-cancel').onclick = () => finish(false);
    dialog.oncancel = event => { event.preventDefault(); finish(false); };
    dialog.showModal();
  });
}

async function load() {
  const result = await bridge.apiGet('state');
  state = result.state; catalog = result.catalog; savedJSON = JSON.stringify(state);
  if (!catalog.some(plugin => plugin.id === selectedId)) selectedId = catalog[0]?.id;
  page = 1;
  renderList(); renderDetail(); syncAppearance(); updateSaveStatus(); await updatePreview();
  $('#refresh').disabled = false;
}

$$('[data-tab]').forEach(button => button.addEventListener('click', () => {
  const appearance = button.dataset.tab === 'appearance';
  $('#plugins-panel').hidden = appearance; $('#appearance-panel').hidden = !appearance;
  $$('[data-tab]').forEach(tab => {tab.classList.toggle('active', tab === button); tab.setAttribute('aria-selected', String(tab === button));});
}));
$$('[data-setting]').forEach(input => input.addEventListener('input', () => {
  if (input.type === 'number' && (!input.value || !input.checkValidity())) return;
  state.settings[input.dataset.setting] = input.type === 'checkbox' ? input.checked : input.type === 'number' || input.tagName === 'SELECT' ? Number(input.value) : input.value;
  changed();
}));
$$('[data-theme]').forEach(button => button.addEventListener('click', () => {
  state.settings.theme = button.dataset.theme;
  state.settings.accent = {sakura:'#af5676', paper:'#687b55', dark:'#c49ae8'}[button.dataset.theme];
  syncAppearance(); changed();
}));
$('#plugin-search').addEventListener('input', () => state && renderList());
$('#preview-search').addEventListener('input', () => changed());
$('#preview-admin').addEventListener('change', () => changed());
$('#prev-page').addEventListener('click', () => {page--; changed(false);});
$('#next-page').addEventListener('click', () => {page++; changed(false);});
$('#preview').addEventListener('load', scalePreview);
new ResizeObserver(scalePreview).observe($('#preview-viewport'));
$('#refresh').addEventListener('click', async () => {
  if (dirty() && !await confirmAction('重新读取菜单？', '未保存的修改会被丢弃。已保存的描述会保留，并重新扫描当前插件。')) return;
  try {await load(); notify('已重新扫描插件并读取配置');} catch (error) {notify(error.message, true);}
});
$('#save').addEventListener('click', async () => {
  busy = true; updateSaveStatus();
  const submitted = clone(state);
  try {
    const result = await bridge.apiPost('settings', submitted);
    const modifiedDuringSave = JSON.stringify(state) !== JSON.stringify(submitted);
    state.revision = result.state.revision;
    savedJSON = JSON.stringify(result.state);
    if (!modifiedDuringSave) {state = result.state; renderList(); renderDetail(); syncAppearance();}
    notify(demo ? '已保存本地演示配置' : '菜单已保存，聊天调用立即生效');
  } catch (error) {notify(error.message, true);}
  finally {busy = false; updateSaveStatus();}
});
$('#export').addEventListener('click', () => {
  if (!state) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(state, null, 2)], {type:'application/json'}));
  const link = element('a'); link.href = url; link.download = 'help-menu-config.json'; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$('#import').addEventListener('click', () => $('#import-file').click());
$('#import-file').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 2 * 1024 * 1024) throw new Error('配置文件不能超过 2MB');
    const imported = JSON.parse(await file.text());
    imported.revision = state.revision;
    // The preview endpoint applies the same validation as saving.
    const validated = await bridge.apiPost('validate', imported);
    if (!await confirmAction('导入菜单配置？', '当前编辑内容会被替换。导入后可先查看预览，再点击保存。')) return;
    state = validated.state; renderList(); renderDetail(); syncAppearance(); changed(); notify('配置已导入，请检查预览后保存');
  } catch (error) {notify(`导入失败：${error.message}`, true);}
  finally {event.target.value = '';}
});
$('#screenshot').addEventListener('click', async () => {
  const button = $('#screenshot'); button.disabled = true; button.textContent = '生成中…';
  try {
    const result = await bridge.apiPost('screenshot', {state:clone(state), page, query:$('#preview-search').value, as_admin:$('#preview-admin').checked});
    if (!/^data:image\/(png|jpeg);base64,/.test(result.image)) throw new Error('截图响应无效');
    $('#rendered-image').src = result.image; $('#download-image').href = result.image;
    $('#download-image').download = `help-menu-${result.page}.png`;
    $('#image-output').hidden = false; notify('图片已生成');
  } catch (error) {notify(error.message, true);}
  finally {button.disabled = false; button.textContent = '生成图片';}
});
$('#close-image').addEventListener('click', () => $('#image-output').hidden = true);
window.addEventListener('beforeunload', event => {if (dirty()) {event.preventDefault(); event.returnValue = '';}});

try {
  if (window.AstrBotPluginPage) {
    bridge = window.AstrBotPluginPage; await bridge.ready();
  } else if (location.pathname.startsWith('/demo/')) {
    demo = true;
    const call = async (endpoint, method, body) => {
      const response = await fetch(`/demo/api/${endpoint}`, {method, headers:{'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || '请求失败');
      return result;
    };
    bridge = {apiGet:endpoint => call(endpoint,'GET'), apiPost:(endpoint,body) => call(endpoint,'POST',body)};
    $('#banner').hidden = false; $('#banner').textContent = '本地演示 · 当前使用示例插件数据。实际插件列表和图片生成请在 AstrBot 的菜单编辑器中使用。';
  } else {
    throw new Error('请从 AstrBot → 插件 → 帮助菜单 → 菜单编辑器 打开此页面；本地预览可运行 python tools/demo.py。');
  }
  await load();
} catch (error) {
  $('#save-status').textContent = '未连接';
  $('#detail').replaceChildren(element('div','placeholder',error.message));
  notify(error.message, true);
}
