#![windows_subsystem = "windows"]

//! Agent Desktop — Tauri v2 Rust 后端
//!
//! 职责：
//!   1. 窗口管理：主窗口 ↔ 桌宠悬浮窗切换
//!   2. Python sidecar：启动 agent-server.py（本地 HTTP API）
//!   3. Tauri Commands：前端通过 invoke() 调用

mod commands;
mod pet_manager;

use tauri::Manager;
use commands::AppState;
use pet_manager::PetState;
use tokio::process::Child;

/// 诊断标记文件目录：一律写系统临时目录，绝不写项目目录（可移植性）。
fn diag_dir() -> std::path::PathBuf {
    std::env::temp_dir().join("agent-desktop")
}

// [诊断] 前端 JS 一加载就调用，写临时文件标记，确认页面是否真正渲染
#[tauri::command]
fn mark_boot() {
    let t = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let dir = diag_dir();
    let _ = std::fs::create_dir_all(&dir);
    let _ = std::fs::write(dir.join("boot.txt"), format!("BOOT epoch={t}\n"));
}

// ===================== Tauri 主入口 =====================

/// 在 WebView 初始化前，定位系统已有的 WebView2 运行时并设置环境变量。
/// 本机无独立 Evergreen Runtime，但 Windows 自带 EdgeWebView。设置
/// WEBVIEW2_BROWSER_EXECUTABLE_FOLDER 让 webview2-com 用上它，避免黑屏。
/// 候选路径基于环境变量（PROGRAMFILES(X86) / ProgramW6432 / SystemRoot）推导，
/// 不再硬编码 C:\ 盘符，换机器也能用。
fn setup_webview2_env() {
    let pf86 = std::env::var_os("PROGRAMFILES(X86)")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from(r"C:\Program Files (x86)"));
    let pf = std::env::var_os("ProgramW6432")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from(r"C:\Program Files"));
    let sysroot = std::env::var_os("SystemRoot")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from(r"C:\Windows"));

    let candidates = [
        pf86.join(r"Microsoft\EdgeWebView\Application"),
        sysroot.join(r"System32\Microsoft-Edge-WebView"),
        pf86.join(r"Microsoft\Edge Core"),
        pf.join(r"Microsoft\EdgeWebView\Application"),
    ];
    let dir = diag_dir();
    let _ = std::fs::create_dir_all(&dir);
    let diag_path = dir.join("webview2_diag.txt");
    for base in candidates {
        let base_p = std::path::Path::new(&base);
        if !base_p.exists() { continue; }
        // 优先用含 msedgewebview2.exe 的版本子目录
        if let Ok(entries) = std::fs::read_dir(base_p) {
            let mut dirs: Vec<_> = entries
                .filter_map(|e| e.ok())
                .filter(|e| e.path().is_dir())
                .collect();
            dirs.sort_by_key(|e| {
                std::fs::metadata(&e.path())
                    .and_then(|m| m.modified())
                    .ok()
                    .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
            });
            // 先查基础目录本身，再查子目录
            let mut check_paths = vec![base_p.to_path_buf()];
            check_paths.extend(dirs.into_iter().map(|e| e.path()));
            for p in check_paths {
                if p.join("msedgewebview2.exe").exists() {
                    std::env::set_var("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", &p);
                    println!("[Tauri] WebView2 运行时: {:?}", p);
                    let _ = std::fs::write(&diag_path, format!("OK folder={}\n", p.display()));
                    return;
                }
            }
        }
    }
    println!("[Tauri] 未找到系统 WebView2 运行时（可能需要安装）");
    let _ = std::fs::write(&diag_path, "NOT_FOUND\n");
}

fn main() {
    setup_webview2_env();
    run();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(AppState {
            python_process: tokio::sync::Mutex::new(None::<Child>),
            pet_state: tokio::sync::Mutex::new(PetState::Idle),
        })
        .setup(|app| {
            // 异步启动 Python agent-server
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(std::time::Duration::from_millis(800)).await;
                if let Err(e) = commands::start_python_server(handle).await {
                    eprintln!("[Tauri] Python server start failed: {e}");
                }
            });

            // 主窗口点 X / Alt+F4 → 直接退出应用（不再切桌宠；桌宠仅由"桌宠模式"按钮唤起）
            if let Some(main) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                let main_hide = main.clone();
                main.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = main_hide.hide();   // 立即隐藏窗口，消除退出延迟感
                        let h = handle.clone();
                        tauri::async_runtime::spawn(async move {
                            commands::exit_app(h).await.ok();
                        });
                    }
                });
            }

            // 桌宠窗口关闭（Alt+F4 / 系统关闭）→ 走优雅退出（finalize + 退出应用）
            if let Some(pet) = app.get_webview_window("pet") {
                let handle = app.handle().clone();
                pet.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let h = handle.clone();
                        tauri::async_runtime::spawn(async move {
                            commands::exit_app(h).await.ok();
                        });
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::start_python_server,
            commands::stop_python_server,
            commands::check_server_health,
            commands::check_connectivity,
            commands::local_status,
            commands::local_start,
            commands::local_stop,
            commands::send_chat,
            commands::get_history,
            commands::reset_chat_session,
            commands::get_profile_items,
            commands::profile_toggle,
            commands::profile_delete,
            commands::profile_add,
            commands::set_pet_state,
            commands::get_pet_state,
            commands::switch_to_pet_mode,
            commands::switch_to_main_window,
            commands::show_pet,
            commands::hide_pet,
            commands::set_pet_position,
            commands::get_app_info,
            commands::exit_app,
            mark_boot,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
