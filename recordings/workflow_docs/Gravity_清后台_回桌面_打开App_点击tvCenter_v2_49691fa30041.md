# Gravity_清后台_回桌面_打开App_点击tvCenter_v2

修正版：set_options 格式正确；find_and_tap 的 verify 使用 verify 支持的 find_on_screen（避免 unsupported_verify_type）。

```script
# 工作流: Gravity_清后台_回桌面_打开App_点击tvCenter_v2

---

1. 设置选项 fail_fast=True, retry_max_attempts=3, retry_delay_s=0.6

2. 清空后台进程

3. 回到桌面

4. 确保启动应用 "Gravity"
   - 等待 1.5秒

5. 等待页面 ""

6. 关闭弹窗
   - 最多 2 轮

7. 等待出现 "anonymous.sns.community.gravity:id/tvCenter"
   - 超时: 10秒

8. 点击文字 "anonymous.sns.community.gravity:id/tvCenter"

9. 截图 "after_tap_tvCenter"

---
# 完成
```