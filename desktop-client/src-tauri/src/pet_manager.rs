//! 桌宠状态管理器
//!
//! 管理桌宠的 6 种交互状态及对应素材切换逻辑。
//! 前端通过 CSS class 切换实现状态动画，此模块提供状态定义，
//! 并通过 Tauri command（set_pet_state / get_pet_state）与前端打通：
//!   - 前端 invoke('set_pet_state', {state: 'response_done'}) → Rust 更新状态
//!   - Rust 向所有窗口 emit 'pet://state-changed' → 桌宠前端监听并切换精灵图

use serde::{Deserialize, Serialize};

/// 桌宠交互状态枚举
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PetState {
    /// 待机 — 默认状态，安静坐着
    Idle = 0,
    /// 睡觉 — 闭眼靠在枕头上
    Sleep = 1,
    /// 按住 — 被点击/触摸中
    Press = 2,
    /// 按住左滑 — 左滑互动
    SwipeLeft = 3,
    /// 按住右滑 — 右滑互动
    SwipeRight = 4,
    /// 对话输出完成 — 回复完毕，开心表情
    ResponseDone = 5,
}

#[allow(dead_code)]  // COUNT/css_class/from_context 为前端状态机做文档化映射，Rust 侧未直接使用
impl PetState {
    /// 总状态数（用于前端 sprite 计算）
    pub const COUNT: usize = 6;

    /// 获取状态对应的 CSS class 名
    pub fn css_class(&self) -> &'static str {
        match self {
            Self::Idle => "pet-idle",
            Self::Sleep => "pet-sleep",
            Self::Press => "pet-press",
            Self::SwipeLeft => "pet-swipe-left",
            Self::SwipeRight => "pet-swipe-right",
            Self::ResponseDone => "pet-response-done",
        }
    }

    /// 根据场景自动选择状态
    pub fn from_context(is_replying: bool, is_idle_long: bool) -> Self {
        if is_replying {
            Self::Press  // 回复中：按住/思考状态
        } else if is_idle_long {
            Self::Sleep  // 长时间无操作：睡觉
        } else {
            Self::Idle  // 默认待机
        }
    }
}
