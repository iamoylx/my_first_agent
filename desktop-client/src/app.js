/**
 * Agent Desktop — 主窗口逻辑
 *
 * 聊天消息收发通过 Tauri IPC（invoke）→ Rust 代理 → Python HTTP API
 * 避免 WebView2 混合内容拦截（tauri:// 安全源 → http:// 不安全目标）
 */

// [诊断] 标记前端已加载（确认页面真实渲染，区分"没加载"与"渲染异常"）
try {
  const __t = window.__TAURI__;
  if (__t && __t.core && __t.core.invoke) {
    const fire = () => __t.core.invoke('mark_boot');
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fire);
    } else {
      fire();
    }
  }
} catch (e) {}

// ============ 配置常量 ============
// 静态资源（立绘/精灵图）已内嵌 src/assets/，通过 tauri://localhost 同源加载，
// 无需走 HTTP 代理（避免混合内容拦截）。

// ============ DOM 引用 ============
const messagesContainer = document.getElementById('messages');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('btn-send');
const btnPetMode = document.getElementById('btn-pet-mode');
const btnResetChat = document.getElementById('btn-reset-chat');
const serverStatusDot = document.getElementById('server-status');
const charStatus = document.getElementById('char-status');
const loadingOverlay = document.getElementById('loading-overlay');

// ============ 全局错误显示（避免黑屏盲调）============
function showFatal(msg) {
    const el = document.getElementById('fatal-error');
    if (el) {
        el.textContent = msg;
        el.style.display = 'block';
    }
    console.error('[FATAL]', msg);
}
window.addEventListener('error', (e) => showFatal('JS Error: ' + (e.message || e.error)));
window.addEventListener('unhandledrejection', (e) => showFatal('Promise Error: ' + (e.reason?.message || e.reason)));

// 安全获取 invoke（window.__TAURI__ 可能未注入）
function getInvoke() {
    try {
        if (window.__TAURI__ && window.__TAURI__.core) {
            return window.__TAURI__.core.invoke;
        }
    } catch (_) {}
    return null;
}

// ============ 状态 ============
let isReplying = false;

// ============ 初始化 ============
document.addEventListener('DOMContentLoaded', async () => {
    bindEvents();
    await waitForServer();
    await loadHistory();
});

// ============ 事件绑定 ============
function bindEvents() {
    sendBtn.addEventListener('click', () => sendMessage());

    userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    userInput.addEventListener('input', () => {
        userInput.style.height = 'auto';
        userInput.style.height = Math.min(userInput.scrollHeight, 120) + 'px';
    });

    btnPetMode.addEventListener('click', switchToPetMode);
    btnResetChat.addEventListener('click', resetChat);
}

// ============ 服务连接（Tauri IPC） ============
async function waitForServer(retries = 20) {
    const invoke = getInvoke();
    if (!invoke) {
        showFatal('Tauri API 未注入（withGlobalTauri 未启用？）');
        return;
    }
    for (let i = 0; i < retries; i++) {
        try {
            const data = await invoke('check_server_health');
            if (data.status === 'ok') {
                serverStatusDot.className = 'dot dot-green';
                loadingOverlay.classList.add('hidden');
                console.log('[Agent] Server ready');
                return;
            }
        } catch { /* 还没起来 */ }
        serverStatusDot.className = 'dot dot-gray';
        await sleep(500);
    }
    serverStatusDot.className = 'dot dot-red';
    loadingOverlay.querySelector('p').textContent =
        '无法连接到 Agent 服务，请检查 Python 环境';
}

// ============ 聊天核心（Tauri IPC） ============

/**
 * 发送用户消息并接收 AI 回复
 */
async function sendMessage() {
    const text = userInput.value.trim();
    if (!text || isReplying) return;

    userInput.value = '';
    userInput.style.height = 'auto';

    appendMessage(text, 'user');
    setReplyingState(true);
    notifyPetState('press');

    try {
        const invoke = getInvoke();
        if (!invoke) { appendMessage('[错误] Tauri API 未注入', 'system'); return; }
        const data = await invoke('send_chat', { message: text });

        if (data.error) {
            appendMessage(`[错误] ${data.error}`, 'system');
            notifyPetState('idle');
        } else if (data.reply) {
            if (Array.isArray(data.thinking) && data.thinking.length > 0) {
                appendThinkingBlock(data.thinking);
            }
            await typeMessage(data.reply, 'ai');
            notifyPetState('response_done');
        }
    } catch (err) {
        console.error('[Agent] Chat error:', err);
        appendMessage(`[连接失败] ${err}`, 'system');
        notifyPetState('idle');
    } finally {
        setReplyingState(false);
    }
}

/**
 * 追加一条消息到聊天区
 */
function appendMessage(content, role) {
    const div = document.createElement('div');
    div.className = `message message-${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';

    if (role === 'ai') {
        const avatar = document.createElement('img');
        avatar.className = 'message-avatar';
        avatar.src = 'assets/avatar.png';
        avatar.alt = '小满';
        div.appendChild(avatar);
        bubble.innerHTML = content.replace(/\n/g, '<br>');
    } else {
        bubble.textContent = content;
    }

    div.appendChild(bubble);
    messagesContainer.appendChild(div);
    scrollToBottom();
}

/**
 * 打字机效果 — 逐字显示 AI 回复
 */
async function typeMessage(text, role) {
    const div = document.createElement('div');
    div.className = `message message-${role}`;

    const avatar = document.createElement('img');
    avatar.className = 'message-avatar';
    avatar.src = 'assets/avatar.png';
    avatar.alt = '小满';
    div.appendChild(avatar);

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble typing-cursor';
    div.appendChild(bubble);
    messagesContainer.appendChild(div);

    let displayed = '';
    const chunkSize = 2;

    for (let i = 0; i < text.length; i += chunkSize) {
        displayed += text.slice(i, i + chunkSize);
        bubble.innerHTML = displayed.replace(/\n/g, '<br>') + '<span class="cursor">▌</span>';
        scrollToBottom();
        await sleep(25);
    }

    bubble.innerHTML = displayed.replace(/\n/g, '<br>');
    bubble.classList.remove('typing-cursor');
}

/**
 * 渲染「💭 思考过程」可折叠块（灰色小字，点击展开/收起）
 * trace: [{kind, text}, ...] — 来自后端 /chat 的 thinking 字段
 */
function appendThinkingBlock(trace) {
    const div = document.createElement('div');
    div.className = 'thinking-block';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'thinking-toggle';
    toggle.title = '点击展开/收起思考过程';
    const arrow = document.createElement('span');
    arrow.className = 'thinking-arrow';
    arrow.textContent = '▸';
    toggle.appendChild(document.createTextNode('💭 思考过程 '));
    toggle.appendChild(arrow);

    const body = document.createElement('div');
    body.className = 'thinking-body';
    body.hidden = true;

    (trace || []).forEach(ev => {
        const line = document.createElement('div');
        line.className = 'thinking-line';
        const kind = document.createElement('span');
        kind.className = 'thinking-kind';
        kind.textContent = '[' + (ev.kind || 'step') + ']';
        const txt = document.createElement('span');
        txt.textContent = ev.text || '';
        line.appendChild(kind);
        line.appendChild(txt);
        body.appendChild(line);
    });

    toggle.addEventListener('click', () => {
        const willOpen = body.hidden;
        body.hidden = !willOpen;
        arrow.textContent = willOpen ? '▾' : '▸';
        toggle.classList.toggle('open', willOpen);
        scrollToBottom();
    });

    div.appendChild(toggle);
    div.appendChild(body);
    messagesContainer.appendChild(div);
    scrollToBottom();
}

/**
 * 设置回复中 UI 状态
 */
function setReplyingState(replying) {
    isReplying = replying;
    sendBtn.disabled = replying;
    charStatus.textContent = replying ? '思考中...' : '在线';
    charStatus.style.background = replying ? '#c9a456' : 'var(--success)';
    // 头部状态同步（替换标题“对话”的位置）
    const headerStatus = document.getElementById('header-status');
    if (headerStatus) {
        headerStatus.textContent = replying ? '思考中...' : '在线';
        headerStatus.style.background = replying ? '#c9a456' : 'var(--success)';
    }
}

// ============ 历史记录（Tauri IPC） ============

async function loadHistory() {
    try {
        const invoke = getInvoke();
        if (!invoke) return;
        const data = await invoke('get_history');
        if (data.messages && data.messages.length > 0) {
            messagesContainer.innerHTML = '';
            data.messages.forEach(msg => {
                const role = msg.role === 'user' ? 'user' : 'ai';
                appendMessage(msg.content, role);
            });
        }
    } catch (err) {
        console.warn('[Agent] Failed to load history:', err);
    }
}

async function resetChat() {
    try {
        const invoke = getInvoke();
        if (!invoke) return;
        await invoke('reset_chat_session');
        messagesContainer.innerHTML = '';
        appendMessage('已开始新对话～（旧对话已归档保留，档案卡不变）', 'system');
    } catch (err) {
        console.error('[Agent] Reset failed:', err);
    }
}

// ============ 窗口管理 ============

async function switchToPetMode() {
    if (window.__TAURI__) {
        const { invoke } = window.__TAURI__.core;
        await invoke('switch_to_pet_mode');
    }
}

async function notifyPetState(state) {
    // 优先走 Tauri 命令：Rust 维护桌宠状态机并广播事件给桌宠窗口（snake_case 与 PetState 对齐）
    const invoke = getInvoke();
    if (invoke) {
        try {
            await invoke('set_pet_state', { state });
            return;
        } catch (err) {
            console.warn('[Agent] set_pet_state 失败，降级 localStorage:', err);
        }
    }
    // 兜底：localStorage + storage 事件（桌面后端不可用时）
    localStorage.setItem('pet-state', JSON.stringify({
        state,
        timestamp: Date.now(),
    }));
    window.dispatchEvent(new StorageEvent('storage', {
        key: 'pet-state',
        newValue: JSON.stringify({ state, timestamp: Date.now() }),
    }));
}

// ============ 工具函数 ============

function scrollToBottom() {
    requestAnimationFrame(() => {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    });
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}


// ============ 档案卡管理（记忆 UI） ============
let memoryData = null;

document.addEventListener('DOMContentLoaded', () => {
    const btnMemory = document.getElementById('btn-memory');
    const btnBack = document.getElementById('btn-memory-back');
    const btnAdd = document.getElementById('btn-memory-add');
    const btnAddOk = document.getElementById('btn-add-confirm');
    const btnAddCancel = document.getElementById('btn-add-cancel');

    // 双保险：启动时确保记忆页隐藏（聊天页优先）
    document.getElementById('memory-view').hidden = true;

    btnMemory?.addEventListener('click', openMemoryView);
    btnBack?.addEventListener('click', closeMemoryView);
    btnAdd?.addEventListener('click', () => {
        const form = document.getElementById('memory-add-form');
        form.hidden = !form.hidden;
        if (!form.hidden) document.getElementById('add-key').focus();
    });
    btnAddOk?.addEventListener('click', addMemoryItem);
    btnAddCancel?.addEventListener('click', () => {
        document.getElementById('memory-add-form').hidden = true;
    });
});

function openMemoryView() {
    const view = document.getElementById('memory-view');
    view.hidden = false;
    loadMemoryItems();
}

function closeMemoryView() {
    document.getElementById('memory-view').hidden = true;
}

async function loadMemoryItems() {
    const invoke = getInvoke();
    if (!invoke) { showFatal('Tauri API 未注入'); return; }
    document.getElementById('facts-list').innerHTML = '<div class="memory-empty">加载中…</div>';
    document.getElementById('prefs-list').innerHTML = '<div class="memory-empty">加载中…</div>';
    try {
        memoryData = await invoke('get_profile_items');
        renderMemoryItems();
    } catch (err) {
        console.error('[Memory] 加载档案卡失败:', err);
        document.getElementById('facts-list').innerHTML = '<div class="memory-empty">加载失败，请稍后重试（' + err + '）</div>';
        document.getElementById('prefs-list').innerHTML = '';
    }
}

function renderMemoryItems() {
    const factsList = document.getElementById('facts-list');
    const prefsList = document.getElementById('prefs-list');
    factsList.innerHTML = '';
    prefsList.innerHTML = '';
    if (!memoryData) return;
    renderMemoryList(factsList, memoryData.facts || []);
    renderMemoryList(prefsList, memoryData.preferences || []);
    if (!(memoryData.facts || []).length) {
        factsList.innerHTML = '<div class="memory-empty">暂无事实，点右上角「＋ 新建」添加</div>';
    }
    if (!(memoryData.preferences || []).length) {
        prefsList.innerHTML = '<div class="memory-empty">暂无偏好，点右上角「＋ 新建」添加</div>';
    }
}

function renderMemoryList(container, items) {
    items.forEach(item => {
        const row = document.createElement('div');
        row.className = 'memory-item' + (item.active === false ? ' inactive' : '');

        const info = document.createElement('div');
        info.className = 'memory-item-info';
        const keyEl = document.createElement('span');
        keyEl.className = 'memory-item-key';
        keyEl.textContent = item.key;
        const valEl = document.createElement('span');
        valEl.className = 'memory-item-value';
        valEl.textContent = item.value;
        const metaEl = document.createElement('span');
        metaEl.className = 'memory-item-meta';
        metaEl.textContent = `confidence ${item.confidence ?? '-'} · ${(item.updated_at || '').slice(0, 16)}`;
        info.append(keyEl, valEl, metaEl);

        const actions = document.createElement('div');
        actions.className = 'memory-item-actions';
        const toggle = document.createElement('button');
        toggle.className = 'toggle-btn ' + (item.active === false ? 'off' : 'on');
        toggle.textContent = item.active === false ? '× 停用' : '√ 生效';
        toggle.addEventListener('click', () => toggleItem(item.key, item.active === false));
        const del = document.createElement('button');
        del.className = 'del-btn';
        del.textContent = '删除';
        del.addEventListener('click', () => deleteItem(item.key));
        actions.append(toggle, del);

        row.append(info, actions);
        container.appendChild(row);
    });
}

async function toggleItem(key, active) {
    const invoke = getInvoke();
    if (!invoke) return;
    try {
        await invoke('profile_toggle', { key, active });
        await loadMemoryItems();
    } catch (err) {
        console.error('[Memory] 切换生效失败:', err);
    }
}

async function deleteItem(key) {
    if (!confirm(`确定删除「${key}」？删除会先存档保留（discarded），可恢复。`)) return;
    const invoke = getInvoke();
    if (!invoke) return;
    try {
        await invoke('profile_delete', { key });
        await loadMemoryItems();
    } catch (err) {
        console.error('[Memory] 删除失败:', err);
    }
}

async function addMemoryItem() {
    const key = document.getElementById('add-key').value.trim();
    const value = document.getElementById('add-value').value.trim();
    const type = document.getElementById('add-type').value;
    if (!key || !value) { alert('key 和内容都不能为空'); return; }
    const invoke = getInvoke();
    if (!invoke) return;
    try {
        await invoke('profile_add', { key, value, factType: type, confidence: 0.9 });
        document.getElementById('add-key').value = '';
        document.getElementById('add-value').value = '';
        document.getElementById('memory-add-form').hidden = true;
        await loadMemoryItems();
    } catch (err) {
        console.error('[Memory] 添加失败:', err);
        alert('添加失败：' + err);
    }
}
