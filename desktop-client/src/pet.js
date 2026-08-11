/**
 * Agent Desktop — 桌宠悬浮窗逻辑
 *
 * 参照 Codex Pet 模式：
 *   - 全局悬浮（alwaysOnTop + transparent）
 *   - 6 种状态可视化（精灵图 background-position 切换）
 *   - 可拖拽、可点击唤出主窗口
 *   - 聊天气泡（AI 回复完成时弹出摘要）
 *   - 始终可见的控制栏（返回主窗口 / 退出）
 */

// ============ 配置 ============
const SPRITE_URL = 'assets/states/idle.png';

// 6 状态名（对应 pet.css 的 background-position class）
const STATES = {
    IDLE:           'state-idle',
    SLEEP:          'state-sleep',
    PRESS:          'state-press',
    SWIPE_LEFT:     'state-swipe-left',
    SWIPE_RIGHT:    'state-swipe-right',
    RESPONSE_DONE:  'state-response-done',
};

// ============ 安全的 Tauri API 访问 ============
function getInvoke() {
    if (window.__TAURI__ && window.__TAURI__.core) {
        return window.__TAURI__.core.invoke;
    }
    return null;
}

function getCurrentWindow() {
    try {
        if (window.__TAURI__ && window.__TAURI__.window) {
            return window.__TAURI__.window.getCurrentWindow();
        }
    } catch { /* no-op */ }
    return null;
}

// ============ DOM 引用 ============
const petSprite   = document.getElementById('pet-sprite');
const petContainer = document.getElementById('pet-container');
const chatBubble   = document.getElementById('chat-bubble');
const bubbleText   = document.getElementById('bubble-text');
const bubbleClose  = document.getElementById('bubble-close');
const clickHint    = document.getElementById('click-hint');
const btnBackMain  = document.getElementById('btn-back-to-main');
const btnClosePet  = document.getElementById('btn-close-pet');
const btnBigger    = document.getElementById('btn-pet-bigger');
const btnSmaller   = document.getElementById('btn-pet-smaller');

// 桌宠大小调节（参考 Codex Pet：可缩放悬浮角色）
const PET_BASE = { width: 230, height: 280 };      // 窗口基准尺寸（收紧，贴近角色）
const SPRITE_BASE = { width: 180, height: 240 };   // 角色基准尺寸
const PET_SIZE_MIN = 0.6;
const PET_SIZE_MAX = 1.6;
let petSize = parseFloat(localStorage.getItem('pet-size') || '1') || 1;

// Rust 侧 PetState（serde snake_case）→ 前端 CSS class
const RUST_TO_STATE = {
    idle: 'state-idle',
    sleep: 'state-sleep',
    press: 'state-press',
    swipe_left: 'state-swipe-left',
    swipe_right: 'state-swipe-right',
    response_done: 'state-response-done',
};

// ============ 状态变量 ============
let currentState = STATES.IDLE;
let idleTimer = null;
let bubbleHideTimer = null;
let dragState = null;   // 拖拽中的手势状态：press / swipe-left / swipe-right

const IDLE_SLEEP_DELAY = 5000;      // 5秒无操作 → 睡觉
const BUBBLE_AUTO_HIDE = 8000;      // 气泡显示 8 秒后自动消失

// ============ 初始化 ============
document.addEventListener('DOMContentLoaded', () => {
    initControls();
    initDrag();
    initBubbleClose();
    initPetStateEvents();
    applyPetSize();
    resetIdleTimer();
    setState(STATES.IDLE);

    // 预加载精灵图
    const img = new Image();
    img.src = SPRITE_URL;

    // 5 秒后隐藏点击提示
    setTimeout(() => clickHint?.classList.add('hidden'), 5000);

    console.log('[Pet] Initialized, sprite:', SPRITE_URL);
});

// ============ 控制按钮 ============
function initControls() {
    // 返回主窗口
    btnBackMain?.addEventListener('click', (e) => {
        e.stopPropagation();
        restoreMainWindow();
    });

    // 关闭桌宠（退出应用）
    btnClosePet?.addEventListener('click', (e) => {
        e.stopPropagation();
        closeApp();
    });

    // 放大 / 缩小桌宠
    btnBigger?.addEventListener('click', (e) => {
        e.stopPropagation();
        petSize = Math.min(PET_SIZE_MAX, petSize + 0.1);
        applyPetSize();
    });
    btnSmaller?.addEventListener('click', (e) => {
        e.stopPropagation();
        petSize = Math.max(PET_SIZE_MIN, petSize - 0.1);
        applyPetSize();
    });
}

// 应用桌宠大小：调整窗口尺寸 + 角色缩放（含点击提示/气泡位置自适应）
async function applyPetSize() {
    petSize = Math.max(PET_SIZE_MIN, Math.min(PET_SIZE_MAX, petSize));
    localStorage.setItem('pet-size', String(petSize));

    // 角色按比例缩放
    petSprite.style.width = Math.round(SPRITE_BASE.width * petSize) + 'px';
    petSprite.style.height = Math.round(SPRITE_BASE.height * petSize) + 'px';

    // 窗口同步缩放
    const win = getCurrentWindow();
    if (win) {
        try {
            const w = Math.round(PET_BASE.width * petSize);
            const h = Math.round(PET_BASE.height * petSize);
            const LogicalSize = window.__TAURI__?.window?.LogicalSize;
            if (LogicalSize) {
                await win.setSize(new LogicalSize(w, h));
            } else {
                await win.setSize({ width: w, height: h });
            }
        } catch (e) {
            console.warn('[Pet] setSize 失败:', e);
        }
    }
    console.log(`[Pet] size → ${petSize.toFixed(1)}`);
}

async function restoreMainWindow() {
    const invoke = getInvoke();
    if (invoke) {
        await invoke('switch_to_main_window');
        // 切换后给一个互动反馈
        setState(STATES.SWIPE_RIGHT);
        setTimeout(() => setState(STATES.IDLE), 600);
    }
}

async function closeApp() {
    const invoke = getInvoke();
    // 优先用 Tauri 命令退出（停止 Python + 退出应用）
    if (invoke) {
        await invoke('exit_app').catch(() => {});
    }
    // 兜底：关闭当前窗口（若主窗口已隐藏，可能触发退出）
    const win = getCurrentWindow();
    if (win) {
        await win.close().catch(() => {});
    }
}

// ============ Rust 状态事件（pet_manager 接线）============
async function initPetStateEvents() {
    try {
        if (!window.__TAURI__ || !window.__TAURI__.event) return;
        const { listen } = window.__TAURI__.event;
        await listen('pet://state-changed', (e) => {
            const cls = RUST_TO_STATE[e.payload];
            // 带优先级：按住/滑动时，回复完成等低优先级状态不会覆盖
            if (cls && setState(cls)) {
                resetIdleTimer();
            }
        });
        console.log('[Pet] Listening pet://state-changed');
    } catch (err) {
        console.warn('[Pet] Event listen 不可用:', err);
    }
}

// ============ 状态切换核心（带优先级） ============
// 优先级：按住/左滑/右滑(用户交互) > 回复完成 > 待机/睡觉
//  - 用户交互（点击按住、左右拖拽）无条件覆盖一切
//  - 回复完成(ResponseDone) 只覆盖待机与睡觉，不覆盖按住/左滑/右滑
const STATE_PRIORITY = {
    [STATES.IDLE]: 0,
    [STATES.SLEEP]: 0,
    [STATES.RESPONSE_DONE]: 1,
    [STATES.PRESS]: 2,
    [STATES.SWIPE_LEFT]: 2,
    [STATES.SWIPE_RIGHT]: 2,
};

function _applyState(stateClass) {
    Object.values(STATES).forEach(s => petSprite.classList.remove(s));
    petSprite.classList.add(stateClass);
    petSprite.classList.add('pet-switching');
    setTimeout(() => petSprite.classList.remove('pet-switching'), 350);
    currentState = stateClass;

    // 回复完成 3 秒后回待机
    if (stateClass === STATES.RESPONSE_DONE) {
        setTimeout(() => {
            if (currentState === STATES.RESPONSE_DONE) {
                forceState(STATES.IDLE);
                resetIdleTimer();
            }
        }, 3000);
    }
    console.log(`[Pet] State → ${stateClass}`);
}

// 带优先级切换：低优先级不能覆盖高优先级（如按住/拖拽时忽略回复完成）
function setState(stateClass) {
    if (stateClass === currentState) return false;
    if (STATE_PRIORITY[stateClass] < STATE_PRIORITY[currentState]) {
        return false;
    }
    _applyState(stateClass);
    return true;
}

// 无条件切换：用户交互（点击按住、拖拽手势）与内部复位专用
function forceState(stateClass) {
    if (stateClass === currentState) return false;
    _applyState(stateClass);
    return true;
}

function showBubble(text) {
    bubbleText.textContent = text;
    chatBubble.classList.add('visible');

    clearTimeout(bubbleHideTimer);
    bubbleHideTimer = setTimeout(hideBubble, BUBBLE_AUTO_HIDE);

    setState(STATES.PRESS);
}

function hideBubble() {
    chatBubble.classList.remove('visible');
}

// ============ 拖拽功能 ============
function initDrag() {
    let isDragging = false;
    let startX, startY, initialX, initialY;
    let dragStartTime = 0;
    let lastMouseX = 0;    // 上一次 mousemove 的鼠标 X（方向判定用）
    let swipeAccum = 0;    // 方向累计：纯跟随鼠标移动方向，不受图标跟手延迟影响

    petContainer.addEventListener('mousedown', (e) => {
        // 控制按钮区域不触发拖拽
        if (e.target.closest('.pet-controls') ||
            e.target.closest('.chat-bubble') ||
            e.target.closest('.bubble-close')) return;

        isDragging = true;
        dragStartTime = Date.now();
        startX = e.screenX;
        startY = e.screenY;
        lastMouseX = e.screenX;
        swipeAccum = 0;

        getCurrentPosition().then(pos => {
            initialX = pos.x;
            initialY = pos.y;
        }).catch(() => {});

        // 透明框内点击/按住 → 显示"按住"状态图（睡觉也会被唤醒，优先级最高）
        dragState = STATES.PRESS;
        forceState(STATES.PRESS);
        resetIdleTimer();
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isDragging) return;

        const dx = e.screenX - startX;
        const dy = e.screenY - startY;
        const newX = Math.round(initialX + dx);
        const newY = Math.round(initialY + dy);

        const invoke = getInvoke();
        if (invoke) {
            invoke('set_pet_position', { x: newX, y: newY }).catch(() => {});
        }

        // 长按 + 左右移动 → 对应手势状态图（无条件覆盖，包括回复完成）
        // 方向判定：纯看鼠标移动方向（实时累计），反向移动会自动切回并重新累计
        const stepX = e.screenX - lastMouseX;
        lastMouseX = e.screenX;
        if (swipeAccum !== 0 && stepX !== 0 && Math.sign(stepX) !== Math.sign(swipeAccum)
            && Math.abs(swipeAccum) >= 14) {
            swipeAccum = 0;   // 反向：重置累计，让状态跟随最新方向
        }
        swipeAccum += stepX;
        if (Math.abs(swipeAccum) >= 14) {
            const st = swipeAccum < 0 ? STATES.SWIPE_LEFT : STATES.SWIPE_RIGHT;
            if (st !== dragState) {
                dragState = st;
                forceState(st);
            }
        }
    });

    document.addEventListener('mouseup', (e) => {
        if (!isDragging) return;
        isDragging = false;
        dragState = null;
        swipeAccum = 0;

        const elapsed = Date.now() - dragStartTime;
        const dist = Math.hypot(e.screenX - startX, e.screenY - startY);

        // < 200ms 且移动 < 8px → 算作点击（唤出主窗口）
        if (elapsed < 200 && dist < 8) {
            restoreMainWindow();
        } else {
            // 交互结束恢复待机
            forceState(STATES.IDLE);
            resetIdleTimer();
        }
    });

    // 睡觉状态下，鼠标悬停到桌宠上即唤醒为待机
    petContainer.addEventListener('mouseenter', () => {
        if (currentState === STATES.SLEEP) {
            forceState(STATES.IDLE);
            resetIdleTimer();
        }
    });
}

// ============ 点击唤出主窗口（备用：双击）=====
let lastClickTime = 0;
petContainer.addEventListener('dblclick', (e) => {
    if (e.target.closest('.pet-controls') || e.target.closest('.chat-bubble')) return;
    restoreMainWindow();
});

// ============ 气泡关闭按钮 ============
function initBubbleClose() {
    bubbleClose?.addEventListener('click', (e) => {
        e.stopPropagation();
        hideBubble();
    });
}

// ============ 空闲管理 ============
// 只在"桌宠交互"时重置：5 秒无操作（无点击/拖动/悬停唤醒）→ 待机转睡觉
function resetIdleTimer() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
        // 仅从待机进入睡觉（按住/滑动/回复完成期间不被打断）
        if (currentState === STATES.IDLE) {
            setState(STATES.SLEEP);
        }
    }, IDLE_SLEEP_DELAY);
}

// ============ 工具函数 ============
async function getCurrentPosition() {
    const win = getCurrentWindow();
    if (win) {
        const pos = await win.outerPosition().catch(() => ({ x: 100, y: 100 }));
        return { x: pos.x, y: pos.y };
    }
    return { x: 100, y: 100 };
}
