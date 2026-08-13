//! Tauri IPC Commands — 前端通过 invoke('command_name', args) 调用
//!
//! 进程管理：使用 tokio::process::Child + tokio::sync::Mutex（满足 Send）

use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager, State};
use tokio::process::Child;
use tokio::sync::Mutex as TokioMutex;

use crate::pet_manager::PetState;

// ===================== 全局状态 =====================

pub struct AppState {
    pub python_process: TokioMutex<Option<Child>>,
    pub pet_state: TokioMutex<PetState>,
}

/// 把 Python 子进程绑定到"关闭即杀"的 Windows Job Object：
/// 应用进程退出（含被任务管理器强杀/崩溃）时，系统关闭其所有句柄 → Job 关闭 →
/// KILL_ON_JOB_CLOSE 自动带走 Python，杜绝残留进程。尽力而为（失败不影响主流程）。
#[cfg(target_os = "windows")]
fn bind_job_kill_on_close(child: &tokio::process::Child) {
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return;
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const std::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        let Some(handle) = child.raw_handle() else { return };
        let _ = AssignProcessToJobObject(job, handle as HANDLE);
        // 故意不 CloseHandle(job)：把句柄保留到进程生命周期末尾，
        // 应用退出时系统关闭所有句柄 → Job 关闭 → Python 被强杀。
    }
}

/// 后端端口：读 AGENT_PORT 环境变量，默认 18789。所有请求统一走这里，避免端口硬编码。
fn server_url(path: &str) -> String {
    let port = std::env::var("AGENT_PORT").unwrap_or_else(|_| "18789".to_string());
    format!("http://127.0.0.1:{port}{path}")
}

// ===================== Python Server 管理 =====================

/// 启动 agent-server.py 子进程
#[tauri::command]
pub async fn start_python_server(app: AppHandle) -> Result<(), String> {
    let state: State<AppState> = app.state();
    let mut proc = state.python_process.lock().await;

    if proc.is_some() {
        return Ok(());
    }

    let python_exe = find_python().ok_or("找不到 Python 解释器（可用环境变量 AGENT_PYTHON 指定路径）")?;

    let resource_dir = app.path().resource_dir().map_err(|e| format!("资源目录: {e}"))?;
    let server_script = find_server_script(&resource_dir)
        .ok_or_else(|| "agent-server.py 不存在（已搜索资源目录/可执行文件目录向上 5 级）".to_string())?;

    println!("[Tauri] 启动 Python: {:?} {:?}", python_exe, server_script);

    let mut cmd = tokio::process::Command::new(&python_exe);
    cmd.arg(&server_script)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());

    // Windows：隐藏 Python 子进程的控制台窗口（不弹黑框）
    #[cfg(target_os = "windows")]
    {
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let child = cmd.spawn()
        .map_err(|e| format!("启动 Python 失败: {e}"))?;

    // 绑定"关闭即杀"Job Object，确保应用退出时 Python 不留残留
    #[cfg(target_os = "windows")]
    bind_job_kill_on_close(&child);

    *proc = Some(child);

    for i in 0..30u8 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        match reqwest::get(server_url("/health")).await {
            Ok(resp) if resp.status().is_success() => {
                println!("[Tauri] Agent Server 就绪 ({}次尝试)", i + 1);
                return Ok(());
            }
            _ => continue,
        }
    }

    Err("Agent Server 启动超时".to_string())
}

/// 停止 Python 子进程（仅停止后端，不触发 finalize；退出请用 exit_app）
#[tauri::command]
pub async fn stop_python_server(state: State<'_, AppState>) -> Result<(), String> {
    let mut proc = state.python_process.lock().await;
    if let Some(mut child) = proc.take() {
        child.kill().await.map_err(|e| format!("停止失败: {e}"))?;
    }
    Ok(())
}

/// 健康检查
#[tauri::command]
pub async fn check_server_health() -> Result<serde_json::Value, String> {
    println!("[Tauri] 前端 health ping 收到（证明 WebView 已渲染 + Tauri API 可用）");
    match reqwest::get(server_url("/health")).await {
        Ok(resp) => resp.json::<serde_json::Value>().await.map_err(|e| e.to_string()),
        Err(_) => Err("server_unreachable".to_string()),
    }
}

/// 启动连通性检测（DeepSeek 可达 + Ollama + 本地模型就绪）→ /health/full
#[tauri::command]
pub async fn check_connectivity() -> Result<serde_json::Value, String> {
    match reqwest::get(server_url("/health/full")).await {
        Ok(resp) => resp.json::<serde_json::Value>().await.map_err(|e| e.to_string()),
        Err(_) => Err("server_unreachable".to_string()),
    }
}

/// 技能列表 → GET /skills（前端「技能」按钮自选 skill 命令 agent 执行）
#[tauri::command]
pub async fn get_skills() -> Result<serde_json::Value, String> {
    match reqwest::get(server_url("/skills")).await {
        Ok(resp) => resp.json::<serde_json::Value>().await.map_err(|e| e.to_string()),
        Err(_) => Err("server_unreachable".to_string()),
    }
}

/// 本地模型状态 → /local/status
#[tauri::command]
pub async fn local_status() -> Result<serde_json::Value, String> {
    match reqwest::get(server_url("/local/status")).await {
        Ok(resp) => resp.json::<serde_json::Value>().await.map_err(|e| e.to_string()),
        Err(_) => Err("server_unreachable".to_string()),
    }
}

/// 按需启动本地模型（Ollama + 校验模型）→ POST /local/start
#[tauri::command]
pub async fn local_start() -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(server_url("/local/start"))
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    if resp.status().is_success() {
        resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

/// 卸载本地模型（释放显存）→ POST /local/stop
#[tauri::command]
pub async fn local_stop() -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(server_url("/local/stop"))
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    if resp.status().is_success() {
        resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

// ===================== 聊天功能 =====================

#[tauri::command]
pub async fn send_chat(
    message: String,
    image_base64: Option<String>,
    provider: Option<String>,
) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let mut body = serde_json::json!({"message": message});
    if let Some(b64) = image_base64 {
        if !b64.trim().is_empty() {
            body["image_base64"] = serde_json::Value::String(b64);
        }
    }
    // provider: "deepseek" | "local"（前端切换按钮），透传给后端路由
    if let Some(p) = provider {
        if !p.trim().is_empty() {
            body["provider"] = serde_json::Value::String(p);
        }
    }
    let resp = client
        .post(server_url("/chat"))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;

    if resp.status().is_success() {
        resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
    } else {
        // 非 2xx：尽量把服务端的真实错误信息透传回前端（如模型报错/请求过大）
        let status = resp.status();
        match resp.json::<serde_json::Value>().await {
            Ok(v) => Err(v.get("error").and_then(|e| e.as_str())
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| format!("HTTP {}", status))),
            Err(_) => Err(format!("HTTP {}", status)),
        }
    }
}

/// 获取聊天历史
#[tauri::command]
pub async fn get_history() -> Result<serde_json::Value, String> {
    match reqwest::get(server_url("/history")).await {
        Ok(resp) => resp.json::<serde_json::Value>().await.map_err(|e| e.to_string()),
        Err(_) => Err("server_unreachable".to_string()),
    }
}

/// 重置对话
#[tauri::command]
pub async fn reset_chat_session() -> Result<(), String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(server_url("/reset"))
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    if resp.status().is_success() {
        Ok(())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

// ===================== 档案卡管理（客户端记忆 UI） =====================

/// 获取档案卡全部事实/偏好（含 active 生效开关）
#[tauri::command]
pub async fn get_profile_items() -> Result<serde_json::Value, String> {
    match reqwest::get(server_url("/profile/items")).await {
        Ok(resp) => resp.json::<serde_json::Value>().await.map_err(|e| e.to_string()),
        Err(_) => Err("server_unreachable".to_string()),
    }
}

/// 生效/停用一条档案事实
#[tauri::command]
pub async fn profile_toggle(key: String, active: bool) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(server_url("/profile/toggle"))
        .json(&serde_json::json!({"key": key, "active": active}))
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    if resp.status().is_success() {
        resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

/// 删除一条档案事实（后端先记入 discarded 审计）
#[tauri::command]
pub async fn profile_delete(key: String) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(server_url("/profile/delete"))
        .json(&serde_json::json!({"key": key}))
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    if resp.status().is_success() {
        resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

/// 新增一条自定义事实/偏好
#[tauri::command]
pub async fn profile_add(key: String, value: String, fact_type: String,
                         confidence: f64, category: String) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(server_url("/profile/add"))
        .json(&serde_json::json!({"key": key, "value": value,
                                  "type": fact_type, "confidence": confidence,
                                  "category": category}))
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    if resp.status().is_success() {
        resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

/// 编辑一条档案项（改内容/板块/置信度）
#[tauri::command]
pub async fn profile_update(key: String, value: Option<String>,
                            category: Option<String>,
                            confidence: Option<f64>) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let mut body = serde_json::json!({"key": key});
    if let Some(v) = value { body["value"] = serde_json::Value::String(v); }
    if let Some(c) = category { body["category"] = serde_json::Value::String(c); }
    if let Some(cf) = confidence { body["confidence"] = serde_json::Value::from(cf); }
    let resp = client
        .post(server_url("/profile/update"))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    if resp.status().is_success() {
        resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

// ===================== 待审批记忆写入（勾选才落盘） =====================

/// 待审批记忆写入提案列表
#[tauri::command]
pub async fn profile_pending() -> Result<serde_json::Value, String> {
    reqwest::get(server_url("/profile/pending"))
        .await
        .map_err(|e| format!("请求失败: {e}"))?
        .json::<serde_json::Value>()
        .await
        .map_err(|e| e.to_string())
}

/// 勾选生效：把选中的记忆提案写入档案卡
#[tauri::command]
pub async fn profile_pending_apply(ids: Vec<String>) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(server_url("/profile/pending/apply"))
        .json(&serde_json::json!({"ids": ids}))
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    if resp.status().is_success() {
        resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

/// 放弃：从待审批列表移除选中的提案（或全部）
#[tauri::command]
pub async fn profile_pending_discard(ids: Vec<String>, all: Option<bool>) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let mut body = serde_json::json!({"ids": ids});
    if let Some(a) = all {
        body["all"] = serde_json::Value::Bool(a);
    }
    let resp = client
        .post(server_url("/profile/pending/discard"))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    if resp.status().is_success() {
        resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

// ===================== 桌宠状态（pet_manager 接线） =====================

/// 设置桌宠状态（前端 invoke），变更时广播事件，桌宠前端监听后切换精灵图。
#[tauri::command]
pub async fn set_pet_state(state: PetState, app: AppHandle) -> Result<(), String> {
    let st: State<AppState> = app.state();
    let mut cur = st.pet_state.lock().await;
    if *cur != state {
        *cur = state;
        println!("[Tauri] PetState -> {:?}", state);
        let _ = app.emit("pet://state-changed", state);
    }
    Ok(())
}

/// 读取当前桌宠状态（前端查询用）。
#[tauri::command]
pub async fn get_pet_state(app: AppHandle) -> Result<PetState, String> {
    let st: State<AppState> = app.state();
    let cur = *st.pet_state.lock().await;
    Ok(cur)
}

// ===================== 窗口管理 =====================

#[tauri::command]
pub async fn switch_to_pet_mode(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("main") { w.hide().map_err(|e| e.to_string())?; }
    if let Some(w) = app.get_webview_window("pet") {
        w.show().map_err(|e| e.to_string())?;
        w.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub async fn switch_to_main_window(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("pet") { w.hide().map_err(|e| e.to_string())?; }
    if let Some(w) = app.get_webview_window("main") {
        w.show().map_err(|e| e.to_string())?;
        w.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub async fn show_pet(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("pet") { w.show().map_err(|e| e.to_string())?; }
    Ok(())
}

#[tauri::command]
pub async fn hide_pet(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("pet") { w.hide().map_err(|e| e.to_string())?; }
    Ok(())
}

#[tauri::command]
pub async fn set_pet_position(x: f64, y: f64, app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("pet") {
        w.set_position(tauri::Position::Physical(
            tauri::PhysicalPosition { x: x as i32, y: y as i32 },
        )).map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub async fn get_app_info() -> serde_json::Value {
    let port = std::env::var("AGENT_PORT").unwrap_or_else(|_| "18789".to_string());
    serde_json::json!({
        "version": env!("CARGO_PKG_VERSION"),
        "name": env!("CARGO_PKG_NAME"),
        "agent_port": port,
    })
}

/// 优雅退出：先触发后端 finalize（归档 + 离线抽取档案卡），等 Python 自行退出，
/// 超时兜底强杀，最后退出 Tauri 应用。与终端模式 AGENT.py 的 finally 行为对齐。
async fn graceful_shutdown(app: &AppHandle) {
    let client = reqwest::Client::new();

    // 1) 先确认后端可达（避免后端未启动时白白等待 40s）
    let reachable = client
        .get(server_url("/health"))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false);

    let state: State<AppState> = app.state();
    let mut proc = state.python_process.lock().await;

    if reachable {
        // 1.5) 退出前卸载本地模型（释放显存；finalize 里也会兜底再卸一次）
        let _ = client
            .post(server_url("/local/stop"))
            .timeout(Duration::from_secs(6))
            .send()
            .await;

        // 2) 通知后端 finalize（后端并发抽取+摘要、短超时；完成后自行停止事件循环并退出）
        let _ = client
            .post(server_url("/finalize"))
            .timeout(Duration::from_secs(20))
            .send()
            .await;

        // 3) 等待 Python 进程自行退出（finalize 完成即 stop；上限 20s，到点强杀）
        let exited = match proc.as_mut() {
            Some(child) => {
                tokio::time::timeout(Duration::from_secs(20), child.wait()).await.is_ok()
            }
            None => true,
        };
        if exited {
            proc.take();
            return;
        }
    }

    // 4) 后端不可达 / 超时未退出 → 兜底强杀
    if let Some(mut child) = proc.take() {
        let _ = child.kill().await;
    }
}

/// 退出整个应用（从桌宠模式调用）：finalize → 回收 Python → 退出 Tauri。
#[tauri::command]
pub async fn exit_app(app: AppHandle) -> Result<(), String> {
    graceful_shutdown(&app).await;
    app.exit(0);
    Ok(())
}

// ===================== 工具函数 =====================

/// 从资源目录 / 可执行文件目录向上回溯查找 agent-server.py，
/// 兼容 dev（src-tauri）、裸 exe（target/debug）、打包后（resources）等布局。
fn find_server_script(resource_dir: &std::path::Path) -> Option<std::path::PathBuf> {
    // 1) 根目录 exe：.../AGENT/agent-desktop.exe → desktop-client/agent-server.py
    if let Ok(exe) = std::env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            let direct = exe_dir.join("desktop-client/agent-server.py");
            if direct.exists() {
                return Some(direct);
            }
        }
    }

    // 2) dev 布局：.../desktop-client/src-tauri/target/debug/agent-desktop.exe
    //    向上 3 级到项目根，再进 desktop-client
    if let Ok(exe) = std::env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            let direct = exe_dir.join("../../../desktop-client/agent-server.py");
            if direct.exists() {
                return Some(direct);
            }
        }
    }

    let mut search_roots: Vec<std::path::PathBuf> = vec![resource_dir.to_path_buf()];
    if let Ok(exe) = std::env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            search_roots.push(exe_dir.to_path_buf());
        }
    }

    for root in &search_roots {
        let mut cur = root.clone();
        for _ in 0..5 {
            let cand = cur.join("agent-server.py");
            if cand.exists() {
                return Some(cand);
            }
            // 根目录 exe：也查一下当前目录下的 desktop-client/ 子目录
            let sub = cur.join("desktop-client/agent-server.py");
            if sub.exists() {
                return Some(sub);
            }
            if !cur.pop() {
                break;
            }
        }
    }
    None
}

/// 判断某个解释器命令名 / 路径是否可运行。
fn is_runnable(c: &str) -> bool {
    std::process::Command::new(c)
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// 查找 Python 解释器，优先级：
///   1) 环境变量 AGENT_PYTHON（路径或命令名，显式指定）
///   2) PATH 常用名：python / py / python3
///   3) 常见安装目录 glob（userprofile/.workbuddy、LOCALAPPDATA/Programs/Python、ProgramW6432/Python）
/// 不再硬编码任何用户特定绝对路径。
fn find_python() -> Option<std::path::PathBuf> {
    // 1) 显式指定
    if let Ok(p) = std::env::var("AGENT_PYTHON") {
        let p = p.trim().to_string();
        if !p.is_empty() && is_runnable(&p) {
            return Some(std::path::PathBuf::from(p));
        }
    }

    // 2) PATH 常用命令名
    for name in ["python", "py", "python3"] {
        if is_runnable(name) {
            return Some(std::path::PathBuf::from(name));
        }
    }

    // 3) 常见安装目录 glob
    let mut roots: Vec<std::path::PathBuf> = Vec::new();
    if let Some(home) = std::env::var_os("USERPROFILE") {
        roots.push(std::path::Path::new(&home).join(r".workbuddy\binaries\python\versions"));
    }
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        roots.push(std::path::Path::new(&local).join(r"Programs\Python"));
    }
    if let Some(pf) = std::env::var_os("ProgramW6432") {
        roots.push(std::path::Path::new(&pf).join("Python"));
    }
    for root in &roots {
        if !root.is_dir() {
            continue;
        }
        let mut entries: Vec<_> = std::fs::read_dir(root)
            .map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.path()).collect())
            .unwrap_or_default();
        entries.sort();
        for dir in entries {
            if !dir.is_dir() {
                continue;
            }
            let cand = dir.join("python.exe");
            if cand.is_file() {
                return Some(cand);
            }
        }
    }

    // 4) 最兜底：交给 OS 解析
    if is_runnable("python") {
        Some(std::path::PathBuf::from("python"))
    } else {
        None
    }
}

// ===================== 微信桥（ClawBot / iLink）=====================
// 与 wechat_bridge/ 联动：扫码登录 / 后台启动 / 开机自启 / 状态查询。
// 桥本体是独立 Node 进程（weixin-agent-sdk），这里只负责拉起与管理。

const WX_PUSH_PORT: u16 = 18888;

/// 项目根目录（AGENT/）：向上回溯，找到含 desktop-client/agent-server.py 的目录。
fn find_project_root() -> Option<std::path::PathBuf> {
    if let Ok(exe) = std::env::current_exe() {
        let mut cur = exe.parent().map(|p| p.to_path_buf());
        for _ in 0..6 {
            if let Some(dir) = cur.clone() {
                if dir.join("desktop-client").join("agent-server.py").exists()
                    || dir.join("wechat_bridge").join("start.vbs").exists()
                {
                    return Some(dir);
                }
                cur = dir.parent().map(|p| p.to_path_buf());
            }
        }
    }
    None
}

fn tcp_port_open(port: u16) -> bool {
    use std::net::{SocketAddr, TcpStream};
    TcpStream::connect_timeout(&SocketAddr::from(([127, 0, 0, 1], port)), Duration::from_millis(800)).is_ok()
}

/// 微信登录态：~/.openclaw/openclaw-weixin/accounts.json 非空数组即视为已登录。
fn wx_logged_in() -> bool {
    let dir = std::env::var("OPENCLAW_STATE_DIR")
        .or_else(|_| std::env::var("CLAWDBOT_STATE_DIR"))
        .unwrap_or_else(|_| std::env::var("USERPROFILE").unwrap_or_default() + "\\.openclaw");
    let path = std::path::Path::new(&dir).join("openclaw-weixin").join("accounts.json");
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<Vec<String>>(&s).ok())
        .map(|v| !v.is_empty())
        .unwrap_or(false)
}

// ---- 开机自启：HKCU Run 键（REG_SZ，UTF-16，中文路径无编码问题）----
const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
const RUN_VALUE: &str = "XiaomanWechatBridge";

fn run_value_command() -> Option<String> {
    let vbs = find_project_root()?.join("wechat_bridge").join("start.vbs");
    if !vbs.exists() { return None; }
    Some(format!("wscript.exe \"{}\"", vbs.to_string_lossy()))
}

#[cfg(target_os = "windows")]
fn run_key_enabled() -> bool {
    use windows_sys::Win32::System::Registry::{HKEY, RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY_CURRENT_USER, KEY_QUERY_VALUE};
    unsafe {
        let path: Vec<u16> = RUN_KEY.encode_utf16().chain(std::iter::once(0)).collect();
        let name: Vec<u16> = RUN_VALUE.encode_utf16().chain(std::iter::once(0)).collect();
        let mut key: HKEY = std::ptr::null_mut();
        if RegOpenKeyExW(HKEY_CURRENT_USER, path.as_ptr(), 0, KEY_QUERY_VALUE, &mut key) != 0 {
            return false;
        }
        let mut buf = [0u8; 2048];
        let mut size = buf.len() as u32;
        let ret = RegQueryValueExW(key, name.as_ptr(), std::ptr::null(), std::ptr::null_mut(), buf.as_mut_ptr(), &mut size);
        RegCloseKey(key);
        ret == 0
    }
}

#[cfg(not(target_os = "windows"))]
fn run_key_enabled() -> bool { false }

#[cfg(target_os = "windows")]
fn set_run_key(enabled: bool) -> bool {
    use windows_sys::Win32::System::Registry::{
        HKEY, RegCloseKey, RegCreateKeyExW, RegDeleteValueW, RegSetValueExW,
        HKEY_CURRENT_USER, KEY_SET_VALUE, REG_SZ,
    };
    let Some(cmd) = run_value_command() else { return false };
    unsafe {
        let path: Vec<u16> = RUN_KEY.encode_utf16().chain(std::iter::once(0)).collect();
        let name: Vec<u16> = RUN_VALUE.encode_utf16().chain(std::iter::once(0)).collect();
        let mut key: HKEY = std::ptr::null_mut();
        if RegCreateKeyExW(
            HKEY_CURRENT_USER, path.as_ptr(), 0, std::ptr::null(), 0,
            KEY_SET_VALUE, std::ptr::null(), &mut key, std::ptr::null_mut(),
        ) != 0 {
            return false;
        }
        let ret = if enabled {
            let val: Vec<u16> = cmd.encode_utf16().chain(std::iter::once(0)).collect();
            RegSetValueExW(key, name.as_ptr(), 0, REG_SZ, val.as_ptr() as *const u8, (val.len() * 2) as u32)
        } else {
            RegDeleteValueW(key, name.as_ptr())
        };
        RegCloseKey(key);
        ret == 0
    }
}

#[cfg(not(target_os = "windows"))]
fn set_run_key(_enabled: bool) -> bool { false }

/// 微信桥状态：桥是否在线 / 是否已登录 / 开机自启是否已绑定
#[tauri::command]
pub fn wechat_status() -> Result<serde_json::Value, String> {
    let root = find_project_root().ok_or("找不到项目根目录")?;
    Ok(serde_json::json!({
        "bridge_running": tcp_port_open(WX_PUSH_PORT),
        "logged_in": wx_logged_in(),
        "startup_enabled": run_key_enabled(),
        "push_port": WX_PUSH_PORT,
        "bridge_dir": root.join("wechat_bridge").to_string_lossy(),
    }))
}

/// 微信扫码登录：弹出新控制台窗口运行 login.bat（显示二维码，手机扫码确认）
#[tauri::command]
pub fn wechat_login() -> Result<serde_json::Value, String> {
    let root = find_project_root().ok_or("找不到项目根目录")?;
    let dir = root.join("wechat_bridge");
    if !dir.join("login.bat").exists() {
        return Err("wechat_bridge/login.bat 不存在".to_string());
    }
    let mut cmd = std::process::Command::new("cmd.exe");
    cmd.arg("/c").arg("login.bat").current_dir(&dir);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x00000010); // CREATE_NEW_CONSOLE：新窗口显示二维码
    }
    cmd.spawn().map_err(|e| format!("启动登录窗口失败: {e}"))?;
    Ok(serde_json::json!({"ok": true}))
}

/// 启动微信桥（隐藏后台），并绑定开机自启；等桥上线后返回状态
#[tauri::command]
pub fn wechat_start() -> Result<serde_json::Value, String> {
    let root = find_project_root().ok_or("找不到项目根目录")?;
    let vbs = root.join("wechat_bridge").join("start.vbs");
    if !vbs.exists() {
        return Err("wechat_bridge/start.vbs 不存在".to_string());
    }
    if !tcp_port_open(WX_PUSH_PORT) {
        let mut cmd = std::process::Command::new("wscript.exe");
        cmd.arg("//nologo").arg(&vbs);
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW：完全隐藏
        }
        cmd.spawn().map_err(|e| format!("启动微信桥失败: {e}"))?;
        for _ in 0..24 {
            if tcp_port_open(WX_PUSH_PORT) { break; }
            std::thread::sleep(Duration::from_millis(500));
        }
    }
    let running = tcp_port_open(WX_PUSH_PORT);
    let startup = set_run_key(true); // 按需绑定开机自启（幂等）
    Ok(serde_json::json!({
        "ok": true,
        "bridge_running": running,
        "startup_enabled": startup,
    }))
}

/// 开关微信桥开机自启（HKCU Run 键）
#[tauri::command]
pub fn wechat_autostart(enabled: bool) -> Result<serde_json::Value, String> {
    Ok(serde_json::json!({ "ok": true, "startup_enabled": set_run_key(enabled) }))
}

/// 把本地生成文件（图片/视频）读成 data URL，供前端 <img>/<video> 直接渲染。
/// 由 Agnes 生图/生视频工具生成在 generated/ 下；文件过大（>40MB）拒绝，避免内存撑爆。
#[tauri::command]
pub fn read_media_as_data_url(path: String) -> Result<String, String> {
    use std::io::Read;
    let p = std::path::PathBuf::from(&path);
    if !p.is_file() {
        return Err(format!("文件不存在: {path}"));
    }
    let mut f = std::fs::File::open(&p).map_err(|e| format!("打开文件失败: {e}"))?;
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).map_err(|e| format!("读取文件失败: {e}"))?;
    if buf.is_empty() {
        return Err("文件为空".to_string());
    }
    if buf.len() > 40 * 1024 * 1024 {
        return Err("文件过大（>40MB），无法在聊天里直接展示".to_string());
    }
    let ext = p.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase();
    let mime = match ext.as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "bmp" => "image/bmp",
        "mp4" => "video/mp4",
        "webm" => "video/webm",
        "mov" => "video/quicktime",
        "mkv" => "video/x-matroska",
        _ => "application/octet-stream",
    };
    use base64::Engine as _;
    let b64 = base64::engine::general_purpose::STANDARD.encode(&buf);
    Ok(format!("data:{mime};base64,{b64}"))
}
