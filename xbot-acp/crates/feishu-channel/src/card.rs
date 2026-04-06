//! 交互卡片处理

use serde_json::json;
use acp_client::PermissionDecision;

/// 权限审批卡片
pub fn permission_card(permission_id: &str, description: &str, options: &[String]) -> serde_json::Value {
    let buttons: Vec<serde_json::Value> = options
        .iter()
        .enumerate()
        .map(|(i, opt)| {
            json!({
                "tag": "button",
                "text": { "tag": "plain_text", "content": opt },
                "type": if i == 0 { "primary" } else { "default" },
                "value": { "action": format!("perm:{}:{}", permission_id, i) },
            })
        })
        .collect();

    json!({
        "config": { "wide_screen_mode": true },
        "elements": [
            {
                "tag": "div",
                "text": { "tag": "lark_md", "content": format!("**{}**\n\n{}", "权限审批", description) },
            },
            {
                "tag": "action",
                "actions": buttons,
            }
        ],
    })
}

/// 流式消息卡片
pub fn stream_card(content: &str) -> serde_json::Value {
    json!({
        "config": { "wide_screen_mode": true },
        "elements": [
            {
                "tag": "div",
                "text": { "tag": "lark_md", "content": content },
            }
        ],
    })
}

/// 解析卡片回调
pub fn parse_card_callback(value: &str) -> Option<(String, usize)> {
    let parts: Vec<&str> = value.split(':').collect();
    if parts.len() == 3 && parts[0] == "perm" {
        let permission_id = parts[1].to_string();
        let option_index: usize = parts[2].parse().ok()?;
        return Some((permission_id, option_index));
    }
    None
}

/// 将选项索引转换为决策
pub fn option_index_to_decision(index: usize, _total: usize) -> PermissionDecision {
    match index {
        0 => PermissionDecision::Allow,
        1 => PermissionDecision::Deny,
        _ => PermissionDecision::AllowAll,
    }
}