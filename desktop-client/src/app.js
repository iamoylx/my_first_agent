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
// 立绘下方状态卡片已移除（charStatus 为 null），用安全助手更新（在线/离线/思考中在顶部 header 显示）
function setCharStatus(text, bg) {
    if (!charStatus) return;
    if (text !== undefined) charStatus.textContent = text;
    if (bg !== undefined) charStatus.style.background = bg;
}
const loadingOverlay = document.getElementById('loading-overlay');
const btnAttach = document.getElementById('btn-attach');
const fileInput = document.getElementById('file-input');
const attachmentPreview = document.getElementById('attachment-preview');
const attachThumb = document.getElementById('attach-thumb');
const attachName = document.getElementById('attach-name');
const attachRemove = document.getElementById('attach-remove');

// ============ 图片附件（多模态准备）============
let pendingAttachment = null;   // { name, dataUrl, mime }

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
// 对话大脑切换：localStorage 持久化，默认 DeepSeek
let currentProvider = localStorage.getItem('xiaoman_provider') || 'deepseek';
let serverConnected = false;      // Agent 后端是否连上
let localApproved = false;        // 本会话是否已确认启动本地模型（每次登录需重新确认）
let connectivity = null;          // {deepseek_ok, ollama_running, local_model_ready}

// ============ 模型切换（DeepSeek / 本地，按需启动本地模型）============
function providerLabel(p) {
    return p === 'local' ? '本地 Qwen3-VL' : 'DeepSeek';
}

function applyProvider(p) {
    if (p !== 'deepseek' && p !== 'local') return;
    currentProvider = p;
    localStorage.setItem('xiaoman_provider', p);
    document.querySelectorAll('#provider-toggle .provider-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.provider === p);
    });
    updateHeaderStatus();
}

function updateHeaderStatus() {
    const st = document.getElementById('header-status');
    if (!st) return;
    if (!serverConnected) { st.textContent = '连接失败'; return; }
    let label = providerLabel(currentProvider);
    if (currentProvider === 'deepseek' && connectivity && connectivity.deepseek_ok === false) {
        label += ' · 离线';
    } else if (currentProvider === 'local' && connectivity && connectivity.local_model_ready === false) {
        label += ' · 未就绪';
    }
    st.textContent = label;
}

/** 点击切换：本地首次需要确认启动；切回 DeepSeek 时顺手卸载本地模型释放显存 */
async function setProvider(p) {
    if (p !== 'deepseek' && p !== 'local') return;
    if (p === currentProvider) return;
    if (p === 'local') {
        if (!localApproved) {
            requestLocalApproval();
            return;
        }
        const ok = await startLocalModel();
        if (ok) applyProvider('local');
    } else {
        // 切回 DeepSeek：卸载本地模型（尽力而为，失败不影响切换）
        const invoke = getInvoke();
        if (invoke) { invoke('local_stop').catch(() => {}); }
        applyProvider('deepseek');
    }
}

/** 本地模型启动确认弹窗（每次登录后第一次选「本地」时弹出） */
function requestLocalApproval() {
    const modal = document.getElementById('local-modal');
    if (!modal) return;
    modal.hidden = false;
}

function hideLocalModal() {
    const modal = document.getElementById('local-modal');
    if (modal) modal.hidden = true;
}

async function startLocalModel() {
    const invoke = getInvoke();
    if (!invoke) { appendMessage('[错误] Tauri API 未注入', 'system'); return false; }
    try {
        const data = await invoke('local_start');
        if (data && data.ok) {
            localApproved = true;
            appendMessage('本地模型已启动：qwen3-vl:4b（断网可用，工具+看图）', 'system');
            scrollToBottom();
            refreshConnectivity();
            return true;
        }
        appendMessage(`[错误] ${data && data.error ? data.error : '本地模型启动失败'}`, 'system');
        return false;
    } catch (err) {
        appendMessage(`[错误] 本地模型启动失败：${err}`, 'system');
        return false;
    }
}

/** 确保本地视觉模型就绪（发图必需：DeepSeek 无视觉，图片走本地 qwen3-vl）。
 * 已就绪时秒回；未就绪时自动启动 Ollama+模型并预热（首次约 20-40s）。 */
async function ensureLocalReady() {
    const invoke = getInvoke();
    if (!invoke) { appendMessage('[错误] Tauri API 未注入', 'system'); return false; }
    try {
        const data = await invoke('local_start');
        if (data && data.ok) return true;
        appendMessage(`[错误] ${data && data.error ? data.error : '本地视觉模型启动失败'}`, 'system');
        return false;
    } catch (err) {
        appendMessage(`[错误] 本地视觉模型启动失败：${err}`, 'system');
        return false;
    }
}

// ============ 技能选择器（类似 codex 的 @ 技能：自选 skill 命令 agent 执行）============
let skillGroups = [];

async function loadSkills() {
    const invoke = getInvoke();
    if (!invoke) return;
    try {
        const data = await invoke('get_skills');
        if (data && Array.isArray(data.groups)) {
            skillGroups = data.groups;
            renderSkillMenu();
        }
    } catch (_) { /* 忽略，按钮无列表时不弹 */ }
}

function renderSkillMenu() {
    const menu = document.getElementById('skill-menu');
    if (!menu) return;
    if (!skillGroups.length) {
        menu.innerHTML = '<div class="skill-menu-empty">暂无技能</div>';
        return;
    }
    const html = skillGroups.map(g => `
        <div class="skill-group">
            <div class="skill-group-name">${g.name}</div>
            ${g.tools.map(t => `<button class="skill-item" data-name="${t.name}">
                <b>${t.name}</b><span>${t.description || ''}</span>
            </button>`).join('')}
        </div>`).join('');
    menu.innerHTML = html;
    menu.querySelectorAll('.skill-item').forEach(btn => {
        btn.addEventListener('click', () => {
            const name = btn.dataset.name;
            insertSkillIntoInput(name);
            hideSkillMenu();
        });
    });
}

function toggleSkillMenu() {
    const menu = document.getElementById('skill-menu');
    if (!menu) return;
    if (menu.hidden) {
        if (!skillGroups.length) loadSkills();
        menu.hidden = false;
    } else {
        menu.hidden = true;
    }
}

function hideSkillMenu() {
    const menu = document.getElementById('skill-menu');
    if (menu) menu.hidden = true;
}

/** 选中技能 → 在输入框插入命令前缀，用户补全任务后发送 */
function insertSkillIntoInput(name) {
    const input = document.getElementById('user-input');
    if (!input) return;
    const prefix = `使用「${name}」技能：`;
    if (input.value.trim() && !input.value.includes(`「${name}」`)) {
        input.value = prefix + input.value;
    } else if (!input.value.trim()) {
        input.value = prefix;
    }
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
}

/** 启动连通性检测（DeepSeek 可达 / Ollama / 本地模型就绪） */
async function refreshConnectivity() {
    const invoke = getInvoke();
    if (!invoke) return;
    try {
        connectivity = await invoke('check_connectivity');
    } catch (_) {
        connectivity = null;
    }
    updateHeaderStatus();
}

// ============ 初始化 ============
document.addEventListener('DOMContentLoaded', async () => {
    // 本地模式需每次登录显式确认才启动：启动时一律回落 DeepSeek，
    // 只有点击「本地」按钮才弹确认框（不弹则本地零占用）
    if (currentProvider === 'local') {
        currentProvider = 'deepseek';
        localStorage.setItem('xiaoman_provider', 'deepseek');
    }
    bindEvents();
    await waitForServer();
    if (serverConnected) {
        await refreshConnectivity();
        await loadHistory();
        await loadSkills();
        initActivePush();
    }
});

// ============ 主动触发推送（阶段A1）============
// 后端 /ws 推送主动消息 → 主窗口插入一条小满主动消息（AI 气泡）
let activeWS = null;
let activeWSPort = null;
let activeWSRetry = null;

function initActivePush() {
    const invoke = getInvoke();
    if (!invoke) { connectActiveWS('18789'); return; }
    invoke('get_app_info')
        .then(info => connectActiveWS((info && info.agent_port) || '18789'))
        .catch(() => connectActiveWS('18789'));
}

function connectActiveWS(port) {
    activeWSPort = port;
    if (activeWSRetry) { clearTimeout(activeWSRetry); activeWSRetry = null; }
    try {
        activeWS = new WebSocket(`ws://127.0.0.1:${port}/ws`);
    } catch (e) { scheduleActiveWSRetry(); return; }
    activeWS.onmessage = (ev) => {
        try {
            const data = JSON.parse(ev.data);
            if (data && data.type === 'active' && data.text) {
                appendMessage(data.text, 'ai');
                scrollToBottom();
            }
        } catch (e) { /* 忽略坏帧 */ }
    };
    activeWS.onclose = () => scheduleActiveWSRetry();
    activeWS.onerror = () => { try { activeWS.close(); } catch (e) {} };
}

function scheduleActiveWSRetry() {
    if (activeWSRetry) return;
    activeWSRetry = setTimeout(() => {
        activeWSRetry = null;
        if (activeWSPort) connectActiveWS(activeWSPort);
    }, 3000);
}

// ============ 事件绑定 ============
function bindEvents() {
    sendBtn.addEventListener('click', () => sendMessage());

    const btnSkill = document.getElementById('btn-skill');
    if (btnSkill) btnSkill.addEventListener('click', (e) => { e.stopPropagation(); toggleSkillMenu(); });
    document.addEventListener('click', () => hideSkillMenu());
    const skillPicker = document.getElementById('skill-picker');
    if (skillPicker) skillPicker.addEventListener('click', (e) => e.stopPropagation());

    const toggle = document.getElementById('provider-toggle');
    if (toggle) {
        toggle.querySelectorAll('.provider-btn').forEach(btn => {
            btn.addEventListener('click', () => setProvider(btn.dataset.provider));
        });
        // 初始化选中态
        toggle.querySelectorAll('.provider-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.provider === currentProvider);
        });
    }
    // 本地模型确认弹窗按钮
    const btnConfirm = document.getElementById('btn-local-confirm');
    const btnCancel = document.getElementById('btn-local-cancel');
    if (btnConfirm) {
        btnConfirm.addEventListener('click', async () => {
            hideLocalModal();
            const ok = await startLocalModel();
            if (ok) applyProvider('local');
        });
    }
    if (btnCancel) {
        btnCancel.addEventListener('click', () => {
            hideLocalModal();
            applyProvider('deepseek');
            appendMessage('已取消：本次未启动本地模型，继续使用 DeepSeek', 'system');
            scrollToBottom();
        });
    }

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

    // 图片附件：选择文件 / 移除
    btnAttach?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', (e) => {
        const file = e.target.files && e.target.files[0];
        if (file) readFileAsAttachment(file);
        e.target.value = '';
    });
    attachRemove?.addEventListener('click', clearAttachment);

    // 粘贴图片（剪贴板）
    userInput.addEventListener('paste', (e) => {
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        for (const it of items) {
            if (it.type && it.type.startsWith('image/')) {
                const file = it.getAsFile();
                if (file) { readFileAsAttachment(file); e.preventDefault(); return; }
            }
        }
    });
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
                serverConnected = true;
                serverStatusDot.className = 'dot dot-green';
                setCharStatus('在线', '');
                loadingOverlay.classList.add('hidden');
                console.log('[Agent] Server ready');
                updateHeaderStatus();
                return;
            }
        } catch { /* 还没起来 */ }
        serverStatusDot.className = 'dot dot-gray';
        await sleep(500);
    }
    // 连不上：不显示「在线」
    serverConnected = false;
    serverStatusDot.className = 'dot dot-red';
    setCharStatus('离线', 'var(--error)');
    updateHeaderStatus();
    loadingOverlay.querySelector('p').textContent =
        '无法连接到 Agent 服务，请检查 Python 环境';
}

// ============ 聊天核心（Tauri IPC） ============

// ============ 图片附件工具 ============
function readFileAsAttachment(file) {
    if (!file || !file.type.startsWith('image/')) {
        appendMessage('[提示] 目前只支持选择图片文件（多模态模型就绪后开放其它附件）', 'system');
        return;
    }
    if (file.size > 15 * 1024 * 1024) {
        appendMessage('[提示] 图片超过 15MB，请压缩后再试', 'system');
        return;
    }
    const reader = new FileReader();
    reader.onload = () => {
        const dataUrl = String(reader.result);
        compressImage(dataUrl, file.type)
            .then(compressed => {
                pendingAttachment = { name: file.name, dataUrl: compressed.dataUrl, mime: compressed.mime };
                renderAttachment();
            })
            .catch(() => {
                // 压缩失败（如非标准图）则原样使用
                pendingAttachment = { name: file.name, dataUrl, mime: file.type };
                renderAttachment();
            });
    };
    reader.onerror = () => appendMessage('[错误] 读取图片失败', 'system');
    reader.readAsDataURL(file);
}

/** 发送前降采样压缩图片：最大边 1280px，JPEG 0.85 / PNG 保留，减小体积与视觉 token。 */
function compressImage(dataUrl, mime) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => {
            try {
                const MAX = 1280;
                let { width, height } = img;
                const scale = Math.min(1, MAX / Math.max(width, height));
                const w = Math.max(1, Math.round(width * scale));
                const h = Math.max(1, Math.round(height * scale));
                const canvas = document.createElement('canvas');
                canvas.width = w;
                canvas.height = h;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, w, h);
                const isPng = (mime || '').includes('png') || (mime || '').includes('webp');
                const outMime = isPng ? 'image/png' : 'image/jpeg';
                const outDataUrl = canvas.toDataURL(outMime, isPng ? undefined : 0.85);
                resolve({ dataUrl: outDataUrl, mime: outMime });
            } catch (e) { reject(e); }
        };
        img.onerror = reject;
        img.src = dataUrl;
    });
}

function renderAttachment() {
    if (!pendingAttachment) { attachmentPreview.hidden = true; return; }
    attachThumb.src = pendingAttachment.dataUrl;
    attachName.textContent = pendingAttachment.name || '图片';
    attachmentPreview.hidden = false;
}

function clearAttachment() {
    pendingAttachment = null;
    attachmentPreview.hidden = true;
    attachThumb.removeAttribute('src');
}

/**
 * 发送用户消息并接收 AI 回复
 */
async function sendMessage() {
    const text = userInput.value.trim();
    if ((!text && !pendingAttachment) || isReplying) return;

    userInput.value = '';
    userInput.style.height = 'auto';

    appendMessage(text || '（图片）', 'user');
    setReplyingState(true);
    notifyPetState('press');

    try {
        const invoke = getInvoke();
        if (!invoke) { appendMessage('[错误] Tauri API 未注入', 'system'); return; }
        // DeepSeek 模式发图：图片必须走本地视觉模型（DeepSeek 无视觉），
        // 自动确保本地模型就绪，无需手动切到本地模式
        const hasImage = !!(pendingAttachment && pendingAttachment.dataUrl);
        if (hasImage && currentProvider !== 'local') {
            appendMessage('图片由本地视觉模型识别，正在准备本地模型…', 'system');
            scrollToBottom();
            const ready = await ensureLocalReady();
            if (!ready) {
                setReplyingState(false);
                notifyPetState('idle');
                return;   // 保留附件，用户可重试或切本地
            }
        }
        const invokeArgs = { message: text || '看看这张图片', provider: currentProvider };
        if (hasImage) {
            invokeArgs.image_base64 = pendingAttachment.dataUrl;
        }
        const data = await invoke('send_chat', invokeArgs);
        clearAttachment();

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
    setCharStatus(replying ? '思考中...' : (serverConnected ? '在线' : '离线'),
                  replying ? '#c9a456' : (serverConnected ? 'var(--success)' : 'var(--error)'));
    // 头部状态同步（替换标题“对话”的位置）
    const headerStatus = document.getElementById('header-status');
    if (headerStatus) {
        headerStatus.textContent = replying ? '思考中...' : providerLabel(currentProvider);
        headerStatus.style.background = replying ? '#c9a456' : '';
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


// ============ 档案卡管理（记忆 UI · v3 板块化） ============
let memoryData = null;
let editKey = null;                       // null=新增模式；非空=编辑该 key
let catOpen = { user: true, agent: true, pref: true, rule: true, schedule: true };

const CATEGORY_NAMES = {
    user: '用户身份', agent: 'Agent设定', pref: '用户偏好',
    rule: '行为规定', schedule: '主动触发',
};

document.addEventListener('DOMContentLoaded', () => {
    const btnMemory = document.getElementById('btn-memory');
    const btnBack = document.getElementById('btn-memory-back');
    const btnAdd = document.getElementById('btn-memory-add');
    const btnAddOk = document.getElementById('btn-add-confirm');
    const btnAddCancel = document.getElementById('btn-add-cancel');

    document.getElementById('memory-view').hidden = true;

    btnMemory?.addEventListener('click', openMemoryView);
    btnBack?.addEventListener('click', closeMemoryView);
    btnAdd?.addEventListener('click', openAddForm);
    btnAddOk?.addEventListener('click', saveMemoryItem);
    btnAddCancel?.addEventListener('click', hideMemoryForm);
});

function openMemoryView() {
    document.getElementById('memory-view').hidden = false;
    loadMemoryItems();
}

function closeMemoryView() {
    document.getElementById('memory-view').hidden = true;
}

// ---------- 表单控制 ----------
function openAddForm() {
    editKey = null;
    document.getElementById('add-key').disabled = false;
    document.getElementById('add-key').value = '';
    document.getElementById('add-value').value = '';
    document.getElementById('add-confidence').value = '';
    document.getElementById('memory-edit-banner').hidden = true;
    document.getElementById('memory-add-form').hidden = false;
    document.getElementById('add-value').focus();
}

function openEditForm(item) {
    editKey = item.key;
    document.getElementById('add-key').disabled = true;
    document.getElementById('add-key').value = item.key;
    document.getElementById('add-value').value = item.value || '';
    document.getElementById('add-type').value = item.type || 'fact';
    document.getElementById('add-category').value =
        (item.category && CATEGORY_NAMES[item.category]) ? item.category : 'user';
    document.getElementById('add-confidence').value = item.confidence ?? '';
    document.getElementById('edit-key-label').textContent = item.key;
    document.getElementById('memory-edit-banner').hidden = false;
    document.getElementById('memory-add-form').hidden = false;
    document.getElementById('add-value').focus();
}

function hideMemoryForm() {
    document.getElementById('memory-add-form').hidden = true;
    document.getElementById('memory-edit-banner').hidden = true;
}

// ---------- 加载与渲染 ----------
async function loadMemoryItems() {
    const invoke = getInvoke();
    if (!invoke) { showFatal('Tauri API 未注入'); return; }
    const body = document.getElementById('memory-body');
    const prevScroll = body ? body.scrollTop : 0;
    try {
        memoryData = await invoke('get_profile_items');
        renderMemoryItems();
    } catch (err) {
        console.error('[Memory] 加载档案卡失败:', err);
        const cat = document.getElementById('memory-categories');
        cat.innerHTML = '<div class="memory-empty">加载失败：' + err + '</div>';
    }
    if (body) body.scrollTop = prevScroll;   // 删除/切换后不跳回顶部
}

function renderMemoryItems() {
    const container = document.getElementById('memory-categories');
    container.innerHTML = '';
    if (!memoryData || !Array.isArray(memoryData.categories)) return;

    memoryData.categories.forEach(cat => {
        const items = cat.items || [];
        const section = document.createElement('details');
        section.className = 'memory-section';
        section.open = catOpen[cat.id] !== false;

        const summary = document.createElement('summary');
        summary.className = 'memory-section-title';
        const badge = document.createElement('span');
        badge.className = 'memory-count';
        badge.textContent = items.length ? ` ${items.length} 条` : ' 空';
        summary.appendChild(document.createTextNode(cat.name));
        summary.appendChild(badge);
        const desc = document.createElement('span');
        desc.className = 'memory-section-desc';
        desc.textContent = cat.desc || '';
        summary.appendChild(desc);
        section.appendChild(summary);

        const list = document.createElement('div');
        list.className = 'memory-list';
        if (items.length) {
            renderMemoryList(list, items);
        } else {
            const empty = document.createElement('div');
            empty.className = 'memory-empty';
            empty.textContent = '暂无内容，点右上角「＋ 新建」添加';
            list.appendChild(empty);
        }
        section.appendChild(list);
        section.addEventListener('toggle', () => { catOpen[cat.id] = section.open; });
        container.appendChild(section);
    });
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
        const t = item.type === 'preference' ? '偏好' : (item.type === 'role' ? '角色' : '事实');
        metaEl.textContent = `${t} · conf ${item.confidence ?? '-'} · ${(item.updated_at || '').slice(0, 16)}`;
        info.append(keyEl, valEl, metaEl);

        const actions = document.createElement('div');
        actions.className = 'memory-item-actions';
        const toggle = document.createElement('button');
        toggle.className = 'toggle-btn ' + (item.active === false ? 'off' : 'on');
        toggle.textContent = item.active === false ? '× 停用' : '√ 生效';
        toggle.addEventListener('click', () => toggleItem(item.key, item.active === false));
        const edit = document.createElement('button');
        edit.className = 'edit-btn';
        edit.textContent = '修改';
        edit.title = '编辑内容/板块（不用删除重建）';
        edit.addEventListener('click', () => openEditForm(item));
        const del = document.createElement('button');
        del.className = 'del-btn';
        del.textContent = '删除';
        del.addEventListener('click', () => deleteItem(item.key));
        actions.append(toggle, edit, del);

        row.append(info, actions);
        container.appendChild(row);
    });
}

// ---------- 操作 ----------
async function toggleItem(key, active) {
    const invoke = getInvoke();
    if (!invoke) return;
    try {
        await invoke('profile_toggle', { key, active });
        await loadMemoryItems();
    } catch (err) { console.error('[Memory] 切换失败:', err); }
}

async function deleteItem(key) {
    if (!confirm(`确定删除「${key}」？删除会先存档保留（discarded），可恢复。`)) return;
    const invoke = getInvoke();
    if (!invoke) return;
    try {
        await invoke('profile_delete', { key });
        await loadMemoryItems();
    } catch (err) { console.error('[Memory] 删除失败:', err); }
}

async function saveMemoryItem() {
    const invoke = getInvoke();
    if (!invoke) return;
    const key = document.getElementById('add-key').value.trim();
    const value = document.getElementById('add-value').value.trim();
    const type = document.getElementById('add-type').value;
    const category = document.getElementById('add-category').value;
    const confRaw = document.getElementById('add-confidence').value.trim();
    if (!key || !value) { alert('key 和内容都不能为空'); return; }
    const confidence = confRaw ? Math.min(1, Math.max(0, parseFloat(confRaw) || 0.9)) : 0.9;
    try {
        if (editKey) {
            await invoke('profile_update', { key, value, category, confidence });
        } else {
            await invoke('profile_add', { key, value, factType: type, confidence, category });
        }
        hideMemoryForm();
        await loadMemoryItems();
    } catch (err) {
        console.error('[Memory] 保存失败:', err);
        alert('保存失败：' + err);
    }
}
